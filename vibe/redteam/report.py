"""Findings report for red-team runs (JSON + markdown scorecard)."""

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from vibe.redteam.oracles import Finding
from vibe.redteam.tier_3 import Tier3Result
from vibe.redteam.tier_b import TierBResult

_MAX_DETAIL = 200


def _md_safe(text: Any) -> str:
    """Make an attacker-influenced string safe for a committed markdown file.

    Strips CR/LF (no fake sections or bullets) and caps length. Rendered inside
    backticks/parenthesized fields, so backticks are neutralized too.
    """
    cleaned = " ".join(str(text).split())[:_MAX_DETAIL]
    return cleaned.replace("`", "'")


def build_report(
    findings: list[Finding],
    tier_b: list[TierBResult] | None = None,
    tier_3: list[Tier3Result] | None = None,
) -> dict[str, Any]:
    """Aggregate Tier-A findings, optional Tier-B scenario results, and Tier-3 tasks."""
    by_surface = Counter(f.entry.surface for f in findings)
    failed = [f for f in findings if not f.passed]
    failures_by_severity = Counter(f.severity for f in failed)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_attacks": len(findings),
        "defense_held": len(findings) - len(failed),
        "bypasses": len(failed),
        "by_surface": dict(sorted(by_surface.items())),
        "bypasses_by_severity": dict(sorted(failures_by_severity.items())),
        "findings": [
            {
                "id": f.entry.id,
                "surface": f.entry.surface,
                "expected": f.entry.expected_outcome,
                "observed": f.observed.outcome,
                "passed": f.passed,
                "severity": f.severity,
                "detail": f.observed.detail,
            }
            for f in findings
        ],
    }
    if tier_b is not None:
        report["tier_b"] = {
            "scenarios": len(tier_b),
            "contained": sum(1 for r in tier_b if r.passed),
            "results": [
                {
                    "id": r.scenario_id,
                    "passed": r.passed,
                    "layer": r.layer,
                    "side_effect_free": r.side_effect_free,
                    "detail": r.detail,
                }
                for r in tier_b
            ],
        }
    if tier_3 is not None:
        report["tier_3"] = {
            "total_tasks": len(tier_3),
            "passed_tasks": sum(1 for r in tier_3 if r.passed),
            "results": [
                {
                    "id": r.scenario_id,
                    "name": r.name,
                    "category": r.category,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for r in tier_3
            ],
        }
    return report


def render_json(
    findings: list[Finding],
    tier_b: list[TierBResult] | None = None,
    tier_3: list[Tier3Result] | None = None,
) -> str:
    return json.dumps(build_report(findings, tier_b, tier_3), indent=2)


def render_markdown(
    findings: list[Finding],
    tier_b: list[TierBResult] | None = None,
    tier_3: list[Tier3Result] | None = None,
) -> str:
    report = build_report(findings, tier_b, tier_3)
    lines = [
        "# Red-Team Findings Report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Attacks executed: {report['total_attacks']}",
        f"- Defense held: {report['defense_held']}",
        f"- Bypasses: {report['bypasses']}",
        "",
        "## Bypasses by severity",
        "",
    ]
    if report["bypasses_by_severity"]:
        for sev, count in report["bypasses_by_severity"].items():
            lines.append(f"- **{sev}**: {count}")
    else:
        lines.append("- none")
    lines += ["", "## Tier A findings", ""]
    for f in report["findings"]:
        mark = "PASS" if f["passed"] else "FAIL"
        lines.append(
            f"- [{mark}] `{_md_safe(f['id'])}` ({_md_safe(f['surface'])}, "
            f"{_md_safe(f['severity'])}): expected {f['expected']}, observed "
            f"{f['observed']} — {_md_safe(f['detail'])}"
        )
    if "tier_b" in report:
        tb = report["tier_b"]
        lines += [
            "",
            "## Tier B compromised-model scenarios",
            "",
            f"- Scenarios: {tb['scenarios']}, contained: {tb['contained']}",
            "",
        ]
        for r in tb["results"]:
            mark = "PASS" if r["passed"] else "FAIL"
            lines.append(
                f"- [{mark}] `{_md_safe(r['id'])}` (layer: {_md_safe(r['layer'])})"
                f" — {_md_safe(r['detail'])}"
            )
    if "tier_3" in report:
        t3 = report["tier_3"]
        lines += [
            "",
            "## Tier 3 long-horizon challenged agent tasks",
            "",
            f"- Tasks: {t3['total_tasks']}, passed: {t3['passed_tasks']}",
            "",
        ]
        for r in t3["results"]:
            mark = "PASS" if r["passed"] else "FAIL"
            lines.append(
                f"- [{mark}] `{_md_safe(r['id'])}` ({_md_safe(r['category'])}) "
                f"[{_md_safe(r['name'])}] — {_md_safe(r['detail'])}"
            )
    return "\n".join(lines) + "\n"
