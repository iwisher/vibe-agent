# Multi-Agent Swarm Orchestration (v0.3.5) - Architecture Review

## 1. Message Bus Design Patterns
**Critique:** The proposed design uses an `asyncio.Queue` per agent under the `MessageBus`. This implies a point-to-point (P2P) messaging pattern directly managed by the orchestrator. While simple, P2P tightens coupling and makes broadcasting (e.g., announcing a global state change) or dynamic agent discovery difficult.
**Improvements:**
- Implement a true **Publish/Subscribe (Pub/Sub) Event Router** instead of direct queues. Agents should subscribe to specific topics or `MessageType`s.
- Add an `EventBroker` to decouple producers from consumers, allowing a `ResearchAgent` to broadcast findings (`ANSWER`/`RESULT`) without knowing if a `CriticAgent` or `Orchestrator` is listening.
- Include a **Dead Letter Queue (DLQ)** for messages that fail to process, aiding in resilience and debugging.

## 2. Sub-Agent Isolation vs. Shared State
**Critique:** Making the `SharedWiki` strictly read-only for sub-agents effectively prevents race conditions and state corruption, but it cripples the usefulness of agents whose primary role is to aggregate knowledge (like the `ResearchAgent`). If agents cannot write findings back, the orchestrator becomes a massive bottleneck for state synthesis.
**Improvements:**
- Adopt a **Write-via-Message** pattern. The `SharedWiki` should have a single authoritative owner (e.g., the Orchestrator or a dedicated `MemoryAgent`). Sub-agents send `UPDATE_WIKI` messages to the bus, which are processed sequentially by the owner, ensuring data integrity without locking issues.
- Provide sub-agents with an isolated, temporary "scratchpad" state for local, intermediate reasoning before they publish final results to the shared wiki.

## 3. Task Decomposition Strategy
**Critique:** Relying purely on unstructured "LLM-based task decomposition" into a flat list of tasks often fails on complex requests where steps have strict dependencies.
**Improvements:**
- Implement a **Directed Acyclic Graph (DAG)** representation for task decomposition. The LLM must define tasks *and* their prerequisites.
- The Orchestrator should act as a DAG scheduler, emitting `TASK` messages to the bus only when a sub-task's prerequisites have been fulfilled.
- Include a robust **Error Recovery & Re-planning** mechanism. If the `CriticAgent` rejects an output, the orchestrator must know how to dynamically insert a correction node into the task graph.

## 4. Concurrency Safety
**Critique:** Running sub-agents in isolated `asyncio.Task` wrappers with max concurrency limits and a read-only shared state provides a solid safety baseline. However, agent isolation goes beyond memory—it includes API rate limits and token usage.
**Improvements:**
- Implement **Context Isolation and Truncation**. Parallel agents might generate massive outputs simultaneously; the orchestrator needs a mechanism to summarize/compact sub-agent responses before feeding them into the shared state or next agent to avoid context window explosion.
- Introduce **Rate-Limit Pooling** at the `ModelGateway` level across all sub-agents to prevent a swarm of parallel `ResearchAgents` from immediately triggering `429 Too Many Requests`.
- Define explicit agent lifecycles (e.g., `SPAWNED`, `ACTIVE`, `IDLE`, `TERMINATED`) to gracefully cancel pending `asyncio.Task` processes if the overall workflow errors out.

## 5. Testing Approach
**Critique:** Unit testing the message bus and orchestrator is a good start, but asynchronous multi-agent systems are notoriously non-deterministic.
**Improvements:**
- **Race Condition & Ordering Tests:** Explicitly write tests that intentionally jitter message delivery times to ensure the system handles out-of-order `RESULT` or `CRITIQUE` messages correctly.
- **Poison Pill Testing:** Inject malformed messages or simulated agent crashes into the message bus to verify the Orchestrator's fault-tolerance and error aggregation.
- **Deterministic Evaluation:** Create a harness mode where the LLM gateway returns fixed, deterministic responses for specific prompts to test the exact DAG scheduling and sub-agent hand-offs without LLM variance.
