"""CI dashboard visualization for eval results.

Generates HTML reports from eval run data for CI integration.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalRunSummary:
    """Summary of a single eval run."""

    run_id: str
    timestamp: float
    total_tests: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    categories: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return self.passed / self.total_tests

    @property
    def status(self) -> str:
        if self.failed == 0:
            return "PASS"
        if self.pass_rate >= 0.95:
            return "WARN"
        return "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": self.duration_seconds,
            "pass_rate": self.pass_rate,
            "status": self.status,
            "categories": self.categories,
        }


class EvalDashboard:
    """Generate HTML dashboard reports from eval results."""

    def __init__(self, output_dir: str = "eval_reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, summaries: list[EvalRunSummary], title: str = "Eval Dashboard") -> str:
        """Generate HTML dashboard and return the file path."""
        html = self._render_html(summaries, title)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filepath = self.output_dir / f"eval_dashboard_{timestamp}.html"
        filepath.write_text(html, encoding="utf-8")
        return str(filepath)

    def _render_html(self, summaries: list[EvalRunSummary], title: str) -> str:
        latest = summaries[-1] if summaries else None
        history_json = json.dumps([s.to_dict() for s in summaries])

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --bg: #0d1117;
            --surface: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --text-secondary: #8b949e;
            --pass: #238636;
            --warn: #9e6a03;
            --fail: #da3633;
            --accent: #58a6ff;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
        }}
        .header {{
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.5rem;
            margin-bottom: 2rem;
        }}
        .header h1 {{ font-size: 1.75rem; font-weight: 600; }}
        .header .subtitle {{ color: var(--text-secondary); margin-top: 0.25rem; }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
        }}
        .card .label {{ font-size: 0.875rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.025em; }}
        .card .value {{ font-size: 2rem; font-weight: 600; margin-top: 0.5rem; }}
        .card .value.pass {{ color: var(--pass); }}
        .card .value.warn {{ color: var(--warn); }}
        .card .value.fail {{ color: var(--fail); }}
        .status-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .status-badge.pass {{ background: rgba(35, 134, 54, 0.2); color: var(--pass); }}
        .status-badge.warn {{ background: rgba(158, 106, 3, 0.2); color: var(--warn); }}
        .status-badge.fail {{ background: rgba(218, 54, 51, 0.2); color: var(--fail); }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            margin-top: 1rem;
        }}
        th, td {{ padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ font-size: 0.875rem; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; }}
        tr:hover {{ background: rgba(88, 166, 255, 0.05); }}
        .bar {{
            height: 8px;
            background: var(--border);
            border-radius: 4px;
            overflow: hidden;
            margin-top: 0.5rem;
        }}
        .bar-fill {{
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }}
        .bar-fill.pass {{ background: var(--pass); }}
        .bar-fill.warn {{ background: var(--warn); }}
        .bar-fill.fail {{ background: var(--fail); }}
        .timestamp {{ color: var(--text-secondary); font-size: 0.875rem; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{title}</h1>
        <p class="subtitle">Generated {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>

    {self._render_summary_cards(latest) if latest else '<p>No eval data available.</p>'}

    {self._render_history_table(summaries)}

    <script>
        const history = {history_json};
        console.log('Eval history:', history);
    </script>
</body>
</html>"""

    def _render_summary_cards(self, latest: EvalRunSummary | None) -> str:
        if latest is None:
            return ""
        status_class = latest.status.lower()
        return f"""
    <div class="summary-grid">
        <div class="card">
            <div class="label">Status</div>
            <div class="value"><span class="status-badge {status_class}">{latest.status}</span></div>
        </div>
        <div class="card">
            <div class="label">Pass Rate</div>
            <div class="value {status_class}">{latest.pass_rate:.1%}</div>
            <div class="bar"><div class="bar-fill {status_class}" style="width: {latest.pass_rate * 100}%"></div></div>
        </div>
        <div class="card">
            <div class="label">Total Tests</div>
            <div class="value">{latest.total_tests}</div>
        </div>
        <div class="card">
            <div class="label">Passed / Failed</div>
            <div class="value pass">{latest.passed}</div>
            <div class="label" style="margin-top: 0.5rem">Failed</div>
            <div class="value fail">{latest.failed}</div>
        </div>
        <div class="card">
            <div class="label">Duration</div>
            <div class="value">{latest.duration_seconds:.1f}s</div>
        </div>
        <div class="card">
            <div class="label">Run ID</div>
            <div class="value" style="font-size: 1rem; word-break: break-all">{latest.run_id}</div>
        </div>
    </div>
"""

    def _render_history_table(self, summaries: list[EvalRunSummary]) -> str:
        if not summaries:
            return ""
        rows = ""
        for s in summaries:
            status_class = s.status.lower()
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.timestamp))
            rows += f"""
        <tr>
            <td><span class="status-badge {status_class}">{s.status}</span></td>
            <td>{ts}</td>
            <td>{s.total_tests}</td>
            <td>{s.passed}</td>
            <td>{s.failed}</td>
            <td>{s.pass_rate:.1%}</td>
            <td>{s.duration_seconds:.1f}s</td>
        </tr>"""

        return f"""
    <h2 style="margin-top: 2rem; margin-bottom: 1rem;">Run History</h2>
    <table>
        <thead>
            <tr>
                <th>Status</th>
                <th>Timestamp</th>
                <th>Total</th>
                <th>Passed</th>
                <th>Failed</th>
                <th>Pass Rate</th>
                <th>Duration</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
"""


def generate_from_pytest(
    pytest_results: dict[str, Any],
    output_dir: str = "eval_reports",
) -> str:
    """Generate dashboard from pytest JSON report data.

    Args:
        pytest_results: Dict with 'summary' and 'tests' keys from pytest-json-report.
        output_dir: Where to write the HTML file.

    Returns:
        Path to generated HTML file.
    """
    summary = pytest_results.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    duration = pytest_results.get("duration", 0.0)

    run_summary = EvalRunSummary(
        run_id=pytest_results.get("environment", {}).get("Python", "unknown"),
        timestamp=time.time(),
        total_tests=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        duration_seconds=duration,
    )

    dashboard = EvalDashboard(output_dir=output_dir)
    return dashboard.generate([run_summary], title="Vibe Agent Eval Dashboard")
