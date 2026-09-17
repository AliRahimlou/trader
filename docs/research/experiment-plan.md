# Research protocol, frozen before candidate evaluation

The executable protocol is [experiment-plan.json](../../research/experiment-plan.json). Baseline: `facc507ae2761a76916031994bcf9f956f9369fe`. The September 17 morning audit is development evidence. This branch cannot deploy itself and must not be merged automatically into the watched `main` branch.

We will test seven predeclared one-factor alternatives, with no adaptive combinations and no paid data or additional VIX requests. The baseline uses the production analyzer. Numerical variants are **proposed enhancements**, not claims about the videos. Exit policy stays fixed; entry-drift sensitivity is predeclared. Historical cached VIX limits the usable sample. Data coverage is measured before interpreting results.

## Timing and confirmation semantics

Evaluate only completed regular-session bars. A pivot is usable after its right-hand confirming bar closes and before the reacting bar starts. For persistence variants, a vote can start only at or after the current Nasdaq event's completion; it belongs to that event and session. Two-bar persistence means the current and preceding 15-minute candle (maximum 15-minute age); three means maximum 30-minute age. A subsequent close through the zone's invalidation side, a newer opposite reaction, a new Nasdaq event, the age limit, or a new session removes it. Repeated observations are not independent opportunities.

## Evaluation and evidence limits

Use chronological development/validation sessions and expanding historical walk-forward summaries. Simulated positions close within each session; outcomes cannot straddle a split. No historical date already used in source analysis or diagnostics is called an untouched final test. The final test is reserved for future, frozen-policy observations. Without adequate independent setups across regimes, the result is **inconclusive / no demonstrated edge**.

Use adverse execution assumptions: enter only after the signal and publication allowance; next available candle opening, stop first if OHLC touches both boundaries, adverse gaps, explicit costs, fractional whole-quantity reconciliation, no unavailable shorts. With 15-minute candles, the 15:55 liquidation price is unknowable; use the earlier 15:45 opening. Historical bars cannot prove live publication delay, contemporaneous spread, current VIX quote entitlement, partial-fill probabilities, or protective-order acceptance.

Report cash and same-$25 QQQ benchmarks, costs and capital exposure. Day-block uncertainty must not treat seven correlated companies or repeated candles as independent evidence. Seven alternatives require multiplicity correction. An empty trade sample has no estimated expectancy, not a zero-risk edge.

## Promotion and proposed loss limits

Promotion requires at least 60 independent opportunities over 40 sessions, positive after-cost uncertainty lower bound with family-wise adjustment, stability under higher costs/nearby parameters, no dependence on the best day, approved drawdown limits, shadow evidence, and paper execution commissioning. These are minimum screening conditions, not proof of future profitability.

Existing approved purchase amount is $25; it is not a loss budget. For review only, propose a $0.50 planned stop-risk ceiling per position, $1 daily realized-plus-unrealized loss pause, and $3 drawdown pause, one open position and no size increase. These values are **not applied to live trading**. A setup exceeding planned risk is skipped, retaining the requested dollar size. Broker stops cannot bound gap or outage losses. The owner must approve risk limits before a candidate can be promoted.
