# Reference Repo Audit

Date: 2026-05-27

Reference inputs:

- `/Users/alirahimlou/Downloads/freqtrade-develop.zip`
- `/Users/alirahimlou/Downloads/intelligent-trading-bot-master.zip`

## Scope Decision

The uploaded ZIPs are trading-bot references. The request also included a factory-optimization specification, which does not match the current trader app or the uploaded repos. This audit treats the ZIPs as trading-system references only.

## Freqtrade Reference

Useful production patterns:

- Protection layer that can temporarily lock all trading or one symbol after loss clusters, drawdown, low profitability, or cooldown.
- Strong backtesting discipline, including explicit warnings about lookahead bias.
- Separation between strategy signal generation and trade protections.
- Operator-visible configuration and documentation for protections.

Adopted now:

- Added Freqtrade-inspired paper-trading protections to this app:
  - Global loss guard.
  - Per-symbol loss guard.
  - Intraday closed-trade drawdown guard.
  - Operator-visible protection status.
  - Signal-level rejection reasons when protections block a setup.

Not adopted directly:

- Freqtrade is crypto/pair based and uses exchange abstractions that do not map cleanly to this Alpaca stock bot.
- No Freqtrade source code was copied into the runner.

## Intelligent Trading Bot Reference

Useful patterns:

- Column-oriented feature generation.
- Rolling features and labels for supervised model workflows.
- Separate download, feature, label, train, predict, simulate scripts.
- Feature declarations that make indicators reproducible.

Not adopted yet:

- ML prediction pipeline. The current bot should first accumulate reliable paper-trade outcomes and pass lookahead/recursive validation before model-driven decisions affect entries.

Recommended next uses:

- Add a declarative feature registry for scanner features.
- Add walk-forward model experiments that are advisory only.
- Add lookahead-bias checks before any ML or backtest result is trusted.

## Risk Note

These changes are intended to reduce avoidable bad trading behavior. They do not guarantee profits and should not be treated as proof that any strategy is profitable.
