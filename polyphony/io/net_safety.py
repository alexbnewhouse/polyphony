"""Shared network safety helpers for external URL fetches."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse


def is_safe_host(hostname: str) -> bool:
    """Reject localhost, private ranges, and common metadata endpoints."""
    if not hostname:
        return False

    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    if hostname in ("169.254.169.254", "metadata.google.internal"):
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
    except ValueError:
        try:
            for info in socket.getaddrinfo(hostname, None):
                addr = ipaddress.ip_address(info[4][0])
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return False
        except socket.gaierror:
            return False

    return True


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow only http(s) redirects to safe hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise urllib.error.URLError(f"Redirected to unsupported scheme: {newurl}")
        if not is_safe_host(parsed.hostname or ""):
            raise urllib.error.URLError(f"Redirected to unsafe host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
