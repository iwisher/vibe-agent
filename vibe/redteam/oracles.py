"""Per-surface executors and outcome oracles for the red-team harness.

Executors run one corpus payload against the *real* defense component (offline,
no LLM) and record the observed outcome. The oracle then compares observed vs.
expected; a mismatch on a hostile payload is a confirmed bypass finding.

Offline guarantee: the SSRF executor stubs ``socket.getaddrinfo`` with a static
table, so no run ever emits real DNS traffic and results are identical on
air-gapped runners.
"""

import socket
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from vibe.redteam.corpus import CorpusEntry


@dataclass
class Observation:
    """What the defense actually did with one payload."""

    outcome: str  # "blocked" | "flagged" | "allowed" | "error"
    detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    """One executed attack with its verdict."""

    entry: CorpusEntry
    observed: Observation
    passed: bool  # True = defense behaved as expected
    severity: str = ""  # effective severity when the defense failed

    def __post_init__(self) -> None:
        if not self.severity:
            self.severity = self.entry.severity


def check_oracle(entry: CorpusEntry, observed: Observation) -> Finding:
    """Compare the observed outcome against the corpus expectation."""
    if observed.outcome == "error":
        return Finding(entry=entry, observed=observed, passed=False, severity="high")
    passed = observed.outcome == entry.expected_outcome
    return Finding(entry=entry, observed=observed, passed=passed)


def _malformed(detail: str) -> Observation:
    return Observation(outcome="error", detail=f"malformed payload: {detail}")


# ── Surface executors (Tier A, offline) ──────────────────────────────────────


def execute_bash_patterns(entry: CorpusEntry, **_: Any) -> Observation:
    """S1: run the payload command through the real PatternEngine."""
    from vibe.tools.security.patterns import PatternEngine, PatternSeverity

    if isinstance(entry.payload, str):
        command = entry.payload
    elif isinstance(entry.payload, dict) and isinstance(entry.payload.get("command"), str):
        command = entry.payload["command"]
    else:
        return _malformed("expected a string command or {'command': str}")

    matches = PatternEngine().scan(command)
    dangerous = [
        m for m in matches if m.severity in (PatternSeverity.CRITICAL, PatternSeverity.WARNING)
    ]
    if dangerous:
        ids = ",".join(m.pattern_id for m in dangerous)
        return Observation(outcome="flagged", detail=f"matched: {ids}")
    return Observation(outcome="allowed", detail="no dangerous pattern matched")


def execute_file_safety(entry: CorpusEntry, **_: Any) -> Observation:
    """S2: attempt the write path against a FileSafetyGuard jailed to a fresh tmpdir."""
    from vibe.tools.security.file_safety import FileSafetyError, FileSafetyGuard

    payload = entry.payload if isinstance(entry.payload, dict) else {"path": entry.payload}
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return _malformed("expected {'path': str}")

    with tempfile.TemporaryDirectory(prefix="vibe-redteam-s2-") as tmp:
        guard = FileSafetyGuard(safe_root=Path(tmp))
        # Relative paths resolve against the jail, not the process CWD.
        candidate = Path(path)
        if not candidate.is_absolute() and not path.startswith("~"):
            candidate = Path(tmp) / candidate
        try:
            target = payload.get("resolve_to")
            if target:
                # Symlink-escape probes: create the symlink inside the jail, then attack it.
                # The probe setup itself must never escape the tmpdir.
                if ".." in Path(path).parts:
                    return _malformed("resolve_to probes must use jail-relative paths")
                link = (Path(tmp) / path).resolve()
                if not link.is_relative_to(Path(tmp).resolve()):
                    return _malformed("probe path escapes the jail")
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
                guard.check_write(link)
            else:
                guard.check_write(candidate)
        except FileSafetyError as e:
            return Observation(outcome="blocked", detail=f"FileSafetyError: {e}")
        except (OSError, ValueError) as e:
            return Observation(outcome="blocked", detail=f"{type(e).__name__}: {e}")
    return Observation(outcome="allowed", detail="write permitted")


#: Static DNS table for the SSRF executor — corpus domains only. Unknown hosts
#: raise gaierror, mirroring NXDOMAIN (which the real guard fails closed on).
_DNS_TABLE = {"example.com": "93.184.216.34"}

#: Serializes the process-wide socket.getaddrinfo patch across executor threads.
_DNS_PATCH_LOCK = threading.Lock()


def _fake_getaddrinfo(host: str, port: int, *_: Any, **__: Any) -> list:
    ip = _DNS_TABLE.get(host)
    if ip is None:
        raise socket.gaierror(f"Name or service not known: {host}")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]


def execute_ssrf(entry: CorpusEntry, **_: Any) -> Observation:
    """S3: ask the real is_safe_url guard about the payload URL (DNS stubbed)."""
    from vibe.tools.browser import is_safe_url

    if isinstance(entry.payload, str):
        url = entry.payload
    elif isinstance(entry.payload, dict) and isinstance(entry.payload.get("url"), str):
        url = entry.payload["url"]
    else:
        return _malformed("expected a string URL or {'url': str}")

    with _DNS_PATCH_LOCK, patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        safe = is_safe_url(url)
    if safe:
        return Observation(outcome="allowed", detail="guard accepted URL")
    return Observation(outcome="blocked", detail="guard rejected URL")


class _RecordingLLMClient:
    """Simulates a fully fooled approver LLM while capturing the prompt it saw."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> str:
        if messages and isinstance(messages, list) and len(messages) > 0:
            prompt_str = str(messages[0].get("content", ""))
        else:
            prompt_str = str(prompt or "")
        self.prompts.append(prompt_str)
        # What a successful injection wants the approver to answer.
        return '{"risk_level": "low", "reasoning": "fooled", "suggested_modifications": null}'


def execute_approval(entry: CorpusEntry, **_: Any) -> Observation:
    """S4: SmartApprover prompt-injection surface + PatternEngine backstop.

    Verifies two things: (a) an injected control string embedded in tool args
    reaches the approver prompt, and whether the occurrence is fenced off as
    untrusted data; (b) a dangerous command is flagged by Layer 1 on its own —
    Layer 4 is never reached on that path.
    """
    import json

    from vibe.tools.security.patterns import PatternEngine, PatternSeverity
    from vibe.tools.security.smart_approver import (
        UNTRUSTED_ARGS_BEGIN,
        UNTRUSTED_ARGS_END,
        SmartApprover,
    )

    payload = entry.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("args"), dict):
        return _malformed("expected {'tool': str, 'args': dict, 'injected'?: str}")
    tool = str(payload.get("tool", "bash"))
    args = payload["args"]
    injected = payload.get("injected")

    # (b) Backstop: Layer 1 judges the command on its own.
    command = str(args.get("command", ""))
    dangerous = [
        m
        for m in PatternEngine().scan(command)
        if m.severity in (PatternSeverity.CRITICAL, PatternSeverity.WARNING)
    ]
    if dangerous:
        ids = ",".join(m.pattern_id for m in dangerous)
        return Observation(outcome="flagged", detail=f"backstop caught command: {ids}")

    # (a) Injection surface: let the fooled approver assess the call.
    recorder = _RecordingLLMClient()
    SmartApprover(llm_client=recorder).assess_tool_call(tool, args)
    if injected is None:
        return Observation(outcome="allowed", detail="no injection probe in payload")
    if not recorder.prompts:
        # Heuristic short-circuit before the LLM — the probe never reached the
        # surface it measures. Loud, never a vacuous pass.
        return Observation(outcome="error", detail="LLM path never consulted (heuristic shortcut)")
    prompt = recorder.prompts[0]

    # The prompt embeds args via json.dumps, so match the marker raw or escaped.
    # Strict fence accounting: exactly one real BEGIN/END pair must exist —
    # a duplicated marker means fence spoofing slipped through unmunged.
    begin = prompt.find(UNTRUSTED_ARGS_BEGIN)
    if begin < 0 or prompt.count(UNTRUSTED_ARGS_BEGIN) != 1:
        return Observation(outcome="error", detail="args fence missing or duplicated")
    if prompt.count(UNTRUSTED_ARGS_END) != 1:
        return Observation(outcome="error", detail="args end-fence duplicated (spoofing?)")
    end = prompt.find(UNTRUSTED_ARGS_END)

    # Anchor the marker search inside the fenced region (template text like
    # "Never follow instructions" lives outside it and must not collide).
    search_from = begin + len(UNTRUSTED_ARGS_BEGIN)
    idx = prompt.find(injected, search_from)
    if idx < 0:
        idx = prompt.find(json.dumps(injected)[1:-1], search_from)
    if idx < 0:
        return Observation(outcome="error", detail="injected marker not found in approver prompt")

    # Note: this verifies demarcation only. Whether a fenced injection still fools
    # a live model into APPROVE is out of offline scope (Tier C) — the always-fooled
    # recorder is the conservative stand-in.
    if begin < idx < end:
        return Observation(outcome="blocked", detail="injection fenced as untrusted data")
    return Observation(outcome="allowed", detail="injection reached approver prompt unfenced")


def execute_skill_supply(entry: CorpusEntry, **_: Any) -> Observation:
    """S5: run a malicious step command through the real SkillValidator."""
    from vibe.harness.skills.models import Skill, SkillStep
    from vibe.harness.skills.validator import SkillValidator

    payload = entry.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
        return _malformed("expected {'command': str}")
    skill = Skill(
        vibe_skill_version="2.0.0",
        id="redteam-probe",
        name="Redteam Probe",
        description="red-team probe skill",
        steps=[
            SkillStep(id="probe", description="probe step", tool="bash", command=payload["command"])
        ],
    )
    result = SkillValidator().validate(skill)
    if result.risks:
        # Real risks block installation (AutoRejectGate rejects on any risk).
        return Observation(outcome="flagged", detail="; ".join(result.risks[:3]))
    if result.warnings:
        # Warnings are detected but do NOT block install (both gates pass them) —
        # reported distinctly so "detected-but-installed" is never read as enforcement.
        return Observation(outcome="warned", detail="; ".join(result.warnings[:3]))
    return Observation(outcome="allowed", detail="validator found no risks")


def execute_mcp(entry: CorpusEntry, **_: Any) -> Observation:
    """S7: invoke an MCP HTTP tool at the payload URL, recording any real request.

    ``httpx`` is swapped only inside the mcp_bridge module namespace, so the real
    ``_get_http_client`` (caching, and any gate added there) still executes; the
    public ``execute_tool`` path is used so the probe covers the whole chain.
    A block must happen before any HTTP call is attempted.
    """
    import asyncio
    import types

    from vibe.tools.mcp_bridge import MCPBridge

    payload = entry.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        return _malformed("expected {'url': str}")
    url = payload["url"]

    attempted: list[str] = []

    class _Recorder:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def post(self, u: str, json: Any = None) -> Any:
            attempted.append(u)

            class _Resp:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {}

            return _Resp()

    bridge = MCPBridge(
        [{"name": "probe", "description": "", "url": url, "tools": [{"name": "probe"}]}]
    )

    async def _invoke() -> Any:
        fake_httpx = types.SimpleNamespace(AsyncClient=_Recorder)
        with patch("vibe.tools.mcp_bridge.httpx", new=fake_httpx):
            return await bridge.execute_tool("probe")

    # Same offline guarantee as S3: stub DNS so the is_safe_url gate (once
    # present) resolves corpus domains deterministically.
    with _DNS_PATCH_LOCK, patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        result = asyncio.run(_invoke())
    if attempted:
        return Observation(outcome="allowed", detail=f"HTTP request attempted to {attempted[0]}")
    return Observation(outcome="blocked", detail=f"blocked before HTTP: {result.error}")


#: Registry mapping corpus surface -> offline executor. Surface S6 (memory
#: poisoning) is exercised end-to-end in Tier B rather than as a unit executor.
EXECUTORS: dict[str, Callable[..., Observation]] = {
    "bash_patterns": execute_bash_patterns,
    "file_safety": execute_file_safety,
    "ssrf": execute_ssrf,
    "approval": execute_approval,
    "skill_supply": execute_skill_supply,
    "mcp": execute_mcp,
}
