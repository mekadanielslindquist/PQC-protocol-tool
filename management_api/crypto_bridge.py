"""Safe access to the real Falcon/Kyber key generation in sip_connect/.

`sip_connect.hipaa_security` (and everything it imports - falcon_wrapper,
kyber_wrapper, quantum_components) does real work at *import time*: it
loads compiled native libraries from hardcoded paths
(sip_connect/PQClean/.../aarch64/*.so, sip_connect/kyber/ref/*.so) and
requires qiskit/qiskit_aer/ephem to be installed. On a machine where those
libraries haven't been compiled for the local architecture - which is the
common case, since the build script only targets aarch64 - importing that
module raises immediately.

This module isolates that risk: the import is attempted lazily, once, and
any failure is captured as a clear status instead of taking the whole API
down. Every route that needs real crypto goes through `require_crypto()`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from management_api.config import KEYS_DIR, ORGANIZATIONS


@dataclass
class CryptoStatus:
    available: bool
    error: Optional[str] = None


_status: Optional[CryptoStatus] = None
_SecureKeyManager = None


def get_status() -> CryptoStatus:
    """Attempt (once) to import the real crypto backend, and remember why
    it failed if it did. Safe to call as often as you like.
    """
    global _status, _SecureKeyManager
    if _status is not None:
        return _status

    try:
        from sip_connect.hipaa_security import SecureKeyManager  # noqa: WPS433 (intentional lazy import)
        _SecureKeyManager = SecureKeyManager
        _status = CryptoStatus(available=True)
    except Exception as exc:  # native lib missing, wrong arch, missing deps, etc.
        _status = CryptoStatus(available=False, error=f"{type(exc).__name__}: {exc}")

    return _status


def require_crypto():
    """Return the SecureKeyManager class, or raise CryptoUnavailable."""
    status = get_status()
    if not status.available:
        raise CryptoUnavailable(status.error or "unknown error")
    return _SecureKeyManager


class CryptoUnavailable(RuntimeError):
    pass


def _fingerprint(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def key_status_for_org(org_id: str) -> dict:
    """Report which key files exist for an org, and a short fingerprint of
    each *public* key so two callers can confirm they're looking at the
    same key without either of them handling private key material.
    Private key bytes are never read into this response.
    """
    files = {
        "falcon_public": KEYS_DIR / f"{org_id}_falcon_public.key",
        "falcon_private": KEYS_DIR / f"{org_id}_falcon_private.key",
        "kyber_public": KEYS_DIR / f"{org_id}_kyber_public.key",
        "kyber_private": KEYS_DIR / f"{org_id}_kyber_private.key",
    }

    result = {"org_id": org_id, "keys": {}}
    for label, path in files.items():
        is_private = label.endswith("private")
        result["keys"][label] = {
            "present": path.exists(),
            # Only ever fingerprint/echo public keys.
            "fingerprint_sha256_16": None if is_private else _fingerprint(path),
        }
    return result


def generate_keys_for_org(org_id: str) -> dict:
    """Generate a fresh Falcon + Kyber keypair for org_id via the existing
    SecureKeyManager, and return metadata only - the response never
    contains private key bytes, only where they were written and a
    fingerprint of the public halves.
    """
    if org_id not in ORGANIZATIONS:
        raise ValueError(f"Unknown organization '{org_id}'. Expected one of {ORGANIZATIONS}.")

    manager = require_crypto()
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    manager.generate_all_keys(org_id, key_dir=str(KEYS_DIR))
    return key_status_for_org(org_id)
