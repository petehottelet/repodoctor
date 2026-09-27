"""Offline guardrails for the whole suite: no provider keys, no external network."""

import ipaddress
import socket
import sys

import pytest

CLEARED_ENV = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "REPO_DOCTOR_MODEL",
)


def _offline_network(event, args):
    """Reject external Python socket traffic, including during test collection.

    This is a test guardrail, not an OS sandbox: separately launched processes
    need their own offline fixtures. No credential-dependent live tests belong
    in this suite.
    """
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event in {"socket.connect", "socket.sendto"}:
        sock, address = args
        if sock.family not in {socket.AF_INET, socket.AF_INET6}:
            return
        host = address[0]
    else:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host in {None, "localhost"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("Offline test suite blocked external network dispatch")


sys.addaudithook(_offline_network)


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    """Real provider keys never reach a test; a test that needs one sets a fake value."""
    for key in CLEARED_ENV:
        monkeypatch.delenv(key, raising=False)
