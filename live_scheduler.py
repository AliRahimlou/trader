from __future__ import annotations

from datetime import time

import pandas as pd

ET_TZ = "America/New_York"


class StaleDataError(RuntimeError):
    pass


def parse_hhmm(value: str) -> time:
    pd.Timestamp(f"2000-01-01 {value}", tz=ET_TZ)
    return pd.Timestamp(f"2000-01-01 {value}", tz=ET_TZ).time()


def to_et_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(ET_TZ)
    return ts.tz_convert(ET_TZ)


def validate_latest_bar(
    now: pd.Timestamp,
    latest_bar_time: pd.Timestamp,
    *,
    max_bar_age_seconds: int,
) -> None:
    latest_bar_time = to_et_timestamp(latest_bar_time)
    expected_previous_minute = now.floor("min") - pd.Timedelta(minutes=1)
    age_seconds = (now - latest_bar_time).total_seconds()

    if latest_bar_time > expected_previous_minute:
        raise StaleDataError(
            f"Latest bar {latest_bar_time} is newer than the expected closed minute {expected_previous_minute}."
        )
    if age_seconds > max_bar_age_seconds:
        raise StaleDataError(
            f"Latest bar {latest_bar_time} is stale by {age_seconds:.1f}s; limit is {max_bar_age_seconds}s."
        )


def session_key(ts: pd.Timestamp) -> str:
    return str(to_et_timestamp(ts).date())
