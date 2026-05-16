# Plan: Multi-Agent Swarm Orchestration (v0.3.5)

## Overview
Enable Vibe Agent to spawn specialized sub-agents that collaborate on complex tasks via a message bus.

## Architecture
```
SwarmOrchestrator
├── AgentProtocol (message bus)
│   ├── AgentMessage (role, content, metadata)
│   └── MessageBus (asyncio.Queue per agent)
├── SubAgent (specialized QueryLoop wrapper)
│   ├── ResearchAgent (web search, knowledge gathering)
│   ├── CodingAgent (code generation, refactoring)
│   └── CriticAgent (review, validation, testing)
└── SharedWiki (read-only access for all agents)

Task Flow:
1. User submits complex task
2. SwarmOrchestrator decomposes into sub-tasks
3. Spawns sub-agents with roles + shared wiki
4. Sub-agents execute in parallel via message bus
5. Results aggregated and returned
```

## Components

### 1. AgentProtocol (`vibe/swarm/protocol.py`)
- `AgentMessage`: dataclass with role, content, timestamp, correlation_id
- `MessageBus`: asyncio.Queue wrapper with publish/subscribe patterns
- `MessageType` enum: TASK, RESULT, QUESTION, ANSWER, CRITIQUE, DONE

### 2. SwarmOrchestrator (`vibe/swarm/orchestrator.py`)
- `SwarmOrchestrator`: main coordinator
- `decompose_task()`: LLM-based task decomposition
- `spawn_agent(role, task, wiki)`: creates SubAgent with shared resources
- `aggregate_results()`: combines sub-agent outputs
- `run(task)`: full orchestration flow

### 3. SubAgent (`vibe/swarm/agent.py`)
- `SubAgent`: wraps QueryLoop with role-specific configuration
- `SubAgentRole` enum: RESEARCH, CODING, CRITIC, PLANNER
- Each role has specialized system prompt and tool set
- Read-only access to shared wiki

### 4. Shared Wiki (`vibe/swarm/shared_wiki.py`)
- `SharedWiki`: read-only wrapper around LLMWiki
- All sub-agents can read but not write to wiki
- Prevents state corruption from parallel agents

## API Design
```python
# Usage
orchestrator = SwarmOrchestrator(wiki=wiki, config=config)
result = await orchestrator.run("Build a REST API with auth")
# Returns SwarmResult with sub-agent outputs and final synthesis
```

## Security
- Sub-agents run in isolated asyncio tasks
- Shared wiki is read-only for sub-agents
- Message bus is internal (no network exposure)
- Max concurrency limit to prevent resource exhaustion

## Testing
- Unit tests for message bus (publish/subscribe/delivery)
- Unit tests for orchestrator (decomposition, aggregation)
- Integration tests for full swarm flow
- Mock LLM responses for deterministic testing

## Review Notes (Gemini CLI)
- Pub/Sub Event Router instead of point-to-point queues
- Write-via-Message pattern for wiki updates (single authoritative owner)
- DAG-based task decomposition with prerequisite tracking
- Rate-Limit Pooling at ModelGateway across all sub-agents
- Agent lifecycles: SPAWNED → ACTIVE → IDLE → TERMINATED
- Race condition tests with jittered delivery
- Poison pill testing for fault tolerance
- Deterministic LLM harness for scheduling tests

## Implementation Order
1. AgentProtocol (Pub/Sub message bus) + tests
2. SubAgent with role configs + scratchpad + tests
3. SharedWiki with Write-via-Message + tests
4. SwarmOrchestrator (DAG scheduler) + tests
5. Integration tests with deterministic LLM
6. Gemini CLI review
