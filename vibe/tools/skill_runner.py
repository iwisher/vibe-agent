"""SkillRunnerTool — execute TOML skills via ToolSystem."""

import re
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

            command = self._substitute_vars(step.command, variables)

            # Detect if shell mode is needed
            use_shell = self._needs_shell(command)

            try:
                result = await self._tool_system.execute_tool(
                    step.tool,
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

    @staticmethod
    def _substitute_vars(command: str, variables: dict[str, Any]) -> str:
        """Substitute {{var}} and ${VAR} / ${VAR:-default} patterns.

        Only substitutes keys present in the variables dict.
        Unresolved {{var}} placeholders raise ValueError.
        """
        result = command

        # Jinja2-style {{var}} with optional whitespace (spacing-insensitive)
        for key, value in variables.items():
            pattern = r"\{\{\s*" + re.escape(key) + r"\s*\}\}"
            result = re.sub(pattern, str(value), result)

        # Shell-style ${VAR} and ${VAR:-default} — only for declared vars
        def replace_env(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            if var_name in variables:
                return str(variables[var_name])
            if default is not None:
                return default
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

        return True
