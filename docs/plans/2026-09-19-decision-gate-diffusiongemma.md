# Plan v2.1: Fast Veto Gate for SmartApprover (Direct-Logit Readout & Qwen3.5 4B Grounding)

> **Review Status:** ✅ **APPROVED (v2.1, final)** — v1/v2 critiques and OpenJev direct-logit findings applied; final review passed with amendments F1–F4 (see [§5.8](#58-critique-adjudication-2026-09-19), [§5.9](#59-second-critique-adjudication--openjev-findings-2026-09-19), [§5.10](#510-final-review-v21-2026-09-19))  
> **Reviewed:** 2026-09-19 by Engineering Management; v2.1 refined + final review 2026-09-19; backend addendum (omlx) 2026-09-20 (see [§5.11](#511-backend-addendum-omlx-evaluation-2026-09-20))

Goal: add an optional **veto-only** fast pre-filter in front of the LLM-based
SmartApprover using the **direct-logit readout pattern** (proven by OpenJev/SemIf) on a local
**Qwen3.5 4B** model via an OpenAI-compatible endpoint (Ollama, llama-server). This cuts latency
on obviously-malicious tool calls to sub-100 ms and adds an independent detection layer —
**without** early-approving anything, requiring 50+ GB cloud GPU rentals, unmerged upstream forks,
or brittle JSON text generation. DiffusionGemma/Jev-class parallel-decision models are
demoted to exploratory future backends behind the same client interface (Phase 4).

## 0. Why v2 — the four verified flaws in v1

1. **Security (central flaw)**: SmartApprover is a veto gate — `coordinators.py:522-533`
   blocks only on `decision == "reject"`, everything else is allowed. Letting a small
   pre-filter short-circuit to APPROVE makes the weakest model the final authority: an
   attacker only needs to fool the gate, and the frontier LLM is never consulted. v2: the
   gate may only **early-reject** (confident HIGH/CRITICAL) or **escalate**. It can never
   approve. The "catch-rate ≥ baseline minus 2 pp" acceptance criterion is rejected — no
   security regression is tolerated in a fail-closed architecture.
2. **Runtime**: `SmartApprover.assess_tool_call` is synchronous (`smart_approver.py:112`),
   called synchronously from `_check_smart_approver` (`coordinators.py:513`). An async
   httpx client there raises `RuntimeError` from event-loop threads (`_resolve_sync`,
   `smart_approver.py:14-30`) and silently falls back to heuristics in unit tests
   (pytest-asyncio) and redteam oracles. v2: synchronous, connection-pooled `httpx.Client`.
3. **Feasibility**: DiffusionGemma cannot run on this M4/24 GiB via vLLM (no Metal
   backend; ~52 GB bf16 or Blackwell-only NVFP4; PR #57250 unmerged), and upstream mlx-lm
   rejects the checkpoint (mlx-lm#1391). Renting cloud GPUs to validate a "local"
   pre-filter violates the repo's simplicity rules. v2: the gate runs on the existing
   local Ollama stack — no new hardware, no unmerged code.
4. **Testing**: canned-probability mocks measure zero calibration; a new
   `s8_decision_gate` corpus would pollute the attack-surface taxonomy (surfaces are
   attack vectors; S4 `approval` already covers approvers); and `run_redteam.py` has no
   `--surface` flag (`scripts/run_redteam.py:124-138`). v2: reuse `s4_approval.yaml` with
   a real local model via a standalone eval script; mocks confined to unit tests.

## 1. Architecture (v2.1)

```
tool call ──► SecurityCoordinator.evaluate_tool_call
                 │  Layers 1–3 unchanged (patterns, file safety, human approval)
                 ▼ Layer 4 (coordinators.py:513)
             SmartApprover.assess_tool_call (smart_approver.py:112)
                 │  1. heuristics (unchanged): CRITICAL short-circuit, LOW+auto_mode
                 ▼  2. NEW: FastGateClient.check() — veto-only
                 ├── confident HIGH/CRITICAL ──► RiskAssessment(REJECT)  [no LLM call]
                 └── anything else / gate down / timeout / parse error
                                  ──► 3. existing _llm_risk_assessment (unchanged)
```

Serving: nothing new. The gate talks to the local Ollama endpoint already used by
`FlashLLMClient` (`vibe/memory/flash_client.py:42`, default `http://localhost:11434/v1`).

Design rules:

- **Veto-only**: no code path in which the gate produces APPROVE. Enforced by tests.
- **Fencing**: the gate prompt embeds tool args inside `UNTRUSTED_ARGS_BEGIN/END`
  (`smart_approver.py:47-50`) after `_munge_fence_markers` (:68-78) — same treatment as
  SmartApprover. Veto-only semantics neutralize injection asymmetry: fooling the gate
  toward "safe" merely causes escalation; toward "dangerous" costs one extra LLM call.
- **Fail-safe**: unreachable / timeout / parse error → escalate (baseline posture
  preserved). The gate never independently blocks on error — an optional component must
  not be able to DoS the agent.
- **Verdict format (Direct-Logit Readout over Candidate Tokens)**: As established by
  OpenJev/SemIf, generating multi-token JSON strings adds 200–500 ms of autoregressive
  decoding latency and produces uncalibrated, self-reported confidence values. Instead,
  the gate defaults to **direct single-token logit readout** (`max_tokens=1`, `logprobs=True`)
  over binary candidate tokens `A: Safe` vs `B: Dangerous`. The normalized softmax:
  $$P(\text{Dangerous}) = \frac{e^{\text{logit}(B)}}{e^{\text{logit}(A)} + e^{\text{logit}(B)}}$$
  yields a **conditional two-way score** in ~50–100 ms with zero syntax failures. Per
  SemIf's own disclaimer, this score is **not calibrated confidence** and excludes mass on
  answers outside {A, B}; the reject threshold is therefore set empirically in Phase 3,
  not assumed. Guards: escalate if either candidate token is absent from `top_logprobs`
  or the argmax token ∉ {A, B}; reasoning models must run with thinking disabled
  (`chat_template_kwargs: {"enable_thinking": false}`) so the first token is the answer.
  If the endpoint lacks logprob support (see the Phase 1 probe), the gate falls back to
  constrained JSON mode.

## 2. Implementation phases (v2.1)

### Phase 1 — FastGateClient (sync, pooled, direct-logit)
- New `vibe/tools/security/fast_gate.py`:
  - `@dataclass GateVerdict`: `reject: bool`, `risk_level: RiskLevel | None`,
    `confidence: float`, `reasoning: str`; all paths never raise.
  - `FastGateClient(base_url, model="qwen3.5:4b", timeout=0.5, reject_confidence=0.85, mode="auto")`:
    one persistent `httpx.Client` (connection pooling; NOT per-call construction as in
    `flash_client.py`).
  - **Capability probe at startup** (`mode="auto"`): Ollama's OpenAI-compatible layer does
    not reliably return logprobs (ollama/ollama#16117, #13638), while llama-server does.
    Send one probe request with `logprobs=True`; if `choices[0].logprobs` is present →
    `logit` mode, otherwise `json` mode (logged loudly). `llama-server` is the reference
    server for logit mode; Ollama's native `/api/generate` (which does expose logprobs)
    may be added later as an alternative transport.
  - In `logit` mode: queries `{base_url}/chat/completions` with `logprobs=True,
    top_logprobs=5, max_tokens=1` and thinking disabled. Extracts logits for `A` and `B`,
    calculates normalized $P(\text{Dangerous})$. Escalates if either candidate is missing
    from `top_logprobs` or the argmax token ∉ {A, B} — the two-way softmax hides
    "neither" mass. If $P(\text{Dangerous}) \ge \text{reject\_confidence}$, returns
    `GateVerdict(reject=True, risk_level=RiskLevel.CRITICAL, confidence=P,
    reasoning="Direct-logit risk threshold crossed")`.
  - In `json` mode (fallback): queries JSON output and parses via `_strip_json_fence`.
  - Cached `available()` probe (sync mirror of `flash_client.py:62-80`).
  - Prompt built with UNTRUSTED_ARGS fencing; reuses `_munge_fence_markers` and
    `RiskLevel` from `smart_approver.py` — no duplication.
- Tests `tests/tools/security/test_fast_gate.py` (httpx MockTransport — unit scope only):
  logit extraction & softmax calculation, probe mode selection (logprobs present/absent),
  candidate-missing / argmax-outside-{A,B} → escalate, thinking-disabled kwarg sent,
  verdict parsing, malformed JSON / missing logprob → escalate, timeout → escalate,
  fencing markers present in the request body, munging applied, connection reuse.

### Phase 2 — Config + SmartApprover wiring
- `vibe/core/config.py`:
  - `FastGateConfig(BaseModel)` near `SecurityConfig` (:359): `enabled: bool = False`,
    `base_url: str = "http://localhost:11434/v1"`, `model: str = "qwen3.5:4b"` (supports
    `qwen2.5:3b`, `qwen3:8b`, etc.; benchmarked via OpenJev at 84.5% Jev agreement),
    `timeout_ms: int = 500`, `reject_confidence: float = 0.85`, `mode: str = "auto"`.
  - `SecurityConfig.fast_gate: FastGateConfig = Field(default_factory=FastGateConfig)`.
  - Parse it in `_parse_security_config` (`config.py:851-898`) — the actual YAML→
    SecurityConfig builder.
- Serving flexibility: works with any OpenAI-compatible local server; the startup probe
  auto-selects logit vs JSON mode:
  - llama.cpp (reference for logit mode — reliable logprobs):
    `http://localhost:8080/v1` (`llama-server -hf Qwen/Qwen3.5-4B-GGUF --port 8080`)
  - Ollama ≥ v0.12.11: `http://localhost:11434/v1` — logprobs/top_logprobs are
    supported locally per current official docs (Ollama Cloud returns null —
    ollama/ollama#13638); version variance persists (ollama/ollama#16117), so the
    probe remains the source of truth.
  - omlx (installed on this machine, v0.6.4, port 8000): first-class Qwen3.5 support
    and per-request `chat_template_kwargs` (`enable_thinking`), but **no logprobs on
    `/v1/chat/completions`** (jundot/omlx#1549 open; PR #1591 unmerged) — probe will
    select JSON mode today, logit mode automatically once #1591 lands (§5.11).
  - mlx-lm (`mlx_lm.server`): logprobs implemented per its SERVER.md — a Mac-native
    logit-mode alternative to llama-server if GGUF conversion is undesirable.
  - Prerequisite on this machine: `ollama`/`llama-server` are not on PATH, but **omlx
    0.6.4 is installed** (`~/.local/bin/omlx`, app bundle) — Phase 3 can start on omlx
    in JSON mode immediately, or install llama.cpp/Ollama for logit mode.
- `vibe/tools/security/smart_approver.py`:
  - `__init__` gains `fast_gate: FastGateClient | None = None`.
  - In `assess_tool_call` (:112), between the heuristics (:120-126) and
    `_llm_risk_assessment` (:129-130): `verdict = gate.check(tool_name, tool_args)`; if
    `verdict.reject` → return `RiskAssessment(REJECT, verdict.risk_level, ...)`.
    Otherwise fall through to the LLM path unchanged.
- `vibe/core/coordinators.py:346-352`: construct the gate when
  `security_config.fast_gate.enabled`, pass into `SmartApprover`.
- Tests `tests/tools/security/test_decision_gate.py`: early-reject short-circuit (LLM
  client never called), uncertain → LLM called, gate down → LLM called, **no gate path
  returns APPROVE**, CRITICAL heuristics still preempt the gate.

### Phase 3 — Redteam evaluation (real local model, offline)
- No new surface, no corpus changes, no `run_redteam.py` flag changes.
- New script `scripts/eval_fast_gate.py` (offline; requires a local OpenAI-compatible
  server — omlx on :8000 works today in JSON mode, llama-server/Ollama for logit mode;
  or `--transport mlx` for in-process readout):
  - Loads `vibe/redteam/corpus_gate/gate_calibration.yaml` (default since the 40-entry
    calibration set landed; `--corpus` still accepts `corpus/s4_approval.yaml`). The
    calibration set lives outside the bundled corpus dir because the offline approval
    oracle cannot express "dangerous but not pattern-matched" (it would force
    mislabeling evade-L1 hostiles as "allowed"); see the file header.
    `tests/redteam/test_gate_corpus.py` pins composition (23 hostile / 17 benign) and
    enforces the per-entry `l1-covered`/`l1-clean` tags against PatternEngine as a
    regression check. Each entry runs through
    (a) baseline SmartApprover (heuristics + recording-LLM pattern from
    `oracles.py:152-238`) and (b) SmartApprover+FastGate; reports catch-rate on hostile
    entries, FPR on benign controls, veto latency p50/p95.
  - Aggregation/parsing unit-tested with mocked HTTP under `tests/redteam/`; the live
    local-server run is manual (CI cannot assume Ollama/omlx — same gating philosophy as
    the eval job's `VIBE_BASE_URL` probe).
- **Acceptance (no security regression tolerated)**: combined catch-rate ≥ baseline; FPR
  ≤ baseline; veto p50 < 200 ms on this M4.
- **Threshold is empirical**: sweep `reject_confidence` over the corpus, report
  catch-rate/FPR per threshold, and ship the value with FPR = 0 on benign controls and
  maximal hostile catch — not the a priori 0.85.
- Findings appended to `docs/redteam_report.md`.

### Phase 4 — Exploratory parallel-decision backends (optional; blocks nothing)
Only if Phase 3 shows the gate earning its keep AND true calibrated probabilities become
desirable (entropy > self-reported confidence): Jev/DiffusionGemma-class backends behind
the FastGateClient interface.
- A: rented A100/H100 + `mmastrac/vllm@structured-reads-main` (PR #57250 unmerged; pin SHA).
- B: this Mac — Rapid-MLX 4-bit quant (~13–14 GB, tight on 24 GiB) + DIY seeded-canvas
  single-step read sampler.
- C: this Mac — mlx-lm parallel-constrained-decoding with a small AR model.
- D: hosted TypeSafe Jev early access.

### Phase 5 — Docs, lint, CI hygiene
- `docs/CONFIGURATION.md` + `docs/sample_config.yaml`: `security.fast_gate.*`.
- AGENTS.md §7: document the optional veto-only pre-filter and the "never early-approve"
  rule for any future gate.
- `black` / `ruff` / `mypy` clean; `pytest -x --tb=short -q` green.

## 3. What runs where (v2.1)

| Phase | This Mac (M4/24GB) | Extra hardware |
|---|---|---|
| 1 client | ✅ | — |
| 2 wiring + config | ✅ | — |
| 3 redteam eval (local Ollama) | ✅ | — |
| 4 exploratory backends | B/C only | A: rented GPU; D: hosted |
| 5 docs/lint/tests | ✅ | — |

## 4. Risks / open questions (v2.1)

- **Small-model reliability at veto**: measured in Phase 3; if the catch contribution is
  ~0, the gate ships disabled (default `enabled: false`) or is dropped.
- **Latency added to the benign path**: one extra local call (~80–150 ms) before LLM
  escalation; bounded by `timeout_ms`; if profiles show it, restrict the gate to a
  configurable tool list (e.g. bash/write_file only).
- **Prompt injection at the gate**: mitigated by fencing + veto-only semantics (§1);
  covered by S4 corpus entries in Phase 3.
- **Verdict JSON robustness**: small models emit malformed JSON; parse failure → escalate
  (tested); consider Ollama `format: json` enforcement.

---

## 5. Engineering Review & Critique

**Reviewed:** 2026-09-19  
**Reviewer:** Engineering Management / Systems Architect  
**Verdict:** 🚨 **REDESIGN REQUIRED** (addressed by v2 above; adjudication in §5.8)

### 5.1 Executive Summary

While the high-level intent—introducing a fast, low-latency "System 1" pre-filter in front of `SmartApprover` to reduce frontier LLM cost and latency—is valuable in theory, **the plan's technical foundation is fundamentally flawed**.

The proposal is blocked by hard hardware limits, relies on unmerged and speculative upstream code, introduces severe sync/async runtime crashes into the security pipeline, and creates an asymmetric security vulnerability by allowing an experimental pre-filter to bypass deep inspection and auto-approve tool execution.

---

### 5.2 Hardware & Infrastructure Feasibility Reality Check

The plan is titled *"Local 'System One' Decision Gate... for vibe-agent"*, yet Section 0 immediately proves that **DiffusionGemma cannot run locally on the target development machine** (Apple M4, 24 GiB unified RAM):
1. **No Metal Backend in vLLM:** vLLM has no Apple Silicon / Metal support; macOS is CPU-only, making a 26B model unexecutable.
2. **Memory Requirements:** `diffusiongemma-26B-A4B-it` in bf16 requires ~52 GB VRAM (80 GB A100/H100 tier). The NVFP4 quant requires Blackwell-class GPUs (RTX 50xx / DGX Spark). Neither fits in 24 GiB unified RAM.
3. **Speculative Upstream Code:** vLLM PR #57250 is unmerged. Upstream `mlx-lm` rejects the checkpoint (issue #1391), and the community fork `Rapid-MLX` lacks structured-read sampling logic, requiring custom sampler development.
4. **Cloud GPU Rental Contradiction:** Proposing that developers rent 80 GB A100 GPUs in the cloud to test a "local pre-filter" for an agent whose primary local brain is an 8B model (`qwen3:8b`) violates repository **Rule 1 (Think Before Coding / Push back when a simpler approach exists)** and **Rule 2 (Simplicity First / Nothing speculative)**.

---

### 5.3 Architectural & Runtime Flaws

#### 1. Sync / Async Impedance Mismatch
* `SmartApprover.assess_tool_call` (`vibe/tools/security/smart_approver.py:112`) is a **synchronous** method called synchronously by `SecurityCoordinator._check_smart_approver` (`vibe/core/coordinators.py:513`), unit tests, and redteam oracles (`vibe/redteam/oracles.py:205`).
* Phase 1 defines `DecisionModelClient` as an `httpx.AsyncClient` following `flash_client.py:129-135`.
* Calling an `AsyncClient` from inside synchronous code fails when called from an active event loop:
  * In unit tests and event loop threads, `asyncio.run()` throws `RuntimeError: This event loop is already running`.
  * If wrapped with `_resolve_sync`, it aborts with `RuntimeError: async LLM client cannot be awaited from the event-loop thread`, causing the gate to silently fall back on every run.
* **Requirement:** The client must provide a synchronous interface (e.g. `httpx.Client`) with persistent connection pooling, or the entire security evaluation pipeline must be refactored to async.

#### 2. Connection Lifecycle & Latency Budget
* The plan targets `timeout_ms: 800` and `p50 < 300 ms`.
* Referencing `flash_client.py:129` (`async with httpx.AsyncClient(...) as client:`) creates and destroys an HTTP client per tool call.
* Instantiating new HTTP clients on every tool call incurs socket allocation and TCP connection handshakes (20–60 ms of overhead), contradicting the sub-300 ms target. A high-frequency gate requires persistent connection pooling.

#### 3. Configuration Parser Misidentification
* Phase 2 states: *"Also parse it in legacy `VibeConfig.load()` (:853-897) so YAML configs work."*
* In `vibe/core/config.py:851-899`, the target function is actually `_parse_security_config(raw: dict[str, Any]) -> SecurityConfig`.

---

### 5.4 Security & Trust Boundary Breakdown

#### 1. Asymmetry of Risk: Dangerous Auto-Approval
* In `vibe/core/coordinators.py:522-530`, `SmartApprover` acts as a **veto gate**:
  ```python
  assessment = self._smart_approver.assess_tool_call(tool_name, tool_args)
  if assessment.decision.value == "reject":
      return SecurityCheckResult(allowed=False, ...)
  return SecurityCheckResult(allowed=True, ...)
  ```
* If the DecisionGate returns `LOW` risk and short-circuits the full LLM evaluation, it returns `ApprovalDecision.APPROVE`.
* **Vulnerability:** An attacker crafting an obfuscated payload or prompt injection only needs to fool the 32-token canvas model. If marked `LOW`, the frontier LLM is **never consulted**, and the destructive call executes unchecked.
* Phase 4 even accepts: *"catch-rate ≥ LLM-SmartApprover baseline minus 2 pp"*. **Tolerating a 2% drop in security catch rate in exchange for lower latency is an unacceptable regression in a fail-closed architecture.**
* **Security Rule:** A fast pre-filter in a defense-in-depth pipeline may safely **early-reject** (fast veto on obvious attacks) or **escalate** to a stronger model. It must **never early-approve** without human consent or frontier model analysis.

#### 2. Prompt Injection & Missing Argument Fencing
* `SmartApprover` explicitly guards against prompt injection using untrusted argument fencing (`UNTRUSTED_ARGS_BEGIN`, `UNTRUSTED_ARGS_END`) and `_munge_fence_markers` (`smart_approver.py:68-79`).
* The plan proposes passing a Noul query and single-token Choice options without specifying how tool arguments are sanitized, delimited, or fenced in the diffusion canvas prompt. Diffusion models are susceptible to adversarial prefixes and prompt manipulation.

---

### 5.5 Redteam Harness & Test Integrity

#### 1. Attack Surface Taxonomy Confusion
* In `vibe/redteam/corpus.py:17-25`, surfaces represent **attack vectors** (`bash_patterns`, `file_safety`, `ssrf`, `approval`, `skill_supply`, `mcp`).
* S4 (`approval`) already covers `HumanApprover` and `SmartApprover`.
* The DecisionGate is a defense component within S4, not a new attack surface `s8_decision_gate`. Creating `s8_decision_gate.yaml` that clones payloads from `s1` and `s4` pollutes the taxonomy.

#### 2. Mock Transport Renders Tier A Testing Inert
* The plan specifies: *"stub HTTP per S7 pattern (:289-320) so Tier A stays offline."*
* In S7 (MCP), the stub verifies that SSRF blocking occurs *before* any HTTP request is dispatched.
* For DecisionGate, stubbing HTTP with canned probabilities tests only that Python parses JSON; **it tests zero model calibration, zero catch rate, and zero false-positive metrics**.

#### 3. Non-Existent CLI Flag
* Phase 4 states: *"Run `python scripts/run_redteam.py (surface decision_gate) live`"*.
* As verified in `scripts/run_redteam.py:124-140`, `run_redteam.py` only accepts `--live`, `--provider`, and `--model`. It has no `--surface` flag. Adding `s8_decision_gate.yaml` would force it to run unconditionally in all offline redteam runs.

---

### 5.6 Pragmatic Production-Ready Alternative

If the goal is a low-latency, low-cost pre-filter before calling a frontier model, **leverage the infrastructure already running in this repository**:

1. **Use Existing Local Stack (`qwen3:1.7b` on Ollama):**
   * The repository already standardizes on Ollama locally with `qwen3:1.7b` for flash operations via `FlashLLMClient` (`vibe/memory/flash_client.py`).
   * `qwen3:1.7b` runs in ~80–150 ms on an Apple M4 Mac with zero cloud cost, no rented GPUs, and no unmerged vLLM PRs.
2. **Synchronous, Pooled Client:**
   * Implement a dedicated `FastGateClient` using `httpx.Client(timeout=0.5)` with persistent connection pooling to Ollama or a local small model endpoint.
3. **Safe Asymmetric Veto Gate:**
   * The gate can **only** early-reject (`CRITICAL`/`HIGH` risk with high confidence) or flag for human review.
   * It must **never** auto-approve a call that would otherwise require LLM evaluation or human review.
4. **Integration Under Surface S4:**
   * Test against the existing S4 approval corpus in `vibe/redteam/corpus/s4_approval.yaml`, measuring latency reduction on obvious rejections without degrading catch rate.

---

### 5.7 Pre-Implementation Review Checklist

```
❌ Architecture locked: Major sync/async mismatch; unmerged upstream dependencies.
❌ Feasibility verified: Model cannot run on host hardware; requires cloud GPU rental.
❌ Security posture sound: Allows low-confidence pre-filter to early-approve tool execution.
❌ Test plan viable: Relies on mock transport that renders calibration testing inert.
🚨 Blockers:
   1. vLLM has no Metal backend on macOS.
   2. DiffusionGemma 26B exceeds machine memory (24 GiB vs 52 GB needed).
   3. vLLM PR #57250 is unmerged.
   4. SmartApprover is synchronous; proposed DecisionModelClient is async.

VERDICT: 🚨 REDESIGN REQUIRED
```

---

### 5.8 Critique Adjudication (2026-09-19)

Every critique was verified against the code before being applied. Verdicts:

| Critique | Verdict | Evidence / action taken in v2 |
|---|---|---|
| 5.2 hardware infeasibility | **Accepted** | Matches §0 findings (vLLM has no Metal backend; bf16 ~52 GB; NVFP4 Blackwell-only; PR #57250 unmerged; mlx-lm#1391 open). DiffusionGemma demoted to exploratory Phase 4. |
| 5.2.4 cloud-rental contradiction | **Accepted** (direction) | v2 requires no rentals and no new hardware — local Ollama only. |
| 5.3.1 sync/async mismatch | **Accepted** | Verified: `assess_tool_call` is sync (`smart_approver.py:112`); `_resolve_sync` (:14-30) raises on the event-loop thread, so an async gate would silently fall back in pytest-asyncio tests and redteam oracles. v2 uses a synchronous pooled `httpx.Client`. Nuance: `_resolve_sync` works in production because the security check runs on a worker thread — the failure bites loop-thread callers (tests/oracles), which is still fatal for a testable gate. |
| 5.3.2 per-call client overhead | **Accepted with correction** | Persistent pooling adopted. The 20–60 ms handshake figure is overstated for loopback (sub-ms to few ms), but per-call client construction is still wasteful and contrary to the latency budget. |
| 5.3.3 config parser name | **Accepted** | Verified: the YAML→SecurityConfig builder is `_parse_security_config` (`config.py:851-898`). v2 Phase 2 cites it correctly. |
| 5.4.1 early-approve asymmetry | **Accepted — the central flaw** | Verified veto logic at `coordinators.py:522-533`. v2 gate is veto-only (early-reject or escalate, never approve); the "−2 pp" acceptance tolerance is dropped in favor of a no-regression rule. |
| 5.4.2 missing fencing | **Accepted** | `UNTRUSTED_ARGS_*` constants and `_munge_fence_markers` (`smart_approver.py:47-78`) are reused for gate input. Bonus: veto-only semantics neutralize the injection asymmetry — a fooled gate can only cause escalation or an extra LLM call. |
| 5.5.1 surface taxonomy | **Accepted** | No `s8_decision_gate`; existing `s4_approval.yaml` is reused. |
| 5.5.2 mock renders Tier A inert | **Accepted** (substance) | Catch-rate/FPR are measured only against a real local model; mocks are confined to unit tests of parsing/wiring. |
| 5.5.3 non-existent `--surface` flag | **Accepted** | Verified `run_redteam.py:124-138` (`--live/--provider/--model` only). v2 uses a standalone `scripts/eval_fast_gate.py`. |
| 5.6 pragmatic alternative | **Accepted with corrections** | Two factual slips in the critique: the FlashLLMClient default model is `phi3:mini`, not `qwen3:1.7b` (`flash_client.py:43`), and `FlashLLMClient` is itself async with per-call client construction — so v2 builds a dedicated sync pooled client rather than reusing it, while keeping its endpoint/config conventions. The recommendation itself (existing local stack, sync pooled client, veto-only gate, S4 integration) is adopted wholesale as v2 Phases 1–3. |

**Not adopted:** §5.2's implication that the Jev/DiffusionGemma investigation was waste.
It produced the feasibility map and the backend-agnostic interface now preserved as
Phase 4 options — but the critique is right that they were positioned as the foundation
instead of the frontier.

---

### 5.9 Second Critique Adjudication & OpenJev Findings (2026-09-19)

Following the initial redesign into v2, a second architectural review evaluated **OpenJev / SemIf** (`openjev.com`) benchmarks and runtime implementation patterns, resulting in **Plan v2.1**.

#### Key Findings from OpenJev / SemIf:
1. **Direct-Logit Readout vs. JSON Text Generation:**
   * OpenJev demonstrated that generating token-by-token JSON strings adds 200–500 ms of autoregressive decoding and produces uncalibrated, self-reported confidence values.
   * Reading raw logits at `max_tokens=1` over candidate tokens (`A: Safe` vs `B: Dangerous`) computes true mathematical softmax probabilities in ~50–100 ms with **zero syntax errors or JSON parsing failures**.
2. **Small-Model Benchmark Data (Accuracy & Jev Agreement):**
   * SemIf published empirical benchmarks comparing small models on categorical choices against TypeSafe Jev:
     * `MiniCPM5 2B`: 68.6% authored accuracy, **63.7% agreement with Jev** (unacceptably high ~31% error rate for zero-trust security).
     * `Qwen3.5 4B`: **81.3% authored accuracy, 84.5% agreement with Jev** (high agreement, ~3 GB GGUF, runs easily in ~100 ms on Apple M4).
     * `phi3:mini` (3.8B): older architecture, less calibrated for logit extraction than Qwen 3.5.
   * **Decision:** Qwen3.5 4B is adopted as the primary benchmarked model for the local fast gate.
3. **Serving Decoupling:**
   * Ollama is not installed by default on all host environments (`command not found: ollama`).
   * v2.1 standardizes on any standard OpenAI-compatible local server (supporting both `http://localhost:11434/v1` via Ollama and `http://localhost:8080/v1` via `llama-server`).

| v2 Proposal | v2.1 Refinement (Applied) | Rationale |
|---|---|---|
| Constrained JSON generation | **Direct-logit readout (`max_tokens=1, logprobs=True`)** with JSON fallback | Eliminates JSON parsing crashes; cuts latency to ~50–100 ms; provides true softmax probability $P(\text{Dangerous})$. |
| Default model `phi3:mini` | **Default model `qwen3.5:4b`** | Verified on SemIf benchmarks at 84.5% Jev agreement (vs MiniCPM's 63.7% and phi-3's uncalibrated logits). Fits in ~3 GB RAM. |
| Hard-coded to local Ollama | **Generic OpenAI-compatible local endpoint** (`llama-server`, Ollama) | Accommodates environments without Ollama in PATH; permits running standalone GGUF builds. |

**Final Verdict:** ✅ **APPROVED (Plan v2.1 is locked and ready for implementation).**

---

### 5.10 Final Review (v2.1, 2026-09-19)

External claims were independently verified before sign-off:

- **OpenJev/SemIf (openjev.com) is real**, and the §5.9 benchmark numbers match the
  source exactly (Qwen3.5 4B: 81.3% authored / 84.5% TypeSafe agreement; MiniCPM5 2B:
  68.6% / 63.7%; published Jev: 88.3%). Caveat: the TypeSafe column is *agreement with
  Jev* on a 102-row public subset — a model-selection heuristic, not veto accuracy on our
  attack distribution. Phase 3 remains the deciding measurement.
- **Qwen3.5-4B exists** with multiple GGUF builds (~3 GB at Q4_K_M) — fits the M4 easily.
- **Ollama logprobs gap confirmed**: ollama/ollama#16117 (open feature request) and
  #13638 (logprobs returns null) — the OpenAI-compatible layer does not reliably return
  logprobs. llama-server does.
- **No local server is currently installed on this machine** (`ollama` CLI absent from
  PATH; `~/.ollama` exists) — Phase 3 requires an explicit install step (added).

Amendments required by this review (applied inline above):

| # | Finding | Fix |
|---|---|---|
| F1 | v2.1 called the logit softmax a "true mathematical probability"; SemIf itself disclaims this ("not calibrated confidence… do not include every answer the model might prefer") | Reworded to "conditional two-way score"; threshold set empirically, not assumed (§1, Phase 3) |
| F2 | Default endpoint (Ollama) likely lacks logprobs → silent JSON fallback would negate v2.1's headline benefit | Startup capability probe with auto mode selection; llama-server documented as the reference server for logit mode (Phase 1, Phase 2) |
| F3 | Candidate-token coverage: with `top_logprobs=5`, A/B may be absent and the argmax may be neither (incl. `<think>` on reasoning models like Qwen3.5); the two-way softmax hides "neither" mass | Escalate on missing candidate or argmax ∉ {A,B}; `enable_thinking: false` required (§1, Phase 1) |
| F4 | `reject_confidence=0.85` was a priori | Phase 3 threshold sweep; ship the FPR = 0 / max-catch value |

**Verdict:** ✅ **APPROVED with amendments F1–F4 (applied). Plan v2.1 is ready for
implementation.**


### 5.11 Backend Addendum: omlx Evaluation (2026-09-20)

**Question (user-raised):** this machine already runs MLX models locally via **omlx**
(installed: v0.6.4, `/Applications/oMLX.app`, CLI at `~/.local/bin/omlx`). Can omlx serve
as the fast-gate / SemIf-pattern backend instead of installing Ollama or llama.cpp?

**Answer: yes for JSON mode today; not yet for logit mode — and the `mode="auto"` probe
already handles both, with zero code changes.**

Findings (verified against the installed app bundle source and upstream tracker):

| Capability | omlx 0.6.4 status | Evidence |
|---|---|---|
| OpenAI-compatible `/v1/chat/completions` | ✅ port 8000 | [README](https://github.com/jundot/omlx) API table |
| `chat_template_kwargs` (`enable_thinking: false`) | ✅ per-request field, explicitly documented | `api/openai_models.py` `ChatCompletionRequest.chat_template_kwargs` |
| Qwen3.5 family support | ✅ first-class (tool-call format auto-detect, optional native custom kernels) | README model tables; `omlx launch codex --model qwen3.5` |
| `logprobs` / `top_logprobs` on chat completions | ❌ **not exposed** — request model has no such fields; extra params silently ignored; response `logprobs` always null/empty | [issue #1549](https://github.com/jundot/omlx/issues/1549) (open since 0.3.x); confirmed absent in installed 0.6.4 source (also absent from 0.6.3/0.6.4 and 0.7.0.dev release notes) |
| `/v1/completions` logprobs | ❌ no field either | `api/openai_models.py` `CompletionRequest` |
| Responses API `top_logprobs` | ⚠️ field declared but **never consumed** by the route | `api/responses_models.py:119`; no consumer in `responses_utils.py`/`server.py` |
| Engine-level logprobs | ✅ computed internally (sampling needs them); scheduler discards them when unrequested | `request.py` `SamplingParams.logprobs`, `scheduler.py` |

**Upstream trajectory:** [PR #1591](https://github.com/jundot/omlx/pull/1591)
("OpenAI-compatible logprobs for /v1/chat/completions") implements exactly the OpenAI
shape our client reads (`choices[0].logprobs.content[].top_logprobs`, range 0–20). It is
**open and actively rebased** (last push 2026-09-20) but unmerged. Caveat from the PR
body: logprobs are omitted on speculative/MTP decode paths — omlx's Qwen3.5 custom
kernels use MTP, so even after the merge the probe must remain the authority (a
logprobs-less response on a speculative path resolves to JSON mode, correctly).

**Interaction with the shipped implementation (no changes required):**

- `mode="auto"` probes each endpoint lazily and **does not cache failures** — pointing
  `security.fast_gate.base_url` at `http://localhost:8000/v1` yields JSON mode today and
  auto-upgrades to logit mode whenever omlx starts returning logprobs. No client edits.
- `chat_template_kwargs: {"enable_thinking": false}` is sent on both modes and is
  supported by omlx — thinking suppression works for Qwen3.5 out of the box.
- omlx's **tiered KV cache / prefix sharing** is a genuine latency upside for the gate:
  the gate prompt's template prefix is identical across calls, so repeated vetoes hit the
  hot prefix cache (benefits JSON mode today, logit mode later). This partially offsets
  JSON mode's 200–500 ms decode overhead; measure in Phase 3 rather than assume.

**Revised backend guidance for Phase 3 (benchmark matrix):**

| Backend | Mode today | Setup cost on this Mac | Notes |
|---|---|---|---|
| omlx :8000 (installed) | JSON | zero | Also the dogfood path — user runs it daily |
| llama-server :8080 | **logit (reference)** | `brew install llama.cpp` + ~3 GB GGUF | Remains the logit-mode ground truth |
| mlx-lm (`mlx_lm.server`) | logit | `pip install mlx-lm` | Mac-native; logprobs per its SERVER.md |
| Ollama :11434 (≥0.12.11) | logit (probe-verified) | `brew install ollama` | Local logprobs per current docs; Cloud returns null |

Phase 3 acceptance criteria are unchanged (catch-rate ≥ baseline, FPR ≤ baseline, veto
p50 < 200 ms). Recommendation: run the matrix on **omlx (JSON) and llama-server (logit)**
at minimum — this quantifies exactly what logit mode buys over JSON mode on real
hardware, which is the open question §5.9/F2 left to measurement.

**SemIf native MLX backend (`src/semif_phase1/mlx_backend.py`) — direct-use evaluation:**
the file implements true 0-token readout in-process on macOS arm64 — `model(ids)[0, -1]`
last-position logits gathered at the option-token slots, plus two prefix-reuse paths
(`SerialPrefixScorer`, `score_shared` with cache-branch merging; the 20 decisions/s
parallel path). It runs on this M4 as-is:

- **Run it directly (Phase 3 yardstick)**: clone SemIf, `pip install -e '.[test,mlx]'` in
  a throwaway venv, `semif-score --backend mlx --mode direct --model Qwen/Qwen3.5-4B
  --revision <pinned-40-char-sha>`. Hard constraints enforced by its loader: macOS arm64
  only, `model_type == "qwen3_5"` only (no custom model code), pinned revision required
  for remote checkpoints (all artifacts SHA-256 recorded into results), optional
  in-memory 4/8-bit affine quant (BF16 ~8–9 GB → ~3 GB at 4-bit). Our `.venv` lacks only
  `mlx`/`mlx-lm` (transformers/huggingface_hub present).
- **Not vendored into `fast_gate.py` as-is**: the module depends on SemIf's own
  `core/direct/shared` prompt pipeline (`encode_prompt` renders *their* benchmark schema —
  state/question/options JSON — not our fenced UNTRUSTED_ARGS security prompt), and its
  audit metadata format serves their harness, not ours. MIT license permits adaptation
  with attribution.
- **Phase 3.5 (in-process `mlx` transport) — IMPLEMENTED 2026-09-20**:
  `transport: http|mlx` added to `FastGateConfig`/`FastGateClient`; the mlx transport
  (`vibe/tools/security/fast_gate_mlx.py`, ~150 LoC) reimplements only the readout core —
  lazy pinned load (40-char revision required for remote checkpoints), chat template with
  `enable_thinking=False`, last-position gather at the A/B token slots, two-way softmax —
  against our own fenced prompt builder. Full-vocabulary access makes the
  candidate-missing guard unnecessary in-process; the argmax guard and prompt-length cap
  (`max_prompt_tokens`, escalate when exceeded) remain. No server process, no RTT, immune
  to the omlx logprobs gap. Costs accepted: `mlx`/`mlx-lm` are lazily imported optional
  deps (never imported at module level; missing deps → unavailable → escalate), ~3 GB
  resident in the agent process at 4-bit, and native-code crash risk moves in-process
  (fail-open `check()` wraps the forward pass exactly as it wraps HTTP). Template-prefix
  KV caching is deliberately deferred (the gate prompt's variable args sit in the middle
  of the template, so prefix reuse buys little — revisit if profiling says otherwise).
  CI-safe unit tests inject loader/forward fakes (16 tests,
  `tests/tools/security/test_fast_gate_mlx.py`); live-model validation runs via
  `scripts/eval_fast_gate.py --transport mlx --model Qwen/Qwen3.5-4B --model-revision
  <pinned-sha>`, with `--export-semif` / `--crosscheck-semif` providing a verdict-level
  agreement check against the SemIf reference harness (argmax agreement + mean |ΔP|).

**Correction to §5.10:** "No local server is currently installed on this machine" was
accurate only for `ollama`/`llama-server` CLIs — omlx (0.6.4 at verification time) was already installed and is
now the zero-setup path for Phase 3 (JSON mode).

**Calibration corpus findings (2026-09-20, surfaced while authoring
`corpus_gate/gate_calibration.yaml`):**
- Layer-1 false positive: PatternEngine rule `shutdown-now` matches the literal
  substring `reboot`, so a cron-persistence line using `@reboot` is flagged CRITICAL.
  The corpus avoids the spelling and documents it; whether to tighten the pattern is a
  separate PatternEngine change (out of scope here).
- Layer-1 coverage gap: `nc-connect` matches IP-literal destinations
  (`nc 203.0.113.9 4444`) but not hostname form (`nc evil.example 4444`). The corpus
  keeps the hostname form as a hostile evade-L1 entry and documents the gap.
- Baseline observation (stub-gate smoke run): heuristic-only SmartApprover rejects
  almost nothing in this corpus (catch ≈ 4%) while flagging ~12% of benign borderline
  entries — exactly the gap a calibrated veto gate is meant to close.
