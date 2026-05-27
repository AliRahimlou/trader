from dataclasses import dataclass

from backend.app.db import models
from backend.app.market_data.base import Quote


@dataclass(frozen=True)
class StopDecision:
    should_exit: bool
    reason: str


def update_trailing_stop(position: models.Position, quote: Quote) -> None:
    if position.trailing_stop is None:
        return
    candidate = quote.last * 0.985
    if candidate > position.trailing_stop:
        position.trailing_stop = round(candidate, 2)


def evaluate_exit(position: models.Position, quote: Quote) -> StopDecision:
    update_trailing_stop(position, quote)
    if position.stop_loss is not None and quote.last <= position.stop_loss:
        return StopDecision(True, "Stop loss reached.")
    if position.take_profit is not None and quote.last >= position.take_profit:
        return StopDecision(True, "Take profit reached.")
    if position.trailing_stop is not None and quote.last <= position.trailing_stop:
        return StopDecision(True, "Trailing stop reached.")
    return StopDecision(False, "Position remains inside exit rules.")
