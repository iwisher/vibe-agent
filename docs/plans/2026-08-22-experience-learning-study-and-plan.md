# Experience-Learning Study Log & Consolidated Plan

> Date: 2026-08-22. Status: **active reference** for workstreams A–D.
> Context: follow-up to the 2026-08-22 improvements (CLI rendering, deterministic
> skill script steps, memory retrieval wiring, trajectory reflection).

---

## 1. Study log — paper list (all verified on arXiv/publisher pages)

### Foundations (≤2025) — implemented in the 2026-08-22 change set

| Paper | Venue | One-line takeaway |
|---|---|---|
| [CodeAct: Executable Code Actions Elicit Better LLM Agents](https://arxiv.org/abs/2402.01030) | ICML 2024 | Executable actions beat interpreted ones → deterministic skill script steps |
| [Anthropic: Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) | 2025 eng. blog | Deterministic logic lives in `scripts/`, not prose |
| [Agent Workflow Memory (AWM)](https://arxiv.org/abs/2409.07429) | ICML 2025 | Induce reusable workflows from trajectories, inject at inference |
| [Agentic Context Engineering (ACE)](https://arxiv.org/abs/2510.04618) | 2025 | Evolving playbook; incremental delta lessons with helpful/harmful counters; merge-don't-rewrite |
| [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) | AAAI 2024 | Distill cross-task insights from successes AND failures |
| [Reflexion](https://arxiv.org/abs/2303.11366) | NeurIPS 2023 | Failed trajectories are the richest learning signal |
| [A-MEM: Agentic Memory](https://arxiv.org/abs/2502.12110) | NeurIPS 2025 | Structured, status-aware memory notes |
| [Generative Agents](https://arxiv.org/abs/2304.03442) | 2023 | Relevance/recency/importance gating at retrieval time |

### 2026 frontier batch (research window ~May 22 – Aug 22, 2026 + near-window)

| Paper | Date | One-line takeaway |
|---|---|---|
| [Evo-Harness: Context-to-Harness Skill Compilation](https://arxiv.org/abs/2608.15071) | 2026-08-15 | Distill noisy one-shot executions into reusable *executable* skill harnesses |
| [Muscle Memory for Agents: Compile not Merely Retrieve](https://arxiv.org/abs/2608.08995) | 2026-08-10 | Compile recurring intent into specialist executables; 88.9% win rate when specialist fires |
| [EvoHarness-RL](https://arxiv.org/abs/2608.05446) | 2026-08-05 | RL-trained runtime harness; "harness action space" = what to store/retrieve/present |
| [PivoARL: Pivotal-Aware Self-Feedback Retry](https://arxiv.org/abs/2607.03702) | 2026-07-04 | Retry locally from the pivotal erroneous turn; ~42% fewer turns than full retry |
| [Retrospective Harness Optimization via Self-Preference](https://arxiv.org/abs/2606.05922) | 2026-06 | Optimize prompts/params/workflow code around a frozen model |
| [Adaptive Auto-Harness](https://arxiv.org/abs/2606.01770) | 2026-06 | Sustained self-improvement on open-ended task streams without degradation |
| [Rethinking Continual Experience Internalization](https://arxiv.org/abs/2606.04703) | 2026-06-03 | Naive multi-iteration experience learning collapses; principle-level + step-wise injection survive |
| [Harness Updating Is Not Harness Benefit](https://arxiv.org/abs/2605.30621) | 2026-05-28 | Harness updates ≠ benefit; held-out eval acceptance required |
| [Evolving-RL](https://arxiv.org/abs/2605.10663) | 2026-05-11 | Jointly optimize experience extraction AND utilization; gains only when co-evolved |
| [A Survey on the Evolution of LLM Agent Memory Mechanisms](https://arxiv.org/abs/2605.06716) | 2026-05-07 | Storage → Reflection → Experience three-stage framework |
| [AgentHER: Hindsight Experience Replay for Trajectory Relabeling](https://arxiv.org/abs/2603.21357) | v4 2026-05-10 | Relabel failed trajectories as demonstrations of achievable goals; +7.6–11.4% over success-only SFT |
| [Meta-Harness: End-to-End Optimization of Model Harnesses](https://arxiv.org/abs/2603.28052) | 2026-03-30 | Search over harness *code* with full access to prior candidates' source/scores/traces; beats hand-engineered baselines (TerminalBench-2) |
| [XSkill: Continual Learning from Experience and Skills](https://arxiv.org/abs/2603.12056) | ICML 2026 (v3 2026-07-01) | Dual streams: action-level experiences + task-level skills; cross-rollout critique; usage feedback loop |
| [Decocted Experience Improves Test-Time Inference](https://arxiv.org/abs/2604.04373) | 2026-04-06 | Essence-extracted, organized, saliently retrieved experience is what makes context scaling work |
| [Experiential Reinforcement Learning (ERL)](https://arxiv.org/abs/2602.13949) | 2026-02-15 | Reflect→retry→consolidate inside RL; +11% on tool-use tasks |
| [Lilian Weng: Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/) | 2026-07-04 | Practitioner map of the harness-evolution landscape |

### Cross-cutting takeaway

The frontier moved: **retrieve text → compile capabilities** (Evo-Harness, Muscle Memory);
**store everything → curate principles with feedback loops** (XSkill, Decocted Experience);
**accumulate → defend against collapse** (2606.04703); **hand-designed harness → optimized
harness with a skepticism gate** (Meta-Harness, 2605.30621); **full retry → pivotal local
repair** (PivoARL); **success-only → failure-as-primary-signal** (AgentHER, ERL).
Against the survey's Storage → Reflection → Experience stages, the shipped work completes
those three; the next stages are **Compilation** (lessons → executable skills) and
**Optimization** (the harness itself evolves under a held-out gate).

---

## 2. Consolidated workstreams (7 raw items merged → 4)

| # | Workstream | Absorbs raw items | Depends on |
|---|---|---|---|
| A | Lesson quality & feedback: critique gate at write time, usage→counter feedback loop, pivotal-turn annotation | ① reflection critique pass, ② usage-feedback loop, ⑤b pivotal annotation | — |
| B | Lesson lifecycle: compaction job (merge similar lessons → principle-level), then lesson→compiled-skill promotion via SkillMaker with eval gate | ③ lesson compaction, ④ skill promotion | A |
| C | Pivotal local retry in error recovery (retry from failed step, reuse correct prefix) | ⑤a runtime retry | — (parallel with A) |
| D | Offline self-improvement: EvoX-over-harness (`vibe evox run --target harness`) with regression-gate acceptance; AgentHER relabeling in RLM export | ⑥ ⑦ | A, B |

Execution order: **A and C first** (mostly disjoint), then **B**, then **D**.
The eval-acceptance gate is built once (in B) and reused (in D).

### Why the merges

- ①+②+⑤b all live in `vibe/memory/reflection.py` and jointly define counter semantics —
  three separate passes would edit the same curator code three times.
- ③+④ share similarity clustering over lesson pages; ③'s output (principle-level,
  counter-validated lessons) is the correct input for ④'s promotion.
- ⑥+⑦ are both offline pipelines reading trace/eval stores and sharing the held-out gate.
- ⑤ splits: annotation (⑤b) rides with A; runtime retry (⑤a) is standalone C.

---

## 3. Self-clarification Q&A — simplest/stable choices for A and C

Asked of ourselves in lieu of user clarification; each answer chosen for simplicity +
stability across valid alternatives.

**A1. Critique pass: separate LLM call vs single-call self-score?**
Options: (a) second critique call per lesson (XSkill-style, 2× LLM cost per session);
(b) one call — model emits a `generality` score per lesson, curator drops low scores;
(c) heuristic-only gating, no LLM.
**Choice: (b).** Zero extra calls, deterministic threshold gate (`memory.reflection.min_generality`,
default 3 on a 1–5 scale). If the LLM omits the score → **fail-open** (accept the lesson);
bad lessons are weeded out later by usage-feedback counters. Fail-closed would silently
drop valid lessons on weak models — less stable.

**A2. Usage feedback: how to attribute outcomes to lessons?**
Options: (a) blanket attribution of session outcome to all injected lesson pages;
(b) fine-grained per-action attribution.
**Choice: (a).** ACE-style noisy counters are the established, simple semantics:
COMPLETED → `helpful+1`, ERROR → `harmful+1`, INCOMPLETE → no signal, applied only to
lesson-tagged pages actually injected by `_build_wiki_hint` (tracked on
`QueryLoop._injected_lesson_ids`). No LLM call; implemented as
`TrajectoryReflector.record_usage()` reusing the curator's counter parse/update helpers.

**A3. Where does record_usage run — own task or folded into the reflection task?**
**Choice: folded in.** `_reflect_on_trajectory()` first applies usage counters (no LLM),
then reflects on the current trajectory (LLM). One task, correct ordering, no new
plumbing in the `run()` finally block, same config gate.

**A4. Pivotal annotation coupling between A and C?**
**Choice: defensive read.** Reflector reads `getattr(loop, "_pivotal_turn", None)` and
includes it in `applies_when` when present; no hard dependency on C landing first.

**C1. What is "pivotal retry" in a ReAct-style loop that already shows tool errors to the model?**
Options: (a) checkpoint/restore full message state at the pivotal turn (complex, risky);
(b) bounded same-boundary repair: detect repeated identical tool failures, and before
giving up to ERROR/INCOMPLETE, do ONE reflection-guided retry of the pivotal call with
the error analysis attached; (c) do nothing new (rely on organic next-iteration retries).
**Choice: (b), built on the existing `ErrorRecovery`/`RetryPolicy` machinery** — no new
recovery subsystem. Organic retries already exist but drift; the add is a
repeated-failure detector + one explicit guided retry + a hard per-call retry bound.

**C2. Which failures are retryable?**
**Choice: never retry security denials** (PatternEngine/FileSafety/HumanApprover rejections
are final — retrying them is both useless and a security smell); never retry when the
iteration budget is exhausted; retry at most once per tool-call signature.

**C3. Default on or off?**
**Choice: on, bounded** (`pivotal_retry_enabled: true`, `max_pivotal_retries: 1`). It only
activates on failure paths that would otherwise degrade to ERROR/INCOMPLETE, so the
risk budget is one extra LLM call per failing task.

**Process choice: A and C in parallel or sequential?**
Both touch `vibe/core/query_loop.py` (A: `_build_wiki_hint` + finally block; C: tool
error path). **Choice: sequential (A → C)** — simplest, zero edit-conflict risk.

---

## 4. Status

- [x] Study + consolidation (this file)
- [ ] Workstream A — lesson quality & feedback
- [ ] Workstream C — pivotal local retry
- [ ] README update
- [ ] Workstream B — lesson lifecycle (not started)
- [ ] Workstream D — offline self-improvement (not started)
