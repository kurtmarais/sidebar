"""PIN-locked secret notes.

The note text is encrypted with Windows protected storage (DPAPI), using
a key derived from the PIN as additional entropy, so decrypting needs both
the Windows account and the PIN. The PIN itself is never stored, only a
salted PBKDF2 fingerprint used to reject wrong PINs early.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Callable

from . import winplatform

ITERATIONS = 600_000      # new secrets; existing ones keep their stored count
MAX_ATTEMPTS = 5
LOCKOUT_S = 30

Protect = Callable[[bytes, bytes], bytes]

_b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
_unb64 = lambda s: base64.b64decode(s.encode("ascii"))  # noqa: E731


def valid_pin(pin: str) -> bool:
    """A PIN of 4+ digits or a passphrase (4 to 64 characters). Longer is much
    stronger: a 4-digit PIN can be guessed in minutes by someone who has your
    Windows account and a copy of the notes database."""
    return 4 <= len(pin or "") <= 64


def _derive(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)


def is_initialised(content: dict) -> bool:
    return bool(content.get("fp") and content.get("cipher") is not None)


def create(pin: str, plaintext: str, protect: Protect = winplatform.dpapi_protect) -> dict:
    if not valid_pin(pin):
        raise ValueError("PIN must be at least 4 characters")
    fp_salt, key_salt = os.urandom(16), os.urandom(16)
    content = {
        "iter": ITERATIONS,
        "fp_salt": _b64(fp_salt),
        "fp": _b64(_derive(pin, fp_salt, ITERATIONS)),
        "key_salt": _b64(key_salt),
    }
    return seal(content, pin, plaintext, protect)


def check_pin(content: dict, pin: str) -> bool:
    if not valid_pin(pin) or not is_initialised(content):
        return False
    expected = _unb64(content["fp"])
    actual = _derive(pin, _unb64(content["fp_salt"]), int(content["iter"]))
    return hmac.compare_digest(expected, actual)


def derive_key(content: dict, pin: str) -> bytes:
    """Encryption entropy for this note. Cache it while the note is unlocked."""
    return _derive(pin, _unb64(content["key_salt"]), int(content["iter"]))


def seal(content: dict, pin: str, plaintext: str, protect: Protect = winplatform.dpapi_protect) -> dict:
    return seal_with_key(content, derive_key(content, pin), plaintext, protect)


def seal_with_key(content: dict, key: bytes, plaintext: str,
                  protect: Protect = winplatform.dpapi_protect) -> dict:
    return {**content, "cipher": _b64(protect(plaintext.encode("utf-8"), key))}


def reveal(content: dict, pin: str, unprotect: Protect = winplatform.dpapi_unprotect) -> tuple[str, bytes]:
    """Decrypt with a PIN. Returns (plaintext, key) so the key can be cached."""
    if not check_pin(content, pin):
        raise ValueError("Wrong PIN")
    key = derive_key(content, pin)
    return unprotect(_unb64(content["cipher"]), key).decode("utf-8"), key


class PinGuard:
    """Rate-limits PIN attempts: after 5 wrong PINs, wait 30 seconds."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.failures = 0
        self.locked_until = 0.0

    def wait_seconds(self) -> int:
        return max(0, int(self.locked_until - self._clock() + 0.999))

    def record(self, success: bool) -> None:
        if success:
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= MAX_ATTEMPTS:
            self.failures = 0
            self.locked_until = self._clock() + LOCKOUT_S
