# Security Execution Control Enhancement Plan for Vibe-Agent

> Based on analysis of Hermes Agent and OpenClaw security architectures.
> Date: 2026-04-25

---

## Executive Summary

Vibe-agent currently has a **minimal security posture**: basic dangerous-pattern regexes in `bash.py`, shell-metacharacter rejection, file path jailing, and a hook pipeline with two rudimentary hooks. There is **no approval system**, **no smart review**, **no secret redaction**, **no sandbox backends**, and **no audit/logging** of security events.

This plan implements a **defense-in-depth security execution control system** across 6 phases, borrowing the best patterns from Hermes (approval modes, smart LLM review, secret redaction, checkpointing) and OpenClaw (policy model, durable approvals, fail-closed behaviors, inline eval detection).

---

## Phase 1: Command Security Enhancement (bash.py + new security module)

### 1.1 Dangerous Pattern Engine (`vibe/tools/security/patterns.py`)
- Extract patterns from hardcoded `bash.py` into a configurable, extensible engine
- Add ~30 additional patterns from Hermes (git reset --hard, hermes gateway stop, .env overwrite, chmod 666, mkfs, SQL DROP/DELETE without WHERE, pkill hermes, etc.)
- Add OpenClaw patterns: inline eval detection (`-c`, `-e`, `--eval`, `-p`, `--print` across python/node/ruby/perl/php/lua/awk), wrapper detection (env/nice/timeout vs sudo/doas/chrt/ionice), npm/npx CVE mitigation
- Command normalization pipeline: strip ANSI, null bytes, Unicode NFKC, collapse whitespace
- Pattern severity levels: `critical` (auto-block), `warning` (flag for review), `info` (log only)

### 1.2 Smart Approval with Auxiliary LLM (`vibe/tools/security/approver.py`)
- When a command matches a `warning`-severity pattern, send to a lightweight LLM review
- Prompt template (from Hermes): "You are a security reviewer... APPROVE/DENY/ESCALATE"
- Temperature=0, max_tokens=16
- Three outcomes: `approve` (auto-execute), `deny` (block with explanation), `escalate` (require human approval)
- Configurable: `security.approval_mode = "manual" | "smart" | "auto"`

### 1.3 Human Approval System (`vibe/tools/security/human_approval.py`)
- CLI mode: `prompt_toolkit`-style approval UI with timeout (60s)
- Choices: `once` | `session` | `always` | `deny` | `view` (show command details)
- Gateway mode (future): thread blocks on `threading.Event`, user sends `/approve` or `/deny`
- YOLO bypass: `_session_yolo` dict + `VIBE_YOLO_MODE` env var for temporary bypass

### 1.4 Durable Approval Store (`vibe/tools/security/approval_store.py`)
- JSON file at `~/.vibe/exec-approvals.json` with `0o600` permissions
- Atomic write (temp+fsync+rename)
- Two approval types:
  - `=command:<sha256>` — exact command text hash
  - `=pattern:<pattern_id>` — all commands matching this pattern
- Stricter-wins policy: host settings can only make execution stricter
- Symlink rejection in approvals path

### 1.5 BashTool Integration
- Wire all new components into `BashTool.execute()`:
  1. Normalize command
  2. Check durable approval store (fast path)
  3. Run pattern engine
  4. If critical → block immediately
  5. If warning + smart mode → LLM review
  6. If warning + manual mode → human approval
  7. If auto mode → execute (with logging)
  8. Execute via existing `create_subprocess_exec`
  9. Log security event

---

## Phase 2: File Safety & Path Security (`vibe/tools/security/file_safety.py`)

### 2.1 Write Denylist
- Block writes to: `~/.ssh/authorized_keys`, `id_rsa`, `id_ed25519`, `~/.env`, `~/.bashrc`, `~/.netrc`, `/etc/sudoers`, `/etc/passwd`, `/etc/shadow`
- Block write prefixes: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `/etc/sudoers.d`, `/etc/systemd`, `~/.docker`, `~/.azure`, `~/.config/gh`
- Configurable `VIBE_WRITE_SAFE_ROOT` env restriction

### 2.2 Read Blocklist
- Block reads of: `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/stdin`, `/dev/tty`, `/dev/stdout`, `/dev/stderr`
- Block read prefixes: `/etc/`, `/boot/`, `/usr/lib/systemd/`, `/private/etc/`, `/private/var/`
- Block `skills/.hub/index-cache` (prompt injection defense)

### 2.3 Path Traversal Hardening
- `validate_within_dir()` using `Path.resolve()` + `relative_to()`
- `has_traversal_component()` quick check for `..` parts
- Symlink escape detection: resolve and re-check against root

### 2.4 Read Loop Detection
- Track `(path, offset, limit)` across consecutive reads
- Warn at 3 consecutive identical reads, block at 4
- Mtime dedup: skip re-read if mtime unchanged

### 2.5 Cross-Agent File Locking
- `file_state.lock_path()` serializes read-modify-write per path
- Staleness check: warn if file modified externally between read and write

### 2.6 Integration into File Tools
- Wire into `ReadFileTool` and `WriteFileTool`
- Add `root_dir` validation with symlink escape detection
- Return clear `PermissionError` messages

---

## Phase 3: Secret Redaction & Audit Logging (`vibe/tools/security/redaction.py` + `audit.py`)

### 3.1 Secret Pattern Redaction
- 40+ regex patterns: `sk-`, `ghp_`, GitHub tokens, Slack tokens, Google API keys, AWS credentials, Stripe keys, JWT patterns, etc.
- URL query redaction: mask `access_token`, `code`, `api_key`; strip userinfo from URLs
- Discord/PII redaction: mentions, E.164 phone numbers
- `redact_sensitive_text()` utility function

### 3.2 Redacting Logger Formatter
- `RedactingFormatter` applies redaction to all log records automatically
- Integrate with existing `setup_session_logger()`

### 3.3 Security Audit Log
- Structured security event log at `~/.vibe/logs/security.log`
- Events: `command_blocked`, `command_approved`, `command_flagged`, `file_write_denied`, `file_read_denied`, `path_traversal_attempt`, `secret_redacted`, `approval_granted`, `approval_revoked`
- Include: timestamp, event type, command/pattern, user decision, LLM decision, session ID

### 3.4 Audit Scanner (future-ready)
- Framework for continuous security audit checks
- Severity levels: `critical`, `warn`, `info`
- Examples: world-writable state dir, config without auth, open channels with exec tools

---

## Phase 4: Hook Pipeline Enhancement (`vibe/harness/constraints.py`)

### 4.1 New Built-in Hooks
- `dangerous_command_hook`: integrates pattern engine + approval flow
- `file_safety_hook`: integrates write denylist + read blocklist
- `secret_redaction_hook`: redacts tool arguments and results
- `audit_log_hook`: logs all tool executions
- `path_traversal_hook`: validates all path arguments

### 4.2 Hook Configuration
- Config-driven hook enablement in `~/.vibe/config.yaml`:
  ```yaml
  security:
    hooks:
      dangerous_command: true
      file_safety: true
      secret_redaction: true
      audit_log: true
      path_traversal: true
  ```

### 4.3 Hook Severity Levels
- Each hook returns `HookOutcome` with severity: `block` (deny), `warn` (allow but log), `allow`
- Multiple hooks can chain: first `block` wins, all `warn` accumulate

---

## Phase 5: Checkpoint / Rollback System (`vibe/tools/security/checkpoints.py`)

### 5.1 Shadow Git Repos
- Before file-mutating operations (`write_file`, `patch`), take transparent git snapshot
- Shadow repo under `~/.vibe/checkpoints/{workspace_hash}/`
- No `.git` state leaks into user's project

### 5.2 Rollback Commands
- `/rollback <N>` — restore to Nth previous checkpoint
- `/rollback <N> <file>` — single-file restore
- Prune to `max_snapshots` (default 50)

### 5.3 Integration
- Hook into `WriteFileTool` and new `PatchTool` (if added)
- Auto-snapshot before any write operation

---

## Phase 6: Config-Level Security Defaults (`vibe/core/config.py`)

### 6.1 Security Config Section
```yaml
security:
  approval_mode: "smart"          # manual | smart | auto
  dangerous_patterns_enabled: true
  secret_redaction: true
  audit_logging: true
  file_safety:
    write_denylist_enabled: true
    read_blocklist_enabled: true
    safe_root: null               # optional VIBE_WRITE_SAFE_ROOT
  checkpoints:
    enabled: true
    max_snapshots: 50
  sandbox:
    backend: "local"              # local | docker | ssh (future)
    auto_approve_in_sandbox: true  # sandbox is the boundary
```

### 6.2 Validation
- Config validation on load: reject invalid approval_mode, warn on auto mode
- Migration path: add security section to existing configs

---

## Implementation Order

| Phase | Priority | Files Touched | Est. Effort |
|-------|----------|---------------|-------------|
| 1.1 Pattern Engine | P0 | `vibe/tools/security/patterns.py`, `bash.py` | 1 day |
| 1.2 Smart Approver | P0 | `vibe/tools/security/approver.py` | 1 day |
| 1.3 Human Approval | P0 | `vibe/tools/security/human_approval.py` | 1 day |
| 1.4 Approval Store | P0 | `vibe/tools/security/approval_store.py` | 0.5 day |
| 1.5 BashTool Integration | P0 | `bash.py` | 0.5 day |
| 2.1-2.6 File Safety | P1 | `vibe/tools/security/file_safety.py`, `file.py` | 1 day |
| 3.1-3.4 Redaction + Audit | P1 | `vibe/tools/security/redaction.py`, `audit.py` | 1 day |
| 4.1-4.3 Hook Enhancement | P1 | `constraints.py` | 0.5 day |
| 5.1-5.3 Checkpoints | P2 | `vibe/tools/security/checkpoints.py` | 1 day |
| 6.1-6.2 Config | P1 | `config.py` | 0.5 day |

**Total: ~8 days of implementation**

---

## Testing Strategy

1. **Unit tests** for each security module (patterns, approver, store, file safety, redaction)
2. **Integration tests** for BashTool with security pipeline enabled
3. **False positive tests** — ensure benign commands aren't blocked (from Hermes test suite patterns)
4. **Attack simulation tests** — attempt bypasses of each defense layer
5. **Config migration test** — existing configs without security section load correctly

---

## References

- Hermes: `tools/terminal_tool.py`, `tools/approval.py`, `agent/file_safety.py`, `agent/redact.py`, `tools/checkpoint_manager.py`
- OpenClaw: `src/security/audit.ts`, `src/exec/security.ts`, `src/sandbox/docker.ts`
- Vibe-agent: `vibe/tools/bash.py`, `vibe/tools/file.py`, `vibe/harness/constraints.py`, `vibe/core/config.py`
