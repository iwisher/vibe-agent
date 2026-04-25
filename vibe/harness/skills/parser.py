"""Parse SKILL.md files with TOML frontmatter."""
import re
import tomllib
from pathlib import Path

from .models import Skill, SkillStep, SkillTrigger, SkillVerification


class SkillParser:
    """Parse vibe-native SKILL.md files."""

    def parse_file(self, path: Path) -> Skill:
        content = path.read_text(encoding="utf-8")
        return self.parse_string(content)

    def parse_string(self, content: str) -> Skill:
        if not content.startswith("+++"):
            raise ValueError("SKILL.md must start with TOML frontmatter (+++)")

        parts = content.split("+++", 2)
        if len(parts) < 3:
            raise ValueError("Invalid frontmatter: missing closing +++")

        frontmatter = parts[1].strip()
        body = parts[2].strip()

        try:
            config = tomllib.loads(frontmatter)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML in frontmatter: {e}") from e

        # Parse steps from frontmatter
        steps = []
        for step_data in config.get("steps", []):
            verif_data = step_data.get("verification", {})
            steps.append(
                SkillStep(
                    id=step_data["id"],
                    description=step_data["description"],
                    script=step_data.get("script"),
                    tool=step_data["tool"],
                    command=step_data["command"],
                    condition=step_data.get("condition"),
                    verification=SkillVerification(
                        exit_code=verif_data.get("exit_code"),
                        output_contains=verif_data.get("output_contains"),
                        file_exists=verif_data.get("file_exists"),
                        json_has_keys=verif_data.get("json_has_keys", []),
                    ),
                )
            )

        # Parse trigger
        trigger_data = config.get("trigger", {})
        trigger = SkillTrigger(
            patterns=trigger_data.get("patterns", []),
            required_tools=trigger_data.get("required_tools", []),
            required_context=trigger_data.get("required_context", []),
        )

        # Parse pitfalls and examples from body
        pitfalls = self._extract_pitfalls(body)
        examples = self._extract_examples(body)

        return Skill(
            vibe_skill_version=config["vibe_skill_version"],
            id=config["id"],
            name=config["name"],
            description=config["description"],
            category=config.get("category", "general"),
            tags=config.get("tags", []),
            trigger=trigger,
            steps=steps,
            pitfalls=pitfalls,
            examples=examples,
            metadata=config.get("metadata", {}),
        )

    def _extract_pitfalls(self, body: str) -> list[str]:
        match = re.search(r"## Pitfalls\n+(.*?)(?=\n## |\Z)", body, re.DOTALL)
        if not match:
            return []
        return [
            line.strip()[1:].strip()
            for line in match.group(1).split("\n")
            if line.strip().startswith("-")
        ]

    def _extract_examples(self, body: str) -> list[dict]:
        examples = []
        match = re.search(r"## Examples\n+(.*?)(?=\n## |\Z)", body, re.DOTALL)
        if not match:
            return examples

        content = match.group(1)
        raw_examples = re.split(r"\n### Example \d+:", content)
        for raw in raw_examples[1:]:  # Skip preamble
            example = {}
            for line in raw.strip().split("\n"):
                if line.startswith("**Input:**"):
                    example["input"] = line.replace("**Input:**", "").strip()
                elif line.startswith("**Expected:**"):
                    example["expected"] = line.replace("**Expected:**", "").strip()
                elif line.startswith("**Notes:**"):
                    example["notes"] = line.replace("**Notes:**", "").strip()
            if example:
                examples.append(example)
        return examples
