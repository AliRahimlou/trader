# Isolated broker-paper commissioning

This workflow tests the real broker API's order lifecycle without addressing a live trading endpoint. It does **not** test the video strategy or profitability. Its entry signal, Nasdaq event, leader confirmation and VIX fields are synthetic admission fixtures, explicitly labeled as such. The QQQ quote, account, orders and positions are broker observations during an actual run.

Implementation and fake-transport tests are complete. **Actual paper commissioning remains unverified until a dated report is produced using separate paper credentials.** No actual paper or live order was sent while implementing or testing this command.

## Boundaries

- Fixed trading destination: `https://paper-api.alpaca.markets`. No configurable live broker URL is accepted. Market quotes use Alpaca's read-only market-data endpoint.
- Requires explicitly named `PAPER_APCA_API_KEY_ID` and `PAPER_APCA_API_SECRET_KEY` in an explicitly selected private file. It never falls back to production `.env` or environment credentials.
- Separate directory named `paper-commission`, environment marker, SQLite ledger and exclusive process lock. Existing unidentified directories or another account's saved trade are refused.
- Default action is read-only broker preflight. `--run-paper` explicitly authorizes one synthetic long workflow, between $1 and **$5 maximum**.
- The production Executor remains live-account-only by default and rejects synthetic commissioning snapshots. Paper fixtures use a distinct signal-policy version, required persisted commissioning purpose, and a separate event-ID namespace. Removing their outer labels cannot turn them into valid live signal contracts; nested candidates are checked too. Paper mode requires the dedicated fixed-host adapter and this separate workflow contract.
- New workflows require a flat account with no working orders. Recovery permits only the saved paper account and its owned QQQ trade; unrelated positions/orders are not canceled or traded over.
- The workflow uses the production notional entry, actual filled quantity, DAY stop, order lookup, cancel-confirm-read exit sizing and persistent lifecycle. It seeks a confirmed working stop before requesting its own exit. It never tests profitability by waiting for a favorable market move.
- Unknown submission/cancellation outcomes are not retried blindly or erased. An incomplete run leaves the paper-only ledger for inspection and a later run using the same directory. The loop has a bounded deadline; an in-progress bounded network call can finish after that deadline.

## Setup

Create a separate Alpaca paper API key in the broker's paper account. Store it in an owner-readable file outside Git:

```dotenv
PAPER_APCA_API_KEY_ID=your_paper_key
PAPER_APCA_API_SECRET_KEY=your_paper_secret
```

The file must be owned by the current user and have mode `0600`. Do not use or copy live credentials. Alpaca documents separate paper keys and endpoint, with paper trading available without a fee: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading).

## Read-only preflight

From the repository root, with the private file location selected explicitly:

```sh
.venv/bin/python -m research.paper_commission \
  --paper-env /absolute/private/paper.env \
  --state-dir /absolute/private/paper-commission
```

Preflight checks the paper account identity, ownership of any saved trade, positions/orders, regular market session, purchase amount, QQQ eligibility and current quote. It does not enable execution or submit/cancel an order. The report is saved as `commission-report.json` in the private paper directory.

## Explicit paper workflow

When preflight is ready, adding `--run-paper` permits the isolated workflow:

```sh
.venv/bin/python -m research.paper_commission \
  --paper-env /absolute/private/paper.env \
  --state-dir /absolute/private/paper-commission \
  --run-paper --amount 5.00 --timeout 120
```

It attempts one notional QQQ long, verifies filled shares and broker stop status, disables additional entries, cancels the stop with confirmation, and closes only its owned shares. Restart the same command against the same directory to reconcile an incomplete owned lifecycle. Never replace the ledger or delete an uncertain order to obtain a fresh attempt.

`workflow_verified` requires observed entry fill, working protection, an exit fill, and a final flat account with no orders/active lifecycle. `incomplete` is not success and includes the next action. The report deliberately sets `strategy_validated=false`, `profitability_validated=false`, and `vix_verified=false`.

## What paper cannot establish

Paper results do not prove live venue acceptance, live liquidity, slippage, market impact, queue position or net returns. Alpaca lists differences from live trading in its [paper documentation](https://docs.alpaca.markets/us/docs/paper-trading). The current fractional documentation supports DAY market/limit/stop/stop-limit workflows and excludes fractional short sales, but retains an inconsistent older market-only sentence; real paper responses and, if needed, broker clarification should be recorded explicitly: [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading).

A successful report is broker-workflow evidence only. Strategy validation still requires source labeling, frozen rules, arrival-time data, chronological holdout and after-cost measurement.
