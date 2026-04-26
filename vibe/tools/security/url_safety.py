"""URL safety and SSRF protection for vibe-agent.

Blocks access to internal IPs, metadata endpoints, and private networks.
"""

import ipaddress
import re
from urllib.parse import urlparse

# Blocked hostnames (from Hermes)
BLOCKED_HOSTNAMES: set[str] = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254.nip.io",
    "metadata.google.internal.nip.io",
}

# Blocked IPs (from Hermes)
BLOCKED_IPS: set[str] = {
    "169.254.169.254",   # AWS/GCP/Azure metadata
    "169.254.170.2",     # AWS ECS metadata
    "169.254.169.253",   # AWS DNS
    "fd00:ec2::254",     # AWS IPv6 metadata
    "100.100.100.200",   # Alibaba Cloud metadata
}

# Blocked networks (CIDR)
BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT
    ipaddress.ip_network("10.0.0.0/8"),       # Private
    ipaddress.ip_network("172.16.0.0/12"),    # Private
    ipaddress.ip_network("192.168.0.0/16"),   # Private
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("fc00::/7"),         # Unique local (IPv6)
    ipaddress.ip_network("fe80::/10"),        # Link-local (IPv6)
    ipaddress.ip_network("::1/128"),          # Loopback (IPv6)
]


class URLSafetyError(Exception):
    """URL safety violation."""

    def __init__(self, reason: str, url: str):
        self.reason = reason
        self.url = url
        super().__init__(f"URL safety violation ({reason}): {url}")


class URLSafetyChecker:
    """Checks URLs for SSRF and internal network access."""

    def __init__(self, allow_private: bool = False):
        self.allow_private = allow_private

    def check_url(self, url: str) -> None:
        """Check if URL is safe. Raises URLSafetyError if not."""
        if not url.startswith(("http://", "https://")):
            raise URLSafetyError("invalid_scheme", url)

        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise URLSafetyError("missing_hostname", url)

        # Check blocked hostnames
        if hostname in BLOCKED_HOSTNAMES:
            raise URLSafetyError("blocked_hostname", url)

        # Check if hostname is already an IP
        try:
            ip = ipaddress.ip_address(hostname)
            self._check_ip(ip, url)
            return
        except ValueError:
            pass

        # Not an IP, check DNS resolution
        self._check_dns(hostname, url)

    def _check_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address, url: str) -> None:
        """Check if IP is blocked."""
        if str(ip) in BLOCKED_IPS:
            raise URLSafetyError("blocked_ip", url)

        if self.allow_private:
            return

        for network in BLOCKED_NETWORKS:
            if ip in network:
                raise URLSafetyError("private_network", url)

    def _check_dns(self, hostname: str, url: str) -> None:
        """Check DNS resolution (fail-closed on error)."""
        import socket
        try:
            # Try to resolve hostname
            ip = socket.gethostbyname(hostname)
            ip_addr = ipaddress.ip_address(ip)
            self._check_ip(ip_addr, url)
        except socket.gaierror:
            # DNS resolution error - fail closed
            raise URLSafetyError("dns_resolution_failed", url)
        except Exception:
            # Any other error - fail closed
            raise URLSafetyError("dns_check_failed", url)

    def check_redirect(self, original_url: str, redirect_url: str) -> None:
        """Re-check redirect target."""
        self.check_url(redirect_url)


def check_url_safe(url: str, allow_private: bool = False) -> None:
    """Convenience function to check URL safety."""
    checker = URLSafetyChecker(allow_private=allow_private)
    checker.check_url(url)
