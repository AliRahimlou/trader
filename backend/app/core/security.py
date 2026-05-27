from backend.app.core.config import get_settings


def redact_secret(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def live_confirmation_valid(mode: str, confirmation: str | None) -> bool:
    if mode != "live":
        return True
    settings = get_settings()
    return bool(settings.live_trading_enabled and confirmation == settings.live_confirmation_phrase)
