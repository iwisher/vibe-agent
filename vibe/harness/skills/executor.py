"""Execute skill steps with variable substitution, delegating to BashTool."""
import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from vibe.tools.bash import BashTool

from .models import Skill, SkillStep


@dataclass
class StepResult:
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int = 0


class SkillExecutor:
    """Execute skill steps using BashTool for security."""

    def __init__(self, bash_tool: BashTool | None = None):
        self.bash_tool = bash_tool or BashTool()

    async def execute_step(
        self,
        step: SkillStep,
        variables: dict[str, str],
        skill_dir: Path | None = None,
    ) -> StepResult:
        # Substitute variables with shlex.quote
        command = step.command
        for key, value in variables.items():
            command = command.replace(f"{{{key}}}", shlex.quote(str(value)))

        # Handle {skill_dir}
        if skill_dir:
            command = command.replace("{skill_dir}", shlex.quote(str(skill_dir)))
        else:
            command = command.replace("{skill_dir}", shlex.quote(str(Path.cwd())))

        # Execute via BashTool (async, sandboxed, no shell=True)
        tool_result = await self.bash_tool.execute(command=command)

        output = tool_result.content or ""
        error = tool_result.error or ""
        exit_code = 0 if tool_result.success else 1

        # Verify
        verification = step.verification
        if verification.exit_code is not None and exit_code != verification.exit_code:
            return StepResult(
                success=False,
                output=output,
                error=f"Exit code {exit_code} != expected {verification.exit_code}",
                exit_code=exit_code,
            )

        if verification.output_contains and verification.output_contains not in output:
            return StepResult(
                success=False,
                output=output,
                error=f"Output does not contain '{verification.output_contains}'",
                exit_code=exit_code,
            )

        if verification.file_exists:
            file_path = Path(verification.file_exists)
            # Restrict to skill_dir to prevent filesystem probing
            if skill_dir and not file_path.is_relative_to(skill_dir):
                return StepResult(
                    success=False,
                    output=output,
                    error=f"File path must be within skill directory: {verification.file_exists}",
                    exit_code=exit_code,
                )
            if not file_path.exists():
                return StepResult(
                    success=False,
                    output=output,
                    error=f"File does not exist: {verification.file_exists}",
                    exit_code=exit_code,
                )

        if verification.json_has_keys:
            try:
                data = json.loads(output)
                missing = [k for k in verification.json_has_keys if k not in data]
                if missing:
                    return StepResult(
                        success=False,
                        output=output,
                        error=f"JSON missing keys: {missing}",
                        exit_code=exit_code,
                    )
            except json.JSONDecodeError:
                return StepResult(
                    success=False,
                    output=output,
                    error="Output is not valid JSON",
                    exit_code=exit_code,
                )

        return StepResult(success=True, output=output, exit_code=exit_code)

    async def execute_skill(
        self,
        skill: Skill,
        variables: dict[str, str],
        skill_dir: Path | None = None,
    ) -> list[StepResult]:
        """Execute all steps in a skill."""
        results = []
        for step in skill.steps:
            # Check condition
            if step.condition:
                if not self._evaluate_condition(step.condition, variables):
                    results.append(StepResult(success=True, output="Skipped (condition false)"))
                    continue

            result = await self.execute_step(step, variables, skill_dir)
            results.append(result)

            if not result.success:
                break

        return results

    def _evaluate_condition(self, condition: str, variables: dict[str, str]) -> bool:
        """Evaluate simple conditions like '{include_chart} == true'.
        
        Supports: ==, !=, and truthy checks.
        """
        condition = condition.strip()
        
        # Handle != comparison
        if "!=" in condition:
            parts = condition.split("!=", 1)
            var_name = parts[0].strip().strip("{}")
            expected = parts[1].strip().strip('"\'')
            return variables.get(var_name) != expected
        
        # Handle == comparison
        if "==" in condition:
            parts = condition.split("==", 1)
            var_name = parts[0].strip().strip("{}")
            expected = parts[1].strip().strip('"\'')
            return variables.get(var_name) == expected
        
        # Default: check if variable is truthy
        var_name = condition.strip().strip("{}")
        return bool(variables.get(var_name))
