# Version 4.5.2 · Live trade view

The owner asked for a live view of the open trade: how much it is up or down since the purchase, where it will be sold, and when. Version 4.5.2 adds that view. It changes no trading rule and no policy version, so there is nothing to re-accept after the update.

## What the page shows

A **Live trade** card at the top of the page appears whenever Socrates holds a position. It is visible in every View and refreshes every 3 seconds.

- **Up or down since the purchase.** Shown in dollars, in percent, and in R (multiples of the planned risk: +1R means the trade has gained as much as the stop would lose). Profit is green and loss is red. The browser tab title also shows it (for example `+$0.14 · PSQ · Pivot`).
- **Prices.** The current price is the bid, which is what a sale would get. The card also shows the amount paid, what the position is worth now, and the entry fill (shares, price, time).
- **A price bar from the stop to the target.** It marks the entry, and a dot shows where the price is now.
- **A price line since the purchase.** It keeps one point every 15 seconds and draws the entry, stop and target as dashed lines.
- **How it will sell**, as three rows:
  1. **Target:** the dollar result if reached and the distance away. The app sells at market as soon as the bid reaches it; it checks every 5 seconds.
  2. **Stop:** the dollar result if hit, the distance away, and the live status of the protective stop order at Alpaca.
  3. **Market close:** the time and a countdown. If neither the target nor the stop is hit, the app sells 5 minutes before the close. Socrates never holds overnight.
- **PSQ trades** also show the QQQ short setup behind them: QQQ now, and the QQQ entry, stop and target.
- **Other stages.** The headline reads *Buying*, *Selling: Target reached* / *Stop price reached* / *Closing before the session ends*, or *Needs your attention*.
- **With no open trade,** the card shows the last trade: the entry and sale prices and times, the gross result from Alpaca fills (fees not included), and why it sold.

## Where the numbers come from

- `GET /api/live-trade` is read-only; `/api/snapshot` carries the same `live_trade` and `last_trade` fields.
- The builder is `pivot/live_trade.py`. It makes no broker calls.
- The current price is the same quote the executor reads every 5 seconds to decide a target exit. It falls back to the Alpaca position price (at most 15 seconds old) while quotes are not being read, for example while selling.
- The price line and latest quote live in memory, so a restart during a trade starts the line again. Updates install only when the account is flat.
- Recording the price can never block an exit. A failure there is logged and management continues (see `test_a_failing_mark_never_blocks_the_target_exit`).
- Exit reasons now name which level was reached: `Target reached` or `Stop price reached`. Before this release both read `Stop or target reached`.

## Checking it locally

```
.venv/bin/python scripts/ui_fixture_server.py --port 8765 --scenario psq
```

The `psq` (winning), `down` (losing) and `sold` (last-trade recap) scenarios show the card.
