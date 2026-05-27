from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ny_now() -> datetime:
    return utc_now().astimezone(NY_TZ)


def is_regular_market_hours(now: datetime | None = None) -> bool:
    current = (now or ny_now()).astimezone(NY_TZ)
    if current.weekday() >= 5:
        return False
    return time(9, 30) <= current.time() <= time(16, 0)
