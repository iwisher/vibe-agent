# Design Spec: Smart Approval Scoping (Approach 1)

**Date**: 2026-05-03
**Status**: Draft
**Topic**: Improving the human approval system to reduce repetitive prompts for safe commands.

## 1. Problem Statement
The current `vibe-agent` human approval system is too strict for common read-only operations. Even if a user selects "Always" for a command like `ls`, minor variations in parameters (e.g., `ls -la` vs `ls -R`) or folder sub-paths trigger a new security prompt. This creates "approval fatigue" and degrades the user experience.

## 2. Goals
- Reduce repetitive prompts for "Safe" read-only commands.
- Implement folder-tree scoping (auto-approve in sub-folders).
- Maintain global security by requiring new approvals when moving out of the approved folder tree.
- Persist approvals globally in `~/.vibe/approvals.json`.

## 3. Architecture

### 3.1 `ApprovalStore`
A new utility class responsible for persisting and querying approvals.

- **Storage**: `~/.vibe/approvals.json`
- **Entry Types**:
    - `scoped_base_cmd`: Approves a base binary (e.g., `ls`) within a specific `root_path`.
    - `exact_match`: Approves a specific full command string (default for non-safe commands).

### 3.2 Safe Command Heuristic
The system will identify "Safe" commands that are eligible for hierarchy-based scoping. For multipart commands like `git`, the system will check the first argument (subcommand) against a whitelist.

**Default Safe List**:
- Navigation/Inspection: `ls`, `find`, `pwd`, `du`, `df`, `stat`
- Reading: `cat`, `head`, `tail`, `grep`, `sort`, `uniq`, `wc`
- Version Control (Read-only): `git` (subcommands: `status`, `log`, `diff`, `branch`, `show`, `remote`)
- Processing: `python -m json.tool`, `jq`

### 3.3 Path Resolution
The system must resolve paths mentioned in commands or use the `cwd` (Current Working Directory) to verify they fall within an approved `root_path`.

## 4. Components

### 4.1 `vibe/tools/security/approval_store.py` [NEW]
- `check_approval(command_line: str, cwd: str) -> bool`
- `add_approval(command_line: str, cwd: str, scope: str = "auto")`
- **Chain Verification**: Splitting command lines by `|`, `&&`, `;`, `>`, and `>>` to verify each segment independently.
- **Redirect Validation**: Ensuring output redirection targets are within the approved path hierarchy.

### 4.2 `vibe/tools/security/human_approval.py` [MODIFY]
- Integrate `ApprovalStore`.
- Update `request_approval` to consult the store before prompting.
- Update `ALWAYS` choice logic to determine if the command is "Safe" and store it as a scoped approval if so.

### 4.3 `vibe/core/coordinators.py` [MODIFY]
- Update `SecurityCoordinator` to pass `cwd` and `pattern_id` (if available) to the `request_approval` call.

### 4.4 `vibe/tools/bash.py` [MODIFY]
- **Shell Support**: Add an option to use `asyncio.create_subprocess_shell` when shell metacharacters are detected and the command has been approved.
- Remove the "Hard Block" on metacharacters, delegating the safety decision to the `SecurityCoordinator`.

## 5. Chain Verification Logic Details
When a command string like `find . -name "*.py" | grep "TODO" > results.txt` is evaluated:
1.  **Split**: The string is divided into units: `find . -name "*.py"`, `grep "TODO"`, and `results.txt`.
2.  **Validate Units**:
    - `find` is on `SAFE_COMMANDS` list.
    - `grep` is on `SAFE_COMMANDS` list.
    - `results.txt` is checked against the approved `root_path`.
3.  **Result**: If all units pass, the entire chain is auto-approved.

## 6. Security Considerations
- **Non-Safe Commands**: Commands like `rm`, `mv`, `sudo`, or scripts (`./run.sh`) will NEVER use scoped base-command matching. They will continue to use exact-match "Always" approvals.
- **Shell Injections**: By verifying every segment of a pipe, we prevent injection of dangerous commands (e.g., `ls | rm`) even if the first command is safe.
- **Path Traversal**: Approved paths must be resolved to absolute paths to prevent `../` traversal tricks bypassing scoping.
- **Fail-Closed**: If the JSON store is corrupted or inaccessible, the system defaults to prompting the user.
