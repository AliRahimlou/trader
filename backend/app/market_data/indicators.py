from statistics import mean, pstdev

from backend.app.market_data.base import Bar


def closes(bars: list[Bar]) -> list[float]:
    return [bar.close for bar in bars]


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    return mean(values[-window:])


def rate_of_change(values: list[float], window: int) -> float:
    if len(values) <= window or values[-window] == 0:
        return 0.0
    return (values[-1] / values[-window]) - 1


def rsi(values: list[float], window: int = 14) -> float:
    if len(values) <= window:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    segment = values[-(window + 1) :]
    for previous, current in zip(segment, segment[1:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = mean(gains) if gains else 0
    avg_loss = mean(losses) if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(bars: list[Bar], window: int = 14) -> float:
    if len(bars) < window + 1:
        return 0.0
    ranges: list[float] = []
    for previous, current in zip(bars[-(window + 1) : -1], bars[-window:]):
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return mean(ranges)


def average_volume(bars: list[Bar], window: int = 20) -> float:
    if not bars:
        return 0.0
    sample = bars[-window:]
    return mean([bar.volume for bar in sample])


def volume_expansion(bars: list[Bar], window: int = 20) -> float:
    avg_volume = average_volume(bars, window)
    if avg_volume <= 0:
        return 0.0
    return bars[-1].volume / avg_volume


def volatility(values: list[float], window: int = 20) -> float:
    if len(values) <= window:
        return 0.0
    returns = [(cur / prev) - 1 for prev, cur in zip(values[-(window + 1) : -1], values[-window:]) if prev]
    if len(returns) < 2:
        return 0.0
    return pstdev(returns)


def rolling_high(bars: list[Bar], window: int = 20) -> float:
    if not bars:
        return 0.0
    return max(bar.high for bar in bars[-window:])


def rolling_low(bars: list[Bar], window: int = 20) -> float:
    if not bars:
        return 0.0
    return min(bar.low for bar in bars[-window:])


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
