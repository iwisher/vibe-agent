# Security Execution Control Enhancement Plan for Vibe-Agent — REVISED v2

> Based on analysis of Hermes Agent and OpenClaw security architectures.
> Incorporating feedback from Gemini CLI review and Kimi CLI review.
> Date: 2026-04-25

---

## Executive Summary

Vibe-agent currently has a **minimal security posture**: basic dangerous-pattern regexes in `bash.py`, shell-metacharacter rejection, file path jailing, and a hook pipeline with two rudimentary hooks. There is **no approval system**, **no audit/logging**, **no secret redaction**, and **no fail-closed design**.

This revised plan implements a **defense-in-depth security execution control system** across 6 phases, addressing all major gaps identified by both independent reviews. Key changes from v1:
- **Config + Audit infrastructure moved to P0** (was P1/P2)
- **Hooks are the PRIMARY integration architecture**, not an afterthought
- **Added TOCTOU revalidation, env sanitization, SSRF/URL safety, skills guard**
- **Fixed _redirect_path traversal vulnerability**
- **Removed dangerous YOLO bypass anti-pattern**
- **Added fail-closed defaults throughout**

---

## REVIEW SUMMARY

### Gemini CLI Review (Rating: Approve with Changes)
**Key findings:**
1. Missing: Environment variable sanitization, network egress/SSRF protection, resource limits (ulimits)
2. Priority issue: Phase 4 (Hooks) must become P0 and precede Phase 1. Security should be decoupled from tools and implemented purely as ConstraintHooks.
3. Anti-pattern: Splitting security logic between BashTool.execute() and hooks. BashTool should only execute; all security lives in HookPipeline.
4. Design flaw: Hash brittleness in durable store (whitespace changes break hash)
5. Performance flaw: Synchronous LLM approver introduces latency and failure domain; needs <5s timeout and fail-closed fallback
6. Checkpoint frequency: Git snapshot before every write is too expensive; should batch

### Kimi CLI Review (Rating: Approve with Major Changes)
**Key findings:**
1. Missing: TOCTOU/execution-time revalidation, safe bin profiles, wrapper unwrapping/blocking, env sanitization, SSRF/URL safety, skills/plugin security, Windows hardening, binary scanning, fail-closed behaviors, permission auditing, config write protection, sub-agent isolation
2. Priority issue: Config should be P0 first. Audit logging should be P0. Smart approver should be P1 (complex, costly, failure-prone). File safety should be P0 alongside bash.
3. Anti-patterns:
   - Regex as primary defense (trivially bypassed with encoding, path manipulation)
   - _redirect_path traversal vulnerability (startswith unsafe on Windows, fallback wrong)
   - YOLO bypass via env var (inherited by children, visible in /proc/*/environ)
   - HookOutcome only has boolean allow; needs severity levels
   - Durable SHA-256 of exact command text is too brittle
   - Smart approver temperature=0 unreliable on local models (Ollama default)
   - auto_approve_in_sandbox: true without strict sandbox validation
4. Python-specific pitfalls: shlex.split() doesn't catch all injection vectors, start_new_session race conditions, working_dir not jailed

---

## REVISED PHASE ORDER

| Phase | Name | Priority | Rationale |
|-------|------|----------|-----------|
| 0 | Config + Audit Infrastructure | P0 | Foundation everything else reads |
| 1 | Hook Pipeline Enhancement | P0 | Primary integration architecture |
| 2 | Pattern Engine + Human Approval | P0 | Core blocking + approval flow |
| 3 | File Safety | P0 | Writes are as dangerous as bash |
| 4 | Env Sanitization + SSRF/URL Safety | P1 | Network and secret exfiltration |
| 5 | Secret Redaction | P1 | Prevent secret leakage in logs/output |
| 6 | Smart Approver (LLM Review) | P1 | Complex, costly, failure-prone — build after core |
| 7 | Checkpoints / Rollback | P2 | Nice-to-have after core safety |
| 8 | Skills Guard + Sub-agent Isolation | P2 | Advanced features |

---

## Phase 0: Config + Audit Infrastructure

### 0.1 Security Config Section (`vibe/core/config.py`)
Add to `~/.vibe/config.yaml`:
```yaml
security:
  approval_mode: "smart"          # manual | smart | auto
  dangerous_patterns_enabled: true
  secret_redaction: true
  audit_logging: true
  fail_closed: true               # NEW: default deny on any security component failure
  
  file_safety:
    write_denylist_enabled: true
    read_blocklist_enabled: true
    safe_root: null
    
  env_sanitization:
    enabled: true
    block_path_overrides: true    # NEW: from OpenClaw
    strip_shell_env: true         # NEW: only locale/color/terminal vars to shell
    secret_prefixes: ["*_API_KEY", "*_TOKEN", "*_SECRET", "AWS_*", "GITHUB_*"]
    
  sandbox:
    backend: "local"              # local | docker | ssh (future)
    auto_approve_in_sandbox: false # CHANGED: false by default, strict validation required
    
  audit:
    log_path: "~/.vibe/logs/security.log"
    max_events: 10000
    redact_in_logs: true
```

### 0.2 Audit Logging Framework (`vibe/tools/security/audit.py`)
- Structured security event log at `~/.vibe/logs/security.log`
- Events: `command_blocked`, `command_approved`, `command_flagged`, `file_write_denied`, `file_read_denied`, `path_traversal_attempt`, `secret_redacted`, `approval_granted`, `approval_revoked`, `env_sanitized`, `url_blocked`
- Include: timestamp, event type, severity, command/pattern, user decision, LLM decision, session ID, tool name
- Rotating log handler (max 10MB, keep 5 backups)
- **Fail-closed**: if audit logger fails to initialize, log to stderr and continue

### 0.3 Permission Auditing (`vibe/tools/security/permission_audit.py`)
- Check `~/.vibe/` state directory permissions on startup
- Warn if world-writable (0o777, 0o757, etc.)
- Check config file permissions (should be 0o600)
- Check approval store permissions (should be 0o600)
- **From OpenClaw**: Synced-folder detection (warn if under iCloud/Dropbox/OneDrive/Google Drive)

---

## Phase 1: Hook Pipeline Enhancement (`vibe/harness/constraints.py`)

### 1.1 HookOutcome Severity Levels
```python
@dataclass
class HookOutcome:
    allow: bool
    reason: str
    severity: Literal["block", "warn", "allow"] = "allow"  # NEW
    warnings: list[str] = field(default_factory=list)      # NEW: accumulate warnings
    modified_arguments: dict[str, Any] = field(default_factory=dict)
    modified_result: ToolResult | None = None
```

### 1.2 Hook Execution Rules
- **First `block` wins**: any hook returns `severity="block" → deny immediately`
- **All `warn` accumulate**: collect all warnings, pass to audit log, allow execution
- **Modified arguments compose**: each hook can transform arguments; transformations chain

### 1.3 New Built-in Hooks (all implemented as ConstraintHook classes)

| Hook | Stage | Purpose |
|------|-------|---------|
| `DangerousPatternHook` | PRE_VALIDATE | Regex pattern engine (critical→block, warning→flag) |
| `FileSafetyHook` | PRE_VALIDATE | Write denylist, read blocklist, path traversal |
| `EnvSanitizationHook` | PRE_MODIFY | Strip secrets from env, block PATH overrides |
| `UrlSafetyHook` | PRE_VALIDATE | Block SSRF targets (metadata IPs, link-local, CGNAT) |
| `PathTraversalHook` | PRE_VALIDATE | Validate all path arguments with resolve()+relative_to() |
| `SecretRedactionHook` | POST_EXECUTE | Redact secrets from tool results before returning to LLM |
| `AuditLogHook` | POST_EXECUTE | Log all tool executions with outcomes |
| `CheckpointHook` | PRE_ALLOW | Take git snapshot before file-mutating operations |

### 1.4 Hook Registration
- Config-driven: `security.hooks.enabled: ["dangerous_pattern", "file_safety", ...]`
- All hooks are registered in `QueryLoopFactory` based on config
- **Fail-closed**: if a hook raises an exception, treat as `severity="block"` unless `security.fail_closed=false`

---

## Phase 2: Pattern Engine + Human Approval

### 2.1 Dangerous Pattern Engine (`vibe/tools/security/patterns.py`)
- Extract from hardcoded `bash.py` into configurable engine
- **~70 patterns total** (20 current + 30 from Hermes + 20 from OpenClaw)
- Pattern severity levels:
  - `critical`: auto-block (rm -rf /, fork bomb, mkfs, dd if=/dev/zero)
  - `warning`: flag for review (curl | bash, git reset --hard, chmod 777)
  - `info`: log only (sudo without -S, eval)
- Command normalization pipeline:
  1. Strip ANSI escape sequences
  2. Remove null bytes
  3. Unicode NFKC normalization
  4. Collapse whitespace
- **NEW from OpenClaw**: Inline eval detection across interpreters (python -c, node -e, ruby -e, perl -e, php -r, lua -e, awk)
- **NEW from OpenClaw**: Wrapper detection (block sudo, doas, chrt, ionice, taskset, setsid; unwrap env, nice, timeout)
- **NEW from Hermes**: Pre-execution transformations (sudo → sudo -S -p '', compound background rewrite)

### 2.2 Human Approval System (`vibe/tools/security/human_approval.py`)
- CLI mode: `prompt_toolkit`-style UI with 60-second timeout
- Choices: `once` | `session` | `always` | `deny` | `view`
- **REMOVED YOLO bypass** (anti-pattern per Kimi review). Instead: `VIBE_APPROVAL_MODE=auto` env var with loud warning on startup.
- **Fail-closed**: timeout → deny (not allow)

### 2.3 Durable Approval Store (`vibe/tools/security/approval_store.py`)
- JSON file at `~/.vibe/exec-approvals.json` with `0o600`
- Atomic write (temp+fsync+rename)
- Parent dir created with `0o700`
- **File locking**: `fcntl` advisory lock for concurrent access
- **Symlink rejection**: recursive resolution of `~/.vibe/` path; reject any symlink component
- Two approval types:
  - `=pattern:<pattern_id>` — all commands matching this pattern (preferred, less brittle)
  - `=command:<sha256>` — exact command text hash (use sparingly)
- **Stricter-wins policy**: host settings can only make execution stricter

### 2.4 BashTool Integration
- **BashTool.execute() ONLY does**: normalize → shlex.split → create_subprocess_exec → timeout handling → return ToolResult
- **All security logic lives in hooks**: pattern check, approval flow, env sanitization, audit log
- BashTool registers itself with ToolSystem; HookPipeline intercepts all calls

---

## Phase 3: File Safety (`vibe/tools/security/file_safety.py`)

### 3.1 Write Denylist
- Block writes to: `~/.ssh/authorized_keys`, `id_rsa`, `id_ed25519`, `~/.env`, `~/.bashrc`, `~/.netrc`, `/etc/sudoers`, `/etc/passwd`, `/etc/shadow`
- Block write prefixes: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `/etc/sudoers.d`, `/etc/systemd`, `~/.docker`, `~/.azure`, `~/.config/gh`
- Configurable `VIBE_WRITE_SAFE_ROOT` env restriction

### 3.2 Read Blocklist
- Block reads of: `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/stdin`, `/dev/tty`, `/dev/stdout`, `/dev/stderr`
- Block read prefixes: `/etc/`, `/boot/`, `/usr/lib/systemd/`, `/private/etc/`, `/private/var/`
- Block `skills/.hub/index-cache` (prompt injection defense)

### 3.3 Path Traversal Hardening
- `validate_within_dir()` using `Path.resolve()` + `relative_to()`
- `has_traversal_component()` quick check for `..` parts
- **TOCTOU mitigation**: resolve and re-check immediately before open()
- **Symlink escape detection**: resolve symlinks; flag those pointing outside root
- **FIXED**: `_redirect_path` now uses `Path.relative_to()` instead of `str.startswith()` (Windows-safe, case-sensitive)
- **FIXED**: traversal detected → raise PermissionError (not fallback to original path)
- **NEW**: null byte injection check (`\x00` in path)

### 3.4 Read Loop Detection
- Track `(path, offset, limit)` across consecutive reads
- Warn at 3 consecutive identical reads, block at 4
- Mtime dedup: skip re-read if mtime unchanged

### 3.5 Cross-Agent File Locking
- `file_state.lock_path()` serializes read-modify-write per path
- Uses `fcntl` on Unix, `msvcrt` on Windows
- Staleness check: warn if file modified externally between read and write

### 3.6 Integration into File Tools
- Wire into `ReadFileTool` and `WriteFileTool` via FileSafetyHook
- Return clear `PermissionError` messages with specific reason (denylist, blocklist, traversal)

---

## Phase 4: Env Sanitization + SSRF/URL Safety

### 4.1 Environment Sanitization (`vibe/tools/security/env_sanitizer.py`)
- **From OpenClaw**: Block PATH overrides from request-scoped env
- **From OpenClaw**: Dangerous env key blocking (prefix list: `SECRET`, `PASSWORD`, `TOKEN`, `API_KEY`)
- **From OpenClaw**: Shell wrapper env stripping — only locale/color/terminal vars passed to shell transports
- **From Hermes**: Strip Hermes-managed secrets via blocklist before spawning subprocesses
- **From OpenClaw**: 32KB env value limit
- **From OpenClaw**: Base64-encoded credential detection

### 4.2 SSRF / URL Safety (`vibe/tools/security/url_safety.py`)
- **From Hermes**: Blocked hostnames: `metadata.google.internal`, `metadata.goog`
- **From Hermes**: Blocked IPs: `169.254.169.254`, `169.254.170.2`, `169.254.169.253`, `fd00:ec2::254`, `100.100.100.200`
- **From Hermes**: Blocked networks: `169.254.0.0/16` (link-local), `100.64.0.0/10` (CGNAT)
- **From Hermes**: Fail-closed DNS — block on DNS resolution errors
- Config toggle: `security.allow_private_urls` (default false)
- Redirect re-validation: re-check redirect targets after following

---

## Phase 5: Secret Redaction (`vibe/tools/security/redaction.py`)

### 5.1 Secret Pattern Redaction
- 40+ regex patterns: `sk-`, `ghp_`, GitHub tokens, Slack tokens, Google API keys, AWS credentials, Stripe keys, JWT patterns
- URL query redaction: mask `access_token`, `code`, `api_key`; strip userinfo from URLs
- Discord/PII redaction: mentions, E.164 phone numbers
- `redact_sensitive_text()` utility

### 5.2 Redacting Output
- Redact tool arguments BEFORE passing to hooks (prevent logging secrets)
- Redact tool results BEFORE appending to LLM context window (prevent memorization)
- Redact all audit log entries

### 5.3 Integration
- SecretRedactionHook at POST_EXECUTE stage
- Audit logger applies redaction automatically

---

## Phase 6: Smart Approver (LLM Review)

### 6.1 Design Constraints (addressing review feedback)
- **P1, not P0**: Build after core blocking patterns + human approval + audit log
- **Strict timeout**: 5s max for LLM call; timeout → escalate to human (fail-closed)
- **Fail-closed on LLM failure**: any API error → escalate (not auto-approve)
- **Robust parser**: accept APPROVE/DENY/ESCALATE case-insensitively; any other output → escalate
- **Local model warning**: if using Ollama/local model, add warning that temperature=0 may not be honored

### 6.2 Prompt Template
Same as Hermes (proven effective):
```
You are a security reviewer for an AI coding agent...
Respond with exactly one word: APPROVE, DENY, or ESCALATE
```

### 6.3 Integration
- SmartApprover is a standalone class called by DangerousPatternHook when pattern severity=warning and approval_mode=smart
- Returns `HookOutcome` with appropriate severity

---

## Phase 7: Checkpoints / Rollback (`vibe/tools/security/checkpoints.py`)

### 7.1 Shadow Git Repos
- Before file-mutating operations, take git snapshot
- Shadow repo under `~/.vibe/checkpoints/{workspace_hash}/`
- **Batched**: snapshot at start of turn, not per-write (addressing Gemini performance concern)
- No `.git` state leaks into user's project

### 7.2 Rollback
- `/rollback <N>` — restore to Nth checkpoint
- `/rollback <N> <file>` — single-file restore
- Prune to `max_snapshots` (default 50)

---

## Phase 8: Skills Guard + Sub-agent Isolation (Future)

### 8.1 Skills Guard (`vibe/tools/security/skills_guard.py`)
- Static analysis scanner with 80+ regex patterns (from Hermes)
- Invisible unicode detection (16 zero-width characters)
- Structural limits: max 50 files, 1MB total, 256KB per file
- Binary detection: flag `.exe`, `.dll`, `.so`, `.dylib`, `.bin`
- Symlink escape detection
- Trust levels: `builtin` > `trusted` > `community` > `agent-created`

### 8.2 Sub-agent Isolation (from Hermes)
- Restricted toolsets for children (intersection of parent's tools minus delegation-blocked)
- Independent IterationBudget (capped at config value)
- Hard timeout (default 600s) with interrupt on exceed
- Approval callback injection (non-interactive default deny)
- Heartbeat thread to keep parent alive

---

## Implementation Order (Revised)

| Phase | Task | Files | Est. Effort |
|-------|------|-------|-------------|
| 0.1 | Security config section | `config.py` | 0.5 day |
| 0.2 | Audit logging framework | `security/audit.py` | 0.5 day |
| 0.3 | Permission auditing | `security/permission_audit.py` | 0.5 day |
| 1.1 | HookOutcome severity levels | `constraints.py` | 0.5 day |
| 1.2 | Hook execution rules | `constraints.py` | 0.5 day |
| 1.3 | New built-in hooks (stubs) | `constraints.py` | 0.5 day |
| 2.1 | Pattern engine extraction | `security/patterns.py` | 1 day |
| 2.2 | Human approval system | `security/human_approval.py` | 1 day |
| 2.3 | Durable approval store | `security/approval_store.py` | 0.5 day |
| 2.4 | BashTool decoupling | `bash.py` | 0.5 day |
| 3.1 | Write denylist | `security/file_safety.py` | 0.5 day |
| 3.2 | Read blocklist | `security/file_safety.py` | 0.5 day |
| 3.3 | Path traversal hardening | `security/file_safety.py`, `file.py` | 0.5 day |
| 3.4 | Read loop detection | `security/file_safety.py` | 0.5 day |
| 3.5 | Cross-agent locking | `security/file_safety.py` | 0.5 day |
| 4.1 | Env sanitization | `security/env_sanitizer.py` | 0.5 day |
| 4.2 | SSRF/URL safety | `security/url_safety.py` | 0.5 day |
| 5.1 | Secret redaction | `security/redaction.py` | 0.5 day |
| 6.1 | Smart approver | `security/approver.py` | 1 day |
| 7.1 | Checkpoints | `security/checkpoints.py` | 1 day |

**Total: ~12 days** (was 8; increased due to additional layers and architectural changes)

---

## Testing Strategy

1. **Unit tests** for each security module (patterns, approver, store, file safety, redaction, url safety)
2. **Integration tests** for HookPipeline with all hooks enabled
3. **False positive tests** — ensure benign commands aren't blocked (100+ test cases from Hermes)
4. **Attack simulation tests** — attempt bypasses of each defense layer (encoding, path manipulation, symlink escape)
5. **Fail-closed tests** — simulate component failures, verify default-deny behavior
6. **Config migration test** — existing configs without security section load correctly with defaults
7. **Concurrency tests** — multiple agents accessing same file, approval store concurrent writes

---

## References

- Hermes: `tools/terminal_tool.py`, `tools/approval.py`, `agent/file_safety.py`, `agent/redact.py`, `tools/checkpoint_manager.py`, `tools/url_safety.py`, `tools/skills_guard.py`
- OpenClaw: `src/security/audit.ts`, `src/exec/security.ts`, `src/sandbox/docker.ts`, `src/config/security.ts`
- Vibe-agent: `vibe/tools/bash.py`, `vibe/tools/file.py`, `vibe/harness/constraints.py`, `vibe/core/config.py`
