"""Dangerous pattern engine for vibe-agent.

Extracted from hardcoded bash.py into a configurable regex-based engine
with severity levels and command normalization.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class PatternSeverity(Enum):
    """Severity levels for pattern matches."""

    CRITICAL = "critical"  # auto-block
    WARNING = "warning"  # flag for review
    INFO = "info"  # log only


@dataclass(frozen=True)
class PatternMatch:
    """Result of a pattern match."""

    pattern_id: str
    severity: PatternSeverity
    description: str
    matched_text: str
    position: int


# ── Command Normalization ────────────────────────────────────────────────────

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def normalize_command(command: str) -> str:
    """Normalize command for pattern matching.

    Pipeline:
    1. Strip ANSI escape sequences
    2. Remove null bytes
    3. Unicode NFKC normalization
    4. Collapse whitespace
    """
    # 1. Strip ANSI
    cmd = _ANSI_ESCAPE_RE.sub("", command)
    # 2. Remove null bytes
    cmd = cmd.replace("\x00", "")
    # 3. Unicode NFKC normalization
    cmd = unicodedata.normalize("NFKC", cmd)
    # 4. Collapse whitespace
    cmd = " ".join(cmd.split())
    return cmd


# ── Pattern Registry ─────────────────────────────────────────────────────────

PatternDef = dict[str, Any]


# Built-in dangerous patterns
# ~70 patterns total (20 current + 30 from Hermes + 20 from OpenClaw)
BUILTIN_PATTERNS: list[PatternDef] = [
    # ── CRITICAL: auto-block ───────────────────────────────────────────────
    {"id": "rm-rf-root", "severity": "critical", "pattern": r"rm\s+-[a-zA-Z]*f.*\s+/\s*$|rm\s+-[a-zA-Z]*f\s+/\s", "description": "rm -rf / or similar"},
    {"id": "rm-rf-home", "severity": "critical", "pattern": r"rm\s+-[a-zA-Z]*f.*\s+~/?\s*$|rm\s+-[a-zA-Z]*f\s+~/?\s", "description": "rm -rf ~ or similar"},
    {"id": "fork-bomb", "severity": "critical", "pattern": r":\(\)\s*\{\s*:\|:\s*&\s*\};\s*:", "description": "Bash fork bomb"},
    {"id": "mkfs", "severity": "critical", "pattern": r"\bmkfs\.[a-zA-Z0-9]+", "description": "Filesystem formatting"},
    {"id": "dd-dev-zero", "severity": "critical", "pattern": r"dd\s+.*if=/dev/zero\s+.*of=/dev/[sh]d[a-z]", "description": "dd overwrite disk with zeros"},
    {"id": "dd-dev-random", "severity": "critical", "pattern": r"dd\s+.*if=/dev/urandom\s+.*of=/dev/[sh]d[a-z]", "description": "dd overwrite disk with random"},
    {"id": "chmod-777-recursive", "severity": "critical", "pattern": r"chmod\s+-R\s+777\s", "description": "Recursive chmod 777"},
    {"id": "chown-root-recursive", "severity": "critical", "pattern": r"chown\s+-R\s+root", "description": "Recursive chown to root"},
    {"id": "shutdown-now", "severity": "critical", "pattern": r"\bshutdown\s+-h\s+now\b|\bpoweroff\b|\breboot\b", "description": "System shutdown/reboot"},
    {"id": "init-sysrq", "severity": "critical", "pattern": r"echo\s+[a-z]\s*>\s*/proc/sysrq-trigger", "description": "SysRq trigger"},
    {"id": "iptables-flush", "severity": "critical", "pattern": r"iptables\s+-F", "description": "Flush all iptables rules"},
    {"id": "userdel-root", "severity": "critical", "pattern": r"userdel\s+root", "description": "Delete root user"},
    {"id": "mv-root", "severity": "critical", "pattern": r"mv\s+/\s", "description": "Move root directory"},
    {"id": "wget-pipe-sh", "severity": "critical", "pattern": r"wget\s+.*\|\s*(sh|bash)\b", "description": "wget | sh pipe"},
    {"id": "curl-pipe-sh", "severity": "critical", "pattern": r"curl\s+.*\|\s*(sh|bash)\b", "description": "curl | sh pipe"},
    # ── WARNING: flag for review ───────────────────────────────────────────
    {"id": "git-reset-hard", "severity": "warning", "pattern": r"git\s+reset\s+--hard", "description": "Git hard reset (destructive)"},
    {"id": "git-force-push", "severity": "warning", "pattern": r"git\s+push\s+.*--force\b|\bgit\s+push\s+.*-f\b", "description": "Git force push"},
    {"id": "git-clean-dfx", "severity": "warning", "pattern": r"git\s+clean\s+-[a-z]*[df]", "description": "Git clean (removes untracked files)"},
    {"id": "chmod-777", "severity": "warning", "pattern": r"chmod\s+.*777\s", "description": "chmod 777 (world-writable)"},
    {"id": "chown-root", "severity": "warning", "pattern": r"chown\s+root", "description": "chown to root"},
    {"id": "eval-inline", "severity": "warning", "pattern": r"\beval\s*\(", "description": "eval() call"},
    {"id": "sudo-no-pwd", "severity": "warning", "pattern": r"\bsudo\b(?!\s+-S)", "description": "sudo without -S (may hang)"},
    {"id": "docker-socket", "severity": "warning", "pattern": r"docker\s+.*-v\s+/var/run/docker\.sock", "description": "Docker socket mount (container escape)"},
    {"id": "docker-privileged", "severity": "warning", "pattern": r"docker\s+.*--privileged", "description": "Docker privileged mode"},
    {"id": "docker-host-pid", "severity": "warning", "pattern": r"docker\s+.*--pid\s+host", "description": "Docker host PID namespace"},
    {"id": "docker-host-net", "severity": "warning", "pattern": r"docker\s+.*--network\s+host", "description": "Docker host network"},
    {"id": "npm-install-global", "severity": "warning", "pattern": r"npm\s+install\s+-g", "description": "npm global install"},
    {"id": "pip-install-user", "severity": "warning", "pattern": r"pip\s+install\s+.*--user", "description": "pip user install"},
    {"id": "rustup-default", "severity": "warning", "pattern": r"rustup\s+default", "description": "rustup default toolchain change"},
    {"id": "cargo-install", "severity": "warning", "pattern": r"cargo\s+install", "description": "cargo install (downloads from crates.io)"},
    {"id": "brew-install", "severity": "warning", "pattern": r"brew\s+(install|reinstall|upgrade)", "description": "brew install/upgrade"},
    {"id": "apt-install", "severity": "warning", "pattern": r"apt\s+(install|remove|purge)", "description": "apt package management"},
    {"id": "yum-install", "severity": "warning", "pattern": r"yum\s+(install|remove)", "description": "yum package management"},
    {"id": "pacman-syu", "severity": "warning", "pattern": r"pacman\s+-[a-z]*[SyU]", "description": "pacman system upgrade"},
    {"id": "systemctl-enable", "severity": "warning", "pattern": r"systemctl\s+(enable|disable|start|stop|restart)", "description": "systemctl service management"},
    {"id": "crontab-edit", "severity": "warning", "pattern": r"crontab\s+-e", "description": "crontab edit"},
    {"id": "at-schedule", "severity": "warning", "pattern": r"\bat\s+\d", "description": "at job scheduling"},
    {"id": "ssh-keygen", "severity": "warning", "pattern": r"ssh-keygen", "description": "SSH key generation"},
    {"id": "openssl-genrsa", "severity": "warning", "pattern": r"openssl\s+genrsa", "description": "OpenSSL key generation"},
    {"id": "gpg-gen-key", "severity": "warning", "pattern": r"gpg\s+--gen-key|gpg\s+--full-generate-key", "description": "GPG key generation"},
    {"id": "wget-execute", "severity": "warning", "pattern": r"wget\s+.*\s+-O\s+-.*\|\s*(sh|bash|python)", "description": "wget output to pipe"},
    {"id": "curl-execute", "severity": "warning", "pattern": r"curl\s+.*\s+-o\s+-.*\|\s*(sh|bash|python)", "description": "curl output to pipe"},
    # ── INFO: log only ─────────────────────────────────────────────────────
    {"id": "sudo-with-s", "severity": "info", "pattern": r"\bsudo\s+-S\b", "description": "sudo with -S (non-interactive)"},
    {"id": "eval-string", "severity": "info", "pattern": r"\beval\s+['\"]", "description": "eval with string argument"},
    {"id": "source-remote", "severity": "info", "pattern": r"source\s+<(curl|wget)", "description": "source from remote URL"},
    {"id": "bash-c-url", "severity": "info", "pattern": r"bash\s+-c\s+['\"].*(curl|wget)", "description": "bash -c with curl/wget"},
    # ── Inline eval detection across interpreters ──────────────────────────
    {"id": "python-inline", "severity": "warning", "pattern": r"\bpython\d*\s+-[cm]\s+['\"]", "description": "Python inline code execution"},
    {"id": "node-inline", "severity": "warning", "pattern": r"\bnode\s+-e\s+['\"]", "description": "Node.js inline code execution"},
    {"id": "ruby-inline", "severity": "warning", "pattern": r"\bruby\s+-e\s+['\"]", "description": "Ruby inline code execution"},
    {"id": "perl-inline", "severity": "warning", "pattern": r"\bperl\s+-e\s+['\"]", "description": "Perl inline code execution"},
    {"id": "php-inline", "severity": "warning", "pattern": r"\bphp\s+-r\s+['\"]", "description": "PHP inline code execution"},
    {"id": "lua-inline", "severity": "warning", "pattern": r"\blua\s+-e\s+['\"]", "description": "Lua inline code execution"},
    {"id": "awk-inline", "severity": "warning", "pattern": r"\bawk\s+['\"].*\{.*\}", "description": "AWK script execution"},
    # ── Wrapper detection ──────────────────────────────────────────────────
    {"id": "wrapper-sudo", "severity": "warning", "pattern": r"\bsudo\b", "description": "sudo wrapper detected"},
    {"id": "wrapper-doas", "severity": "warning", "pattern": r"\bdoas\b", "description": "doas wrapper detected"},
    {"id": "wrapper-chrt", "severity": "warning", "pattern": r"\bchrt\b", "description": "chrt wrapper detected"},
    {"id": "wrapper-ionice", "severity": "warning", "pattern": r"\bionice\b", "description": "ionice wrapper detected"},
    {"id": "wrapper-taskset", "severity": "warning", "pattern": r"\btaskset\b", "description": "taskset wrapper detected"},
    {"id": "wrapper-setsid", "severity": "warning", "pattern": r"\bsetsid\b", "description": "setsid wrapper detected"},
    {"id": "wrapper-env", "severity": "info", "pattern": r"\benv\s+\w+=", "description": "env wrapper (variable setting)"},
    {"id": "wrapper-nice", "severity": "info", "pattern": r"\bnice\s+-?\d*\s", "description": "nice wrapper (priority adjustment)"},
    {"id": "wrapper-timeout", "severity": "info", "pattern": r"\btimeout\s+\d+", "description": "timeout wrapper"},
    # ── Data exfiltration / network ────────────────────────────────────────
    {"id": "nc-listen", "severity": "warning", "pattern": r"\bnc\s+.*-l\b|\bncat\s+.*-l\b|\bnetcat\s+.*-l\b", "description": "Netcat listener (backdoor)"},
    {"id": "nc-connect", "severity": "warning", "pattern": r"\bnc\s+\d+\.\d+\.\d+\.\d+\s+\d+\b", "description": "Netcat outbound connection"},
    {"id": "python-http-server", "severity": "warning", "pattern": r"python\d*\s+-m\s+http\.server", "description": "Python HTTP server"},
    {"id": "python-https-server", "severity": "warning", "pattern": r"python\d*\s+-m\s+http\.server.*--bind", "description": "Python HTTP server with bind"},
    {"id": "socat", "severity": "warning", "pattern": r"\bsocat\b", "description": "socat (arbitrary socket relay)"},
    {"id": "tcpdump", "severity": "warning", "pattern": r"\btcpdump\b", "description": "tcpdump (packet capture)"},
    {"id": "tshark", "severity": "warning", "pattern": r"\btshark\b", "description": "tshark (packet capture)"},
    {"id": "scp-outbound", "severity": "warning", "pattern": r"\bscp\s+.*\d+\.\d+\.\d+\.\d+", "description": "SCP to remote host"},
    {"id": "rsync-remote", "severity": "warning", "pattern": r"\brsync\s+.*\w+@\d+\.\d+\.\d+\.\d+", "description": "rsync to remote host"},
    {"id": "sftp-connect", "severity": "warning", "pattern": r"\bsftp\s+\w+@", "description": "SFTP connection"},
    {"id": "ftp-connect", "severity": "warning", "pattern": r"\bftp\s+\d+\.\d+\.\d+\.\d+", "description": "FTP connection"},
    {"id": "telnet-connect", "severity": "warning", "pattern": r"\btelnet\s+\d+\.\d+\.\d+\.\d+", "description": "Telnet connection"},
    # ── Credential / secret access ─────────────────────────────────────────
    {"id": "cat-etc-passwd", "severity": "warning", "pattern": r"cat\s+/etc/passwd", "description": "Read /etc/passwd"},
    {"id": "cat-etc-shadow", "severity": "critical", "pattern": r"cat\s+/etc/shadow", "description": "Read /etc/shadow"},
    {"id": "cat-ssh-keys", "severity": "warning", "pattern": r"cat\s+.*\.ssh/(id_rsa|id_ed25519|id_ecdsa)", "description": "Read SSH private keys"},
    {"id": "cat-aws-creds", "severity": "warning", "pattern": r"cat\s+.*\.aws/credentials", "description": "Read AWS credentials"},
    {"id": "cat-env-file", "severity": "warning", "pattern": r"cat\s+.*\.env", "description": "Read .env file"},
    {"id": "env-secrets", "severity": "warning", "pattern": r"env\s*\|\s*grep\s+-i\s+(secret|password|token|key)", "description": "Grep env for secrets"},
    # ── Process / system manipulation ──────────────────────────────────────
    {"id": "killall", "severity": "warning", "pattern": r"\bkillall\b", "description": "killall (kill processes by name)"},
    {"id": "pkill", "severity": "warning", "pattern": r"\bpkill\b", "description": "pkill (kill processes by pattern)"},
    {"id": "kill-9", "severity": "warning", "pattern": r"\bkill\s+-9\b", "description": "kill -9 (SIGKILL)"},
    {"id": "xargs-rm", "severity": "warning", "pattern": r"xargs\s+rm", "description": "xargs rm (bulk delete)"},
    {"id": "find-exec-rm", "severity": "warning", "pattern": r"find\s+.*-exec\s+rm", "description": "find -exec rm (bulk delete)"},
    {"id": "find-delete", "severity": "warning", "pattern": r"find\s+.*-delete", "description": "find -delete (bulk delete)"},
    {"id": "truncate", "severity": "warning", "pattern": r"\btruncate\s+-s\s+0\b", "description": "truncate to zero (data destruction)"},
    {"id": "shred", "severity": "critical", "pattern": r"\bshred\b", "description": "shred (secure delete)"},
    {"id": "wipefs", "severity": "critical", "pattern": r"\bwipefs\b", "description": "wipefs (filesystem signature erase)"},
]


class PatternEngine:
    """Regex-based dangerous pattern engine."""

    def __init__(self, patterns: Optional[list[PatternDef]] = None):
        self._patterns: list[PatternDef] = list(patterns) if patterns else list(BUILTIN_PATTERNS)
        self._compiled: dict[str, re.Pattern] = {}
        self._compile_all()

    def _compile_all(self) -> None:
        for p in self._patterns:
            try:
                self._compiled[p["id"]] = re.compile(p["pattern"], re.IGNORECASE)
            except re.error:
                # Skip invalid patterns but log
                self._compiled[p["id"]] = None  # type: ignore[assignment]

    def scan(self, command: str) -> list[PatternMatch]:
        """Scan a command for dangerous patterns.

        Returns list of PatternMatch sorted by severity (critical first).
        """
        normalized = normalize_command(command)
        matches: list[PatternMatch] = []

        for p in self._patterns:
            compiled = self._compiled.get(p["id"])
            if compiled is None:
                continue

            for m in compiled.finditer(normalized):
                severity = PatternSeverity(p["severity"])
                matches.append(
                    PatternMatch(
                        pattern_id=p["id"],
                        severity=severity,
                        description=p["description"],
                        matched_text=m.group(0),
                        position=m.start(),
                    )
                )

        # Sort by severity: critical > warning > info
        severity_order = {PatternSeverity.CRITICAL: 0, PatternSeverity.WARNING: 1, PatternSeverity.INFO: 2}
        matches.sort(key=lambda m: severity_order[m.severity])
        return matches

    def has_critical(self, command: str) -> bool:
        """Quick check if command has any critical patterns."""
        return any(m.severity == PatternSeverity.CRITICAL for m in self.scan(command))

    def get_patterns_by_severity(self, severity: PatternSeverity) -> list[PatternDef]:
        """Get all patterns of a given severity."""
        return [p for p in self._patterns if p["severity"] == severity.value]

    def add_pattern(self, pattern: PatternDef) -> None:
        """Add a custom pattern."""
        self._patterns.append(pattern)
        try:
            self._compiled[pattern["id"]] = re.compile(pattern["pattern"], re.IGNORECASE)
        except re.error:
            self._compiled[pattern["id"]] = None  # type: ignore[assignment]

    def remove_pattern(self, pattern_id: str) -> bool:
        """Remove a pattern by ID. Returns True if found."""
        for i, p in enumerate(self._patterns):
            if p["id"] == pattern_id:
                del self._patterns[i]
                self._compiled.pop(pattern_id, None)
                return True
        return False
