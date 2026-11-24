"""Parser for the X-Simulate-Failure test header.

Format: comma-separated `<key_id>=<status>` pairs, `*` meaning every key.
    key_1=429
    key_1=429,key_2=500
    *=429
"""
from __future__ import annotations

ALLOWED_STATUSES = {429, 500, 502, 503}


def parse_simulation_header(value: str | None) -> dict[str, int]:
    rules: dict[str, int] = {}
    if not value:
        return rules
    for part in value.split(","):
        if "=" not in part:
            continue
        key_id, _, status = part.partition("=")
        key_id = key_id.strip()
        try:
            code = int(status.strip())
        except ValueError:
            continue
        if key_id and code in ALLOWED_STATUSES:
            rules[key_id] = code
    return rules


def simulated_status(rules: dict[str, int], key_id: str) -> int | None:
    """Specific key rule wins over the wildcard."""
    if key_id in rules:
        return rules[key_id]
    return rules.get("*")
