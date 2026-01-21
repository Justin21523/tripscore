from __future__ import annotations


# We keep typing intentionally flexible because overrides come from JSON payloads (dict-like objects)
# and we want clear error messages when users send unexpected shapes.
from typing import Any, Mapping

from tripscore.config.settings import Settings

"""
Per-request settings overrides (safe subset).

The web UI and API can send `settings_overrides` to tune certain knobs for a single
recommendation run. This module:
- validates the override payload against a whitelist,
- deep-merges the safe subset onto current settings,
- re-validates with Pydantic to ensure types/ranges remain correct.

Security note:
We intentionally do NOT allow overriding secrets (TDX credentials) or file paths.
"""

# This constant defines which parts of the global Settings object can be overridden per request.
#
# Why this exists:
# - The API allows clients (e.g., the web UI) to "tune knobs" without editing server files.
# - But we must not allow overriding secrets (TDX credentials) or unsafe values (e.g., file paths).
#
# How to read this structure:
# - A value of True means "allow any keys under this subtree".
# - A nested dict means "only allow the listed keys, recursively".
#
# Security note (important):
# - We intentionally do NOT allow overriding file paths like `features.context.district_factors_path`,
#   because that could let a user point the server at arbitrary files.

ALLOWED_SETTINGS_OVERRIDES_TREE: dict[str, Any] = {
    # Scoring is safe to tune because it only changes numeric weights and thresholds.
    "scoring": True,
    # Features are generally safe, but we still restrict context to avoid path overrides.
    "features": {
        "weather": True,
        "parking": True,
        "preference_match": True,
        "context": {
            "default_avoid_crowds_importance": True,
            "default_family_friendly_importance": True,
            "crowd": True,
            "family": True,
        },
    },
    # Ingestion is more sensitive (URLs, timeouts, secrets), so we whitelist only "math knobs".
    "ingestion": {
        # `city` is safe to override (it only changes which bulk cached datasets are read).
        "tdx": {"accessibility": True, "city": True},
        "weather": {
            "aggregation": True,
            "comfort_temperature_c": True,
            "temperature_penalty_scale_c": True,
            "score_weights": True,
        },
    },
}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    # We create a new dict so the caller's `base` object is never mutated (safer for caching/reuse).
    merged: dict[str, Any] = dict(base)
    # We apply each override key on top of the base settings.
    for key, override_value in override.items():
        # If both values are mappings, we merge recursively so nested keys override cleanly.
        if isinstance(override_value, Mapping) and isinstance(merged.get(key), Mapping):
            # `dict(...)` normalizes Mapping -> dict so we can assign and recurse consistently.
            merged[key] = _deep_merge(dict(merged[key]), override_value)
            continue
        # Otherwise, the override "wins" and replaces the base value.
        merged[key] = override_value
    # The returned dict is a merged view suitable for Pydantic validation.
    return merged


def _filter_overrides(
    overrides: Mapping[str, Any],
    *,
    allowed_tree: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    # We build a new dict that contains only whitelisted keys (and raises on any unknown key).
    filtered: dict[str, Any] = {}
    # We iterate through user-provided overrides so we can validate every key path.
    for key, value in overrides.items():
        # If the key is not explicitly allowed, we reject it early with a precise path.
        if key not in allowed_tree:
            dotted_path = ".".join((*path, key))
            raise ValueError(
                f"settings_overrides contains a disallowed key: '{dotted_path}'"
            )

        # `allowed` is either True (allow subtree) or a nested dict (restrict subtree).
        allowed = allowed_tree[key]
        # If the subtree is fully allowed, we keep the value as-is.
        if allowed is True:
            filtered[key] = value
            continue

        # If the subtree is restricted, the override value must be a mapping we can recurse into.
        if not isinstance(value, Mapping):
            dotted_path = ".".join((*path, key))
            raise ValueError(
                f"settings_overrides key '{dotted_path}' must be a mapping"
            )

        # Recurse into the subtree to validate nested keys.
        filtered[key] = _filter_overrides(
            value, allowed_tree=allowed, path=(*path, key)
        )
    # Returning only the safe keys prevents clients from changing unrelated configuration.
    return filtered


def apply_settings_overrides(
    settings: Settings, overrides: Mapping[str, Any] | None
) -> Settings:
    # If the request did not include overrides, we return the original Settings unchanged.
    if not overrides:
        return settings

    # First, validate and strip overrides to a safe subset (raises ValueError on disallowed keys).
    safe_overrides = _filter_overrides(
        overrides, allowed_tree=ALLOWED_SETTINGS_OVERRIDES_TREE
    )

    # Then, merge the safe overrides into the current settings dict (override values take priority).
    merged_payload = _deep_merge(settings.model_dump(mode="python"), safe_overrides)

    # Finally, re-validate via Pydantic so we never run with an invalid Settings object.
    return Settings.model_validate(merged_payload)
