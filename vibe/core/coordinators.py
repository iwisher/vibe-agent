"""Coordinator submodules extracted from QueryLoop.

Each coordinator owns a distinct responsibility:
- ToolExecutor: tool call execution with hooks and MCP fallback
- FeedbackCoordinator: feedback engine integration and retry logic
- CompactionCoordinator: context compaction before LLM calls
- SecurityCoordinator: 5-layer defense before tool execution

This separation allows QueryLoop.run() to remain a thin orchestrator
(< 40 lines) and makes each component independently testable.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vibe.core.context_compactor import CompactionResult, ContextCompactor
from vibe.harness.constraints import HookPipeline
from vibe.harness.feedback import FeedbackEngine, FeedbackStatus
from vibe.tools._utils import extract_tool_call_arguments, extract_tool_call_name
from vibe.tools.mcp_bridge import MCPBridge
from vibe.tools.tool_system import ToolResult, ToolSystem


@dataclass
class SecurityCheckResult:
    """Result of a 5-layer security evaluation."""

    allowed: bool
    reason: str = ""
    layer: str | None = None
    checkpoint_id: str | None = None
    risk_level: str | None = None


class ToolExecutor:
    """Executes tool calls with pre/post hooks and MCP fallback."""

    def __init__(
        self,
        tool_system: ToolSystem,
        hook_pipeline: HookPipeline,
        mcp_bridge: MCPBridge | None = None,
    ):
        self.tools = tool_system
        self.hook_pipeline = hook_pipeline
        self.mcp_bridge = mcp_bridge
        self._handlers: dict[str, Callable] = {}

    def register_handler(self, tool_name: str, handler: Callable) -> None:
        self._handlers[tool_name] = handler

    def select_tools(self, all_schemas: list[dict], selected_names: list[str] | None) -> list[dict]:
        """Filter schemas by planner selection, with safety fallback to all."""
        if not selected_names:
            return all_schemas
        filtered = [
            t for t in all_schemas
            if t.get("function", {}).get("name") in selected_names
        ]
        return filtered if filtered else all_schemas

    async def execute(self, tool_calls: list) -> list[ToolResult]:
        """Execute a batch of tool calls with hooks and fallback."""
        results = []
        for call in tool_calls:
            try:
                if isinstance(call, dict):
                    call_name = extract_tool_call_name(call)
                    arguments = extract_tool_call_arguments(call)
                else:
                    call_name = getattr(call, "name", None)
                    arguments = getattr(call, "arguments", {})

                # Pre-hooks
                pre_outcome = self.hook_pipeline.run_pre_hooks(call_name, arguments)
                if not pre_outcome.allow:
                    results.append(
                        ToolResult(
                            success=False,
                            content=None,
                            error=f"Hook veto: {pre_outcome.reason}",
                        )
                    )
                    continue

                exec_args = pre_outcome.modified_arguments or arguments

                if call_name in self._handlers:
                    result = await self._handlers[call_name](exec_args)
                else:
                    result = await self.tools.execute_tool(call_name, **exec_args)
                    if (
                        not result.success
                        and "not found" in (result.error or "").lower()
                        and self.mcp_bridge
                    ):
                        result = await self.mcp_bridge.execute_tool(call_name, **exec_args)

                # Post-hooks
                result = self.hook_pipeline.run_post_hooks(call_name, exec_args, result)
                results.append(result)
            except Exception as e:
                results.append(ToolResult(success=False, content=None, error=str(e)))
        return results


class FeedbackCoordinator:
    """Coordinates feedback engine evaluation and retry hints."""

    def __init__(
        self,
        feedback_engine: FeedbackEngine | None,
        threshold: float = 0.7,
        max_retries: int = 1,
    ):
        self.engine = feedback_engine
        self.threshold = threshold
        self.max_retries = max_retries
        self._retry_count = 0

    def reset(self) -> None:
        self._retry_count = 0

    async def evaluate(self, content: str) -> tuple[bool, str | None]:
        """Evaluate content and return (should_continue, fix_hint)."""
        if not self.engine or not content or self._retry_count >= self.max_retries:
            return False, None

        fb = await self.engine.self_verify(content)

        # Don't retry on engine or validation errors
        if fb.status in (FeedbackStatus.ENGINE_ERROR, FeedbackStatus.VALIDATION_ERROR):
            return False, None

        if fb.score >= self.threshold:
            return False, None

        self._retry_count += 1
        fb.status = FeedbackStatus.BELOW_THRESHOLD
        fix_hint = fb.suggested_fix or "Please improve your response."
        issues_text = "\n".join(f"- {i}" for i in fb.issues) if fb.issues else ""
        hint = (
            f"Feedback score {fb.score:.2f} below threshold "
            f"({self.threshold}). Issues:\n{issues_text}\n"
            f"Suggested fix: {fix_hint}"
        )
        return True, hint


class CompactionCoordinator:
    """Manages context compaction before LLM calls."""

    def __init__(self, compactor: ContextCompactor):
        self.compactor = compactor
        self.last_result: CompactionResult | None = None

    def should_compact(self, messages: list[dict]) -> bool:
        return self.compactor.should_compact(messages)

    async def compact(self, messages: list[dict]) -> tuple[list[dict], bool]:
        """Return (compacted_messages, was_compacted)."""
        if not self.should_compact(messages):
            self.last_result = None
            return messages, False
        result = await self.compactor.compact_async(messages)
        self.last_result = result
        return result.messages, result.was_compacted


class SecurityCoordinator:
    """5-layer defense coordinator for tool execution security.

    Layers:
    1. Pattern scanning (dangerous command detection)
    2. File safety (path jailing, denylist/blocklist)
    3. Human approval gates (interactive/strict/auto)
    4. Smart approver (LLM-based risk assessment)
    5. Checkpoints (rollback before destructive ops)
    """

    DESTRUCTIVE_TOOLS = {"bash", "shell", "write_file", "delete_file", "execute"}
    FILE_TOOLS = {"read_file", "write_file", "delete_file", "list_directory"}

    def __init__(
        self,
        config: Any,
        llm_client: Any | None = None,
        checkpoint_manager: Any | None = None,
    ):
        self.config = config
        self._pattern_engine: Any | None = None
        self._file_guard: Any | None = None
        self._human_approver: Any | None = None
        self._smart_approver: Any | None = None
        self._checkpoint_manager = checkpoint_manager
        self._init_layers(llm_client)

    def _init_layers(self, llm_client: Any | None) -> None:
        """Lazy-initialize security layers based on config."""
        # Layer 1: Pattern engine
        if getattr(self.config, "dangerous_patterns_enabled", True):
            from vibe.tools.security.patterns import PatternEngine
            self._pattern_engine = PatternEngine()

        # Layer 2: File safety guard
        safe_root = getattr(self.config.file_safety, "safe_root", None) if hasattr(self.config, "file_safety") else None
        if safe_root:
            from vibe.tools.security.file_safety import FileSafetyGuard
            self._file_guard = FileSafetyGuard(safe_root=Path(safe_root))

        # Layer 3: Human approval gates
        approval_mode = getattr(self.config, "approval_mode", "smart")
        mode_map = {"manual": "interactive", "smart": "interactive", "auto": "auto", "strict": "strict"}
        from vibe.tools.security.human_approval import ApprovalMode, HumanApprover
        mapped_mode = mode_map.get(approval_mode, "interactive")
        self._human_approver = HumanApprover(
            mode=ApprovalMode(mapped_mode),
            timeout_seconds=60,
        )

        # Layer 4: Smart approver (heuristics work without LLM client)
        if getattr(self.config, "smart_approver_enabled", True):
            from vibe.tools.security.smart_approver import SmartApprover
            self._smart_approver = SmartApprover(
                llm_client=llm_client,
                auto_mode=getattr(self.config, "is_auto_approve", lambda: False)(),
            )

    def evaluate_tool_call(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Evaluate a tool call through all 5 security layers.

        Returns SecurityCheckResult with allowed=True if the call passes all layers.
        """
        # Layer 1: Pattern scanning (for bash/shell commands)
        pattern_result = self._check_patterns(tool_name, tool_args)
        if not pattern_result.allowed:
            return pattern_result

        # Layer 2: File safety (for file tools)
        file_result = self._check_file_safety(tool_name, tool_args)
        if not file_result.allowed:
            return file_result

        # Layer 3: Human approval gates
        approval_result = self._check_approval(tool_name, tool_args)
        if not approval_result.allowed:
            return approval_result

        # Layer 4: Smart approver (LLM-based risk assessment)
        smart_result = self._check_smart_approver(tool_name, tool_args)
        if not smart_result.allowed:
            return smart_result

        # Layer 5: Checkpoints (create rollback point before destructive ops)
        checkpoint_result = self._check_checkpoint(tool_name, tool_args)
        return checkpoint_result

    def _check_patterns(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Layer 1: Scan commands for dangerous patterns."""
        if self._pattern_engine is None:
            return SecurityCheckResult(allowed=True)

        command = tool_args.get("command", "")
        if not command and "content" in tool_args:
            command = tool_args.get("content", "")
        if not isinstance(command, str) or not command:
            return SecurityCheckResult(allowed=True)

        matches = self._pattern_engine.scan(command)
        critical = [m for m in matches if m.severity.value == "critical"]
        if critical:
            return SecurityCheckResult(
                allowed=False,
                reason=f"Critical pattern detected: {critical[0].description}",
                layer="pattern_scan",
            )
        return SecurityCheckResult(allowed=True)

    def _check_file_safety(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Layer 2: Validate file paths against safety rules."""
        if self._file_guard is None:
            return SecurityCheckResult(allowed=True)

        if tool_name not in self.FILE_TOOLS:
            return SecurityCheckResult(allowed=True)

        path = tool_args.get("path", "")
        if not path:
            return SecurityCheckResult(allowed=True)

        try:
            if tool_name in {"write_file", "delete_file"}:
                self._file_guard.check_write(path)
            elif tool_name == "read_file":
                self._file_guard.check_read(path)
            return SecurityCheckResult(allowed=True)
        except Exception as exc:
            return SecurityCheckResult(
                allowed=False,
                reason=str(exc),
                layer="file_safety",
            )

    def _check_approval(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Layer 3: Human approval gates."""
        if self._human_approver is None:
            return SecurityCheckResult(allowed=True)

        is_auto = getattr(self.config, "is_auto_approve", lambda: False)()
        is_strict = getattr(self.config, "is_strict_mode", lambda: False)()
        if is_auto:
            return SecurityCheckResult(allowed=True)

        # Only flag destructive tools in interactive mode; strict blocks everything flagged
        if tool_name not in self.DESTRUCTIVE_TOOLS and not is_strict:
            return SecurityCheckResult(allowed=True)

        command = tool_args.get("command", "")
        if not isinstance(command, str):
            command = str(tool_args)

        # Detect if shell execution is needed
        has_shell = any(c in command for c in "|&;><$`")
        
        result = self._human_approver.request_approval(
            command=command,
            description=f"{tool_name} tool call",
            cwd=tool_args.get("cwd") or tool_args.get("path"),
        )
        if result.approved:
            if has_shell:
                tool_args["use_shell"] = True
            return SecurityCheckResult(allowed=True)
        
        return SecurityCheckResult(
            allowed=False,
            reason=result.reason or "Approval denied",
            layer="human_approval",
        )

    def _check_smart_approver(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Layer 4: LLM-based risk assessment."""
        if self._smart_approver is None:
            return SecurityCheckResult(allowed=True)
        if not getattr(self.config, "smart_approver_enabled", True):
            return SecurityCheckResult(allowed=True)

        assessment = self._smart_approver.assess_tool_call(tool_name, tool_args)
        if assessment.decision.value == "reject":
            return SecurityCheckResult(
                allowed=False,
                reason=f"Smart approver rejected: {assessment.reasoning}",
                layer="smart_approver",
                risk_level=assessment.risk_level.value,
            )
        return SecurityCheckResult(
            allowed=True,
            risk_level=assessment.risk_level.value,
        )

    def _check_checkpoint(self, tool_name: str, tool_args: dict[str, Any]) -> SecurityCheckResult:
        """Layer 5: Create checkpoint before destructive operations."""
        if self._checkpoint_manager is None:
            return SecurityCheckResult(allowed=True)
        if not getattr(self.config, "checkpoint_enabled", True):
            return SecurityCheckResult(allowed=True)
        if tool_name not in self.DESTRUCTIVE_TOOLS:
            return SecurityCheckResult(allowed=True)

        try:
            from vibe.tools.security.checkpoints import CheckpointType
            cp = self._checkpoint_manager.create(
                checkpoint_type=CheckpointType.COMMAND_EXECUTION,
                description=f"Before {tool_name}: {tool_args.get('command', '')}",
            )
            return SecurityCheckResult(allowed=True, checkpoint_id=cp.id)
        except Exception:
            # Fail open on checkpoint failure unless fail_closed is True
            if getattr(self.config, "fail_closed", False):
                return SecurityCheckResult(
                    allowed=False,
                    reason="Checkpoint creation failed (fail_closed=True)",
                    layer="checkpoint",
                )
            return SecurityCheckResult(allowed=True)
