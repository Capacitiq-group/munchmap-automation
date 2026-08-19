"""
Short code generation for short links (e.g. mm.synkra.co.za/r/a7k2m9).

Pure stdlib - no network, no external packages - so this is fully
unit-testable without any mocking and without pytest even needing the
rest of the project's dependencies installed.

Design choices, deliberate:
- Excludes visually ambiguous characters (0/O, 1/I/l) because these
  codes end up printed on stickers, table cards, and menus where a
  customer is typing them by hand or a phone camera is reading a QR
  code that then displays the code as a fallback.
- Default length of 6 gives 32^6 ~= 1.07 billion possible codes from a
  32-character alphabet, which is comfortably enough headroom for a
  single city's restaurant + order volume without meaningful collision
  risk, while staying short enough to print legibly.
"""
from __future__ import annotations

import secrets
import string

# Unambiguous alphabet: lowercase letters and digits, minus 0/o and 1/i/l.
_ALPHABET = "".join(
    ch for ch in (string.ascii_lowercase + string.digits) if ch not in "0o1il"
)

DEFAULT_CODE_LENGTH = 6


def generate_short_code(length: int = DEFAULT_CODE_LENGTH) -> str:
    """Generate a single random short code. Not guaranteed unique on its own -
    the caller (link_service) is responsible for checking uniqueness against
    PocketBase and retrying on collision."""
    if length < 4:
        raise ValueError("Short codes shorter than 4 characters collide too easily at scale.")
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def is_valid_short_code(code: str) -> bool:
    """Validates a code's shape (not whether it exists) - used to reject
    obviously malformed lookups before hitting PocketBase at all."""
    if not code:
        return False
    return all(ch in _ALPHABET for ch in code)
