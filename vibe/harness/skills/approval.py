"""Approval gate protocol for skill installation security prompts."""
from typing import Protocol


class ApprovalGate(Protocol):
    """Protocol for user approval decisions."""

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        """Return True if installation should proceed."""
        ...


class CLIApprovalGate:
    """Interactive CLI approval — prompts user via input()."""

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        print(f"\n[SECURITY REVIEW] Skill: {skill_name}")
        print("-" * 50)
        if risks:
            print("RISKS (will block installation):")
            for risk in risks:
                print(f"  - {risk}")
        if warnings:
            print("WARNINGS:")
            for warning in warnings:
                print(f"  - {warning}")
        print("-" * 50)

        if risks:
            print("\nThis skill has CRITICAL risks. Installation blocked.")
            return False

        response = input("\nApprove installation despite warnings? (yes/no): ").strip().lower()
        return response in ("yes", "y")


class AutoApproveGate:
    """Auto-approve everything — for headless/agent contexts."""

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        return True


class AutoRejectGate:
    """Auto-reject if risks present, auto-approve if only warnings."""

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        return len(risks) == 0
