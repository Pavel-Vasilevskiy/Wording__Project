"""Independent mode providers validate settings for each level type."""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from models import db
from cyberpunk_warehouse.models import CyberpunkGameMode


class ModeConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModeProvider:
    key: str
    display_name: str
    description: str
    configuration_schema: dict
    default_configuration: dict

    def validate(self, configuration, *, time_limit_seconds=None):
        raise NotImplementedError


def _object(value, label):
    if not isinstance(value, dict):
        return [f"{label} must be an object."]
    return []


def _positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class ClassicMode(ModeProvider):
    def validate(self, configuration, *, time_limit_seconds=None):
        errors = _object(configuration, "Classic configuration")
        if errors:
            return errors
        objective = configuration.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            errors.append("Classic mode requires a non-empty objective.")
        if time_limit_seconds is not None and not _positive_integer(time_limit_seconds):
            errors.append("Classic mode timer must be a positive number of seconds when supplied.")
        if "lives" in configuration and not _positive_integer(configuration["lives"]):
            errors.append("Classic lives must be a positive integer when supplied.")
        if "scoring_rules" in configuration and not isinstance(configuration["scoring_rules"], dict):
            errors.append("Classic scoring_rules must be an object.")
        return errors


class TimedMode(ModeProvider):
    def validate(self, configuration, *, time_limit_seconds=None):
        errors = _object(configuration, "Timed configuration")
        if errors:
            return errors
        if not _positive_integer(time_limit_seconds):
            errors.append("Timed mode requires a positive level time limit in seconds.")
        if "lives" in configuration and not _positive_integer(configuration["lives"]):
            errors.append("Timed mode lives must be a positive integer when supplied.")
        if "failure_conditions" in configuration and not (
            isinstance(configuration["failure_conditions"], list)
            and all(isinstance(value, str) and value.strip() for value in configuration["failure_conditions"])
        ):
            errors.append("Timed failure_conditions must be a list of non-empty strings.")
        if "scoring_rules" in configuration and not isinstance(configuration["scoring_rules"], dict):
            errors.append("Timed scoring_rules must be an object.")
        return errors


class UnlimitedMode(ModeProvider):
    def validate(self, configuration, *, time_limit_seconds=None):
        errors = _object(configuration, "Unlimited configuration")
        if errors:
            return errors
        if time_limit_seconds is not None:
            errors.append("Unlimited mode cannot have a countdown time limit.")
        move_limit = configuration.get("move_limit")
        if move_limit is not None and not _positive_integer(move_limit):
            errors.append("Unlimited move_limit must be a positive integer or null.")
        if "scoring_rules" in configuration and not isinstance(configuration["scoring_rules"], dict):
            errors.append("Unlimited scoring_rules must be an object.")
        return errors


class PvPMode(ModeProvider):
    def validate(self, configuration, *, time_limit_seconds=None):
        errors = _object(configuration, "PvP configuration")
        if errors:
            return errors
        if not _positive_integer(configuration.get("match_duration_seconds")):
            errors.append("PvP mode requires a positive match_duration_seconds value.")
        if not isinstance(configuration.get("synchronization_settings"), dict):
            errors.append("PvP mode requires synchronization_settings as an object.")
        if not isinstance(configuration.get("competitive_scoring"), dict):
            errors.append("PvP mode requires competitive_scoring as an object.")
        conditions = configuration.get("completion_conditions")
        if not isinstance(conditions, list) or not conditions or not all(
            isinstance(value, str) and value.strip() for value in conditions
        ):
            errors.append("PvP mode requires one or more completion_conditions.")
        return errors


_PROVIDERS = {}


def register_mode_provider(provider):
    """Register one server-side validator; providers own their mode rules."""
    if not isinstance(provider, ModeProvider) or not provider.key:
        raise ValueError("A mode provider with a non-empty key is required.")
    if provider.key in _PROVIDERS:
        raise ValueError(f"A provider is already registered for mode '{provider.key}'.")
    _PROVIDERS[provider.key] = provider


def get_mode_provider(key):
    return _PROVIDERS.get(key)


for _provider in (
    ClassicMode(
        "classic", "Classic", "Standard objective-based level progression.",
        {"type": "object", "required": ["objective"], "properties": {"objective": {"type": "string"}, "lives": {"type": "integer", "minimum": 1}, "scoring_rules": {"type": "object"}}, "level_fields": {"time_limit_seconds": {"type": ["integer", "null"], "minimum": 1}}},
        {"objective": "complete_order"},
    ),
    TimedMode(
        "timed", "Timed", "Complete an order before the level countdown expires.",
        {"type": "object", "properties": {"lives": {"type": "integer", "minimum": 1}, "failure_conditions": {"type": "array", "items": {"type": "string"}}, "scoring_rules": {"type": "object"}}, "level_fields": {"time_limit_seconds": {"type": "integer", "minimum": 1}}},
        {"lives": 3, "failure_conditions": ["timer_expired"], "scoring_rules": {}},
    ),
    UnlimitedMode(
        "unlimited", "Unlimited", "Play at your own pace without a countdown.",
        {"type": "object", "properties": {"move_limit": {"type": ["integer", "null"], "minimum": 1}, "scoring_rules": {"type": "object"}}},
        {"move_limit": None, "scoring_rules": {}},
    ),
    PvPMode(
        "pvp", "PvP", "Competitive play prepared for synchronized two-player matches.",
        {"type": "object", "required": ["match_duration_seconds", "synchronization_settings", "competitive_scoring", "completion_conditions"], "properties": {"match_duration_seconds": {"type": "integer", "minimum": 1}, "synchronization_settings": {"type": "object"}, "competitive_scoring": {"type": "object"}, "completion_conditions": {"type": "array", "items": {"type": "string"}}}},
        {"match_duration_seconds": 300, "synchronization_settings": {}, "competitive_scoring": {}, "completion_conditions": ["all_orders_completed"]},
    ),
):
    register_mode_provider(_provider)


def validate_mode_configuration(mode_key, configuration, *, time_limit_seconds=None):
    provider = get_mode_provider(mode_key)
    if provider is None:
        return [f"No server-side configuration validator is registered for mode '{mode_key}'."]
    return provider.validate(configuration, time_limit_seconds=time_limit_seconds)


def seed_builtin_game_modes():
    """Insert missing built-in modes without overwriting administrator data."""
    added = False
    for provider in _PROVIDERS.values():
        if CyberpunkGameMode.query.filter_by(key=provider.key).first():
            continue
        db.session.add(CyberpunkGameMode(
            key=provider.key,
            display_name=provider.display_name,
            description=provider.description,
            configuration_schema=provider.configuration_schema,
            default_configuration=provider.default_configuration,
            is_builtin=True,
        ))
        added = True
    if not added:
        return
    try:
        db.session.commit()
    except IntegrityError:
        # Safe to retry in the rare case two startup processes seed concurrently.
        db.session.rollback()

