from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.app.backtesting.engine import BacktestEngine
from backend.app.db import models
from backend.app.db.session import get_db


router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    ticker: str | None = None
    auto_pick: bool = False
    strategy: str = "auto_strategy"
    risk_level: str = "balanced"
    capital: float = Field(default=10_000, gt=0)
    days: int = Field(default=220, ge=90, le=500)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


@router.post("/run")
def run_backtest(payload: BacktestRequest, db: Session = Depends(get_db)) -> dict:
    if not payload.auto_pick and not payload.ticker:
        raise HTTPException(status_code=400, detail="Choose a ticker or enable Auto-Pick.")
    result = BacktestEngine().run(
        ticker=payload.ticker,
        auto_pick=payload.auto_pick,
        strategy=payload.strategy,
        risk_level=payload.risk_level,
        capital=payload.capital,
        days=payload.days,
    )
    run = models.BacktestRun(
        ticker=payload.ticker,
        auto_pick=payload.auto_pick,
        strategy=payload.strategy,
        risk_level=payload.risk_level,
        capital=payload.capital,
        metrics=result.metrics,
        equity_curve=result.equity_curve,
        trades=result.trades,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return {
        "id": run.id,
        "metrics": run.metrics,
        "equity_curve": run.equity_curve,
        "trades": run.trades,
    }


@router.get("/{run_id}")
def get_backtest(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(models.BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {
        "id": run.id,
        "ticker": run.ticker,
        "auto_pick": run.auto_pick,
        "strategy": run.strategy,
        "risk_level": run.risk_level,
        "capital": run.capital,
        "metrics": run.metrics,
        "equity_curve": run.equity_curve,
        "trades": run.trades,
        "created_at": run.created_at.isoformat(),
    }
