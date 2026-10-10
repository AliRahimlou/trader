# Native execution-data follow-on — September 18, 2026

## Outcome

The execution-only follow-on collected **2,293 authentic one-minute QQQ candles** from Alpaca IEX for September 9–16. There are **47 missing regular-session minute observations** across the six sessions. No missing prices were created or carried forward. None of the sessions meets the predeclared complete-session requirement for a return comparison.

The new [v4 plan](../../research/native-execution-plan-v4.json) and [offline evaluation path](../../research/native_execution_experiments_v4.py) preserve the native five-minute signal rules, publication delay, evidence deadlines and cost assumptions. Only execution observations become finer. **The historical v4 strategy comparison was not run:** the server's native five-minute capture has not been exported locally through an available authenticated route. The complete-session execution-data requirement would also remain unmet with this IEX sample.

## Actual acquisition

The fixed request used QQQ, `1Min`, IEX, split adjustment, ascending timestamps, a 10,000-row cap, and September 9 at 09:30 through September 16 at 16:00 New York. It accessed only Alpaca's historical stock-data endpoint. There were no account, order or VIX requests.

Two request attempts occurred. The first failed normalization; its raw response had not been saved before validation, so the exact offending timestamp cannot be recovered. Its consumed claim remains recorded. After adding durable raw-response storage and an inclusive-end-boundary test, one additional request was explicitly authorized and succeeded. No pagination or third request occurred.

The successful request was received at **17:27:04.887972 UTC on September 18, 2026**. It returned 2,453 provider rows; 160 outside the permitted regular-session execution interval were excluded, including the row starting exactly at the requested ending boundary. The original response is retained privately. This receipt establishes when the history was collected, not when each candle was available during the historical session.

| Session | Expected regular-session minutes | Native IEX observations | Missing observations |
| --- | ---: | ---: | ---: |
| September 9 | 390 | 385 | 5 |
| September 10 | 390 | 384 | 6 |
| September 11 | 390 | 384 | 6 |
| September 14 | 390 | 381 | 9 |
| September 15 | 390 | 386 | 4 |
| September 16 | 390 | 373 | 17 |
| Total | 2,340 | 2,293 | 47 |

These gaps describe observations missing from the returned IEX series. They do not establish that QQQ stopped trading across the market. IEX is one exchange; Alpaca distinguishes it from consolidated SIP data in its [official market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).

## Reproducibility

Private evidence is retained under `runtime/research/`:

- Successful artifact: `native-v4-qqq1m-execution-20260918-attempt2.json`; SHA-256 `2ca4b82702a7c39039e62054f9040888b5f49bc399fbbae185772ce26c4b21e4`.
- Original provider-response content SHA-256: `52dcf5269c0768be8e3708d53ccf75696c174a89d334f3198b6387551482dd5d`.
- Normalized bar content SHA-256: `a7891f6d45a737838cae0be0c159608ec8be76fc02b7dff1d039e777872c2fda`.
- Attempt accounting: `native-v4-acquisition-ledger-20260918.json`, recording both attempts and the first response's loss.
- Predeclared v4 plan SHA-256: `4fc22922d3a47d998ede1d27582ca1447b53c3502aa1ccb6c6abc15f0da048e8`.

The plan's remaining-request field records the allowance at plan creation, before the second attempt. **Both authorized attempts have now been used.** The acquisition ledger is the actual request count. No earlier v2/v3 result or plan was overwritten.

## What the v4 path can and cannot establish

The evaluator requires hash-verified native v3 observations and the same frozen strategy/input identity. It can count actual observed one-minute openings that occur before fixed signal deadlines. Such an opening is an execution observation, not a verified broker fill or permission to trade. Short observations remain separate from the fractional long-only research executor's eligibility.

Return metrics are produced only for complete native execution sessions. Otherwise the report leaves performance unevaluated; it does not label an empty run profitable or risk-free. For admissible baseline entries, X1 can compare a VIX-driven exit at the same entry and quantity, using an observed one-minute QQQ opening after the five-minute VIX bar's close plus publication delay. Missing execution observations while holding produce an unresolved outcome. Protective stop/target rules remain active, and proceeds are not reinvested into an invented portfolio path.

Fifteen manufactured-fixture tests passed for one-request acquisition limits, retained failed responses, pagination rejection, source/price integrity, inclusive-end exclusion, honest gaps, unchanged deadlines, cost effects, short eligibility, VIX exit timing and missing holding-period observations. These tests verify implementation behavior; they are not market results.

## Bounded next comparison

A separate future plan could compare execution evidence from historical SIP with the same frozen IEX-based signals. Alpaca's [official FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) says historical SIP queries can be made without a subscription when the requested end is at least 15 minutes old. That is distinct from entitlement to live or latest SIP data. No SIP request was made in this experiment, no completeness claim is assumed, and the current plan/feed was not silently changed.

After obtaining the native five-minute export through a supported authenticated route, import and validate it, run the unchanged native v3 gate comparison, then use a separately declared execution-feed comparison if its data pass the coverage checks. Neither additional history nor passing software tests alone establishes profitability or exact replication of the creator's discretionary strategy.
