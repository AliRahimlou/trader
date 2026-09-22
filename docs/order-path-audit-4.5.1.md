# Socrates order-path audit (4.5.1)

Every request the Socrates executor (`pivot/execution.py`, `pivot/broker.py`) can send to Alpaca for QQQ longs and PSQ short-proxy purchases, checked against the Alpaca Trading API v2 rules. The rules were read on September 22, 2026 from:

- [Create an order (POST /v2/orders)](https://docs.alpaca.markets/reference/postorder): `client_order_id` at most 128 characters; `notional` only on market orders with DAY; `qty` and `notional` up to 9 decimals and never both; `extended_hours` only with limit orders.
- [Fractional trading](https://docs.alpaca.markets/docs/fractional-trading): fractional orders support market, limit, stop and stop-limit with `time_in_force=day`; fractional sells are long only.
- [Orders at Alpaca](https://docs.alpaca.markets/docs/orders-at-alpaca): stop and limit prices at or above $1 take at most 2 decimals (4 below $1), otherwise error 42210000; an elected stop becomes a market order.
- [Alpaca forum: "potential wash trade detected"](https://forum.alpaca.markets/t/apierror-potential-wash-trade-detected-use-complex-orders/13441): an order is rejected while an opposite-side market or stop order is open in the same symbol.

No order was sent to produce this audit. The checks run offline in `pivot/tests/test_order_payload_conformance.py` against a fake venue that uses Alpaca's 9-decimal fills and slow cancellations and records any request Alpaca would reject.

| Request | Payload | Rule checked | Result |
| --- | --- | --- | --- |
| Entry, fractionable QQQ or PSQ | `buy` `market` `day`, `notional` = saved amount (e.g. `15.00`), `extended_hours: false` | Notional only on a market buy, DAY, whole cents, at least $1 (settings refuse less than $1) | Conforms |
| Entry, non-fractionable PSQ | `buy` `market` `day`, whole-share `qty` within 1% of the amount | Integer quantity; no `notional` | Conforms |
| Protective stop | `sell` `stop` `day`, `qty` = held shares, `stop_price` | Fractional stop must be DAY; `stop_price` whole cents at or above $1 | **Fixed**: an entry whose stop is not in whole cents is now skipped before the entry is sent, since the stop placed after the fill would be rejected and leave shares unprotected. The analysis already rounds QQQ stops to cents and PSQ stops/targets are translated and rounded to cents (`ROUND_HALF_UP`); the guard catches any other source. |
| Protective stop and market exit quantity | `qty` text | Plain decimal, at most 9 decimals | **Fixed**: quantities are written with `format(..., 'f')`; `str(Decimal)` would have written a remnant below 0.000001 share as `1E-7`. Alpaca reports positions with at most 9 decimals, so the quantity is passed through unchanged. |
| Market exit | `sell` `market` `day`, `qty` = held shares | DAY fractional market sell; long only | Conforms |
| Cancel | `DELETE /v2/orders/{id}` | 204/404/422 never prove cancellation; the order is read again | Conforms |
| Every POST | `client_order_id = pvt-<24 hex>-<entry|stop|exit0..2>` | At most 128 characters, unique | Conforms (34 characters; one identifier per operation, durably claimed before the POST and never resubmitted) |
| Every POST | `extended_hours: false` | Only limit orders may trade extended hours | Conforms |
| Exit while a stop is open | Stop cancelled, then read again until terminal, then market sale | No competing sale while the stop holds the shares | Conforms (`_exit` returns after each cancel request; the sale is prepared only after the stop reads canceled, expired or filled) |
| Entry while an own stop is open | None sent | No buy while an opposite-side order is open (wash trade) | Conforms (one Socrates trade at a time; a new entry is admitted only with no active trade, and foreign QQQ/PSQ orders block admission) |
| PSQ translated stop and target | Cents, `ROUND_HALF_UP` | Stop below the PSQ bid, target above the ask; whole cents | Conforms |

The entry payload is also checked by `order_payload_violations` just before its durable reservation; a payload that breaks a rule is skipped with a waiting message and no order. Stops and exits are not blocked by this check (protection must never be withheld); their conformance is covered by the tests above.

Not verified: fills and rejections on the live account. The first live entry will be the first end-to-end test of this path.
