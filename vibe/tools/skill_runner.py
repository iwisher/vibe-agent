"""SkillRunnerTool — execute TOML skills via ToolSystem."""

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from vibe.harness.skills.typed_vars import SkillSchema
from vibe.tools.tool_system import Tool, ToolResult, ToolSystem


class SkillRunnerTool(Tool):
    """Execute an installed executable skill by ID.

    The LLM calls run_skill(skill_id="...", variables={...}) to trigger
    a multi-step workflow defined in a TOML skill file.
    """

    def __init__(self, executable_skills: dict[str, Any], tool_system: ToolSystem):
        skill_list = ", ".join(executable_skills.keys()) if executable_skills else "none installed"
        super().__init__(
            name="run_skill",
            description=(
                "Execute an installed executable skill by its ID. "
                "Available skills: " + skill_list + ". "
                "Pass the skill_id and any required variables."
            ),
        )
        self._executable_skills = executable_skills
        self._tool_system = tool_system

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The ID of the skill to execute",
                },
                "variables": {
                    "type": "object",
                    "description": "Variables to substitute into skill steps",
                },
            },
            "required": ["skill_id"],
        }

    async def execute(
        self,
        *,
        skill_id: str = "",
        variables: dict[str, Any] | None = None,
        **kwargs,
    ) -> ToolResult:
        variables = variables or {}

        if skill_id not in self._executable_skills:
            return ToolResult(
                success=False,
                content=None,
                error=(
                    f"Skill '{skill_id}' not found. "
                    f"Available: {list(self._executable_skills.keys())}"
                ),
            )

        skill = self._executable_skills[skill_id]

        if not skill.steps:
            return ToolResult(
                success=False,
                content=None,
                error=f"Skill '{skill_id}' has no executable steps. It may be a prompt skill.",
            )

        # Build SkillSchema from skill.variables (list[dict])
        props = {}
        required = []
        for var in getattr(skill, "variables", []):
            name = var.get("name")
            if not name:
                continue
            prop = {
                "type": var.get("type", "string"),
                "description": var.get("description", ""),
            }
            if "default" in var:
                prop["default"] = var["default"]
            if "enum" in var:
                prop["enum"] = var["enum"]
            if "minimum" in var:
                prop["minimum"] = var["minimum"]
            if "maximum" in var:
                prop["maximum"] = var["maximum"]
            if "pattern" in var:
                prop["pattern"] = var["pattern"]
            props[name] = prop
            if var.get("required", False):
                required.append(name)

        schema = SkillSchema.from_dict({"properties": props, "required": required})
        coerced_vars, errors = schema.apply(variables)
        if errors:
            return ToolResult(
                success=False,
                content=None,
                error=f"Variable validation failed for skill '{skill_id}': {', '.join(errors)}",
            )
        variables = coerced_vars

        step_results = []
        for step in skill.steps:
            # Circular check to prevent infinite nested run_skill loops
            if step.tool == "run_skill":
                return ToolResult(
                    success=False,
                    content=None,
                    error=(
                        f"Circular execution blocked: step '{step.id}' cannot call "
                        f"'run_skill' recursively."
                    ),
                )

            if getattr(step, "script", None):
                # Deterministic script step: the runner builds a fully-quoted argv
                # itself; step.command is only the argument template. The result
                # contains no unquoted shell metacharacters, so BashTool runs it in
                # exec mode without requiring shell approval.
                command, error = self._build_script_argv(skill, step, variables)
                if error is not None:
                    return ToolResult(success=False, content=None, error=error)
                tool_name = "bash"
                use_shell = False
            else:
                command = self._substitute_vars(step.command, variables)
                tool_name = step.tool
                # Detect if shell mode is needed
                use_shell = self._needs_shell(command)

            try:
                result = await self._tool_system.execute_tool(
                    tool_name,
                    command=command,
                    use_shell=use_shell,
                )
            except Exception as e:
                result = ToolResult(
                    success=False,
                    content=None,
                    error=f"Tool execution failed: {e}",
                )

            verified = self._verify_step(result, step.verification, command, variables)

            step_results.append(
                {
                    "step_id": step.id,
                    "success": result.success and verified,
                    "output": result.content,
                    "error": result.error,
                }
            )

            if not (result.success and verified):
                break

        all_success = all(sr["success"] for sr in step_results)
        output_lines = []
        for sr in step_results:
            status = "OK" if sr["success"] else "FAIL"
            output_lines.append(f"[{status}] {sr['step_id']}: {sr['output'] or ''}")
            if sr["error"]:
                output_lines.append(f"  Error: {sr['error']}")

        return ToolResult(
            success=all_success,
            content="\n".join(output_lines),
            error=None if all_success else step_results[-1].get("error"),
        )

    def _build_script_argv(
        self, skill: Any, step: Any, variables: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """Resolve and render a deterministic script step into a safe argv string.

        The script must live under the skill's scripts/ directory. Every substituted
        variable value is shlex.quote()d, and the interpreter and script path are
        quoted as well, so the resulting argv contains no unquoted shell
        metacharacters and runs via BashTool in exec mode.

        Returns (argv_string, None) on success or (None, error_message).
        """
        skill_dir = getattr(skill, "skill_dir", None)
        if not skill_dir:
            return None, (
                f"Step '{step.id}' declares script '{step.script}' but skill "
                f"'{getattr(skill, 'id', '?')}' has no skill_dir; script steps require "
                "the skill to be loaded from a directory."
            )

        # Jail: the resolved script path must stay under <skill_dir>/scripts.
        base = Path(skill_dir).resolve()
        scripts_root = (base / "scripts").resolve()
        script_path = (base / step.script).resolve()
        if script_path == scripts_root or scripts_root not in script_path.parents:
            return None, (
                f"Step '{step.id}': script '{step.script}' is outside the skill's "
                "scripts/ directory (use a relative path under scripts/)."
            )
        if not script_path.is_file():
            return None, f"Step '{step.id}': script not found: {script_path}"

        interpreter = getattr(step, "interpreter", None)
        if not interpreter:
            suffix = script_path.suffix.lower()
            if suffix == ".py":
                interpreter = sys.executable
            elif suffix == ".sh":
                interpreter = "bash"
            else:
                return None, (
                    f"Step '{step.id}': cannot infer interpreter for '{step.script}'; "
                    'set interpreter = "..." on the step.'
                )

        try:
            args = self._substitute_vars(step.command, variables, quote=True).strip()
        except ValueError as e:
            return None, f"Step '{step.id}': {e}"

        parts = [shlex.quote(interpreter), shlex.quote(str(script_path))]
        if args:
            parts.append(args)
        return " ".join(parts), None

    @staticmethod
    def _substitute_vars(command: str, variables: dict[str, Any], quote: bool = False) -> str:
        """Substitute {{var}} and ${VAR} / ${VAR:-default} patterns.

        Only substitutes keys present in the variables dict.
        Unresolved {{var}} placeholders raise ValueError.
        When quote=True, substituted values are shlex.quote()d so they arrive at the
        process as single inert argv tokens (used for script-step arguments).
        """

        def render(value: Any) -> str:
            text = str(value)
            return shlex.quote(text) if quote else text

        result = command

        # Jinja2-style {{var}} with optional whitespace (spacing-insensitive)
        for key, value in variables.items():
            pattern = r"\{\{\s*" + re.escape(key) + r"\s*\}\}"
            result = re.sub(pattern, lambda m: render(value), result)

        # Shell-style ${VAR} and ${VAR:-default} — only for declared vars
        def replace_env(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            if var_name in variables:
                return render(variables[var_name])
            if default is not None:
                return render(default)
            # Not in variables and no default — leave as-is (may be shell env var)
            return match.group(0)

        result = re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", replace_env, result)

        # Check for unresolved {{var}} placeholders spacing-insensitively
        unresolved = re.findall(r"\{\{\s*[^}]+\s*\}\}", result)
        if unresolved:
            raise ValueError(f"Unresolved variables in command: {unresolved}")

        return result

    @staticmethod
    def _needs_shell(command: str) -> bool:
        """Detect if command needs shell mode based on metacharacters."""
        shell_metacharacters = "|&;><$`\"'"
        shell_builtins = {"exit", "cd", "export", "unset", "alias", "source", ".", "eval", "exec"}
        stripped = command.strip()
        if not stripped:
            return False
        first_word = stripped.split()[0]
        has_meta = any(c in command for c in shell_metacharacters)
        is_builtin = first_word in shell_builtins
        return has_meta or is_builtin

    @staticmethod
    def _verify_step(
        result: ToolResult,
        verification: Any,
        command: str,
        variables: dict[str, Any] | None = None,
    ) -> bool:
        """Verify step output against criteria."""
        if verification.exit_code is not None:
            actual = result.metadata.get("exit_code", 0 if result.success else 1)
            if actual != verification.exit_code:
                return False

        if verification.output_contains:
            expected_output = verification.output_contains
            if variables:
                expected_output = SkillRunnerTool._substitute_vars(expected_output, variables)
            output = str(result.content or "")
            if expected_output not in output:
                return False

        if verification.file_exists:
            expected_file = verification.file_exists
            if variables:
                expected_file = SkillRunnerTool._substitute_vars(expected_file, variables)
            # Resolve relative to command context or CWD
            path = Path(expected_file)
            if not path.exists():
                return False

        json_keys = getattr(verification, "json_has_keys", None)
        if json_keys and isinstance(json_keys, (list, tuple)):
            data = SkillRunnerTool._extract_json_object(str(result.content or ""))
            if not isinstance(data, dict):
                return False
            if any(key not in data for key in json_keys):
                return False

        return True

    @staticmethod
    def _extract_json_object(text: str) -> Any:
        """Parse a JSON object from tool output.

        BashTool may append a "[stderr]" section to the content, so if a full
        parse fails, fall back to decoding from the first '{' in the output.
        Returns None when no JSON value can be decoded.
        """
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            pass
        start = text.find("{")
        if start < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj
        except ValueError:
            return None
