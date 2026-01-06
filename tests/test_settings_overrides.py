from __future__ import annotations

# We use pytest because the repository already standardizes on it for automated checks.
import pytest

# We import the existing Settings loader so tests run with the real default config structure.
from tripscore.config.settings import get_settings

# We test the override helper directly because it is pure (no network) and safety-critical.
from tripscore.config.overrides import apply_settings_overrides


def test_apply_settings_overrides_returns_same_object_when_none():
    # Load the baseline settings once (this is a cached Pydantic model).
    settings = get_settings()

    # When no overrides are provided, we expect a no-op and the same object back (fast path).
    out = apply_settings_overrides(settings, None)

    # Identity equality is intentional here: the function returns early without rebuilding the model.
    assert out is settings


def test_apply_settings_overrides_can_override_allowed_numeric_knobs():
    # Load the baseline settings (do not mutate it; it is shared via lru_cache).
    settings = get_settings()

    # Override an allowed ingestion knob: the bus-stop search radius used by accessibility scoring.
    overrides = {"ingestion": {"tdx": {"accessibility": {"radius_m": 1234}}}}

    # Apply the override; this returns a NEW Settings model validated by Pydantic.
    out = apply_settings_overrides(settings, overrides)

    # The override should take effect on the returned model.
    assert out.ingestion.tdx.accessibility.radius_m == 1234

    # The original shared settings should remain unchanged (important to avoid cross-request leakage).
    assert settings.ingestion.tdx.accessibility.radius_m != 1234


def test_apply_settings_overrides_rejects_disallowed_keys_with_clear_path():
    # Load baseline settings for context (not strictly required, but keeps the call signature realistic).
    settings = get_settings()

    # This key is intentionally disallowed because it is a file path (could enable arbitrary file reads).
    overrides = {"features": {"context": {"district_factors_path": "/etc/passwd"}}}

    # We expect a ValueError with a dotted path so users can find the offending key quickly.
    # Note: in regex, a literal dot must be escaped as `\.` (a raw string avoids double escaping).
    with pytest.raises(ValueError, match=r"features\.context\.district_factors_path"):
        apply_settings_overrides(settings, overrides)


def test_apply_settings_overrides_rejects_wrong_value_shapes_for_restricted_subtrees():
    # Load baseline settings.
    settings = get_settings()

    # `ingestion` is a restricted subtree (only certain nested keys are allowed),
    # so its override must be an object/mapping, not a scalar.
    overrides = {"ingestion": 1}

    # The helper should raise a ValueError explaining the expected shape.
    with pytest.raises(ValueError, match=r"settings_overrides key 'ingestion' must be a mapping"):
        apply_settings_overrides(settings, overrides)
