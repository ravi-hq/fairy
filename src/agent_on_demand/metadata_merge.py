"""Metadata merge with Anthropic-compatible delete-on-empty semantics.

The agents API exposes a free-form `metadata` field on `PUT /agents/{id}`.
The merge semantic mirrors Anthropic's metadata API: keys with non-empty
values overwrite, keys with empty-string `""` values delete, keys absent
from the patch pass through. SDKs depend on this exact behavior — a
silent drift (e.g. dropping the delete-on-empty branch) would break
clients that issue ``{"key": ""}`` to clear a value.

Kept as a tiny dedicated module so it can be mutation-tested in
isolation; the wider `views/agents.py` is too DB-heavy to mutate
directly under hammett.
"""

from __future__ import annotations


def merge_metadata(current: dict, patch: dict) -> dict:
    """Apply `patch` to `current` and return a new dict.

    - Keys whose patch value is the literal empty string ``""`` are
      removed (no error if the key wasn't in `current`).
    - Keys whose patch value is anything else (including non-string
      values) overwrite the corresponding entry in `current`.
    - Keys not present in `patch` carry through unchanged.

    Never mutates `current` or `patch` directly — neither dict has
    keys added, removed, or reassigned by this call. The returned
    dict is a *shallow* copy of `current` with the patch applied:
    mutable values (nested dicts, lists) are still shared by
    reference. Don't mutate values in the result if you also rely
    on `current` being unchanged.
    """
    merged = dict(current)
    for k, v in patch.items():
        if v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged
