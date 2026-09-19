# Crypto market coverage audit — September 19, 2026

## Conclusion

Watch **BTC/USD, ETH/USD, SOL/USD, LINK/USD and XRP/USD**. Bitcoin is the market demonstrated in the recording; the other four are explicitly separate applications of the same rules. Watching a market does not enable trading it. Preserve the owner's saved trade selection when expanding this list.

This is an engineering/data-quality choice, not evidence that these markets or the strategy will be profitable. Only BTC and XRP passed complete-day coverage and the spread screen in this small snapshot. Adding markets does not repair missing candles or guarantee a qualifying long setup.

## What was measured

Four read-only Alpaca requests: active crypto assets, latest USD quotes, native five-minute bars for eight candidates, and ten minutes of historical quotes for the same eight. No account settings, orders or permissions were changed. No private account identifiers are included here.

- Assets and latest quotes: **2026-09-19 21:42:47 UTC / 5:42:47 p.m. ET**.
- Completed bars: **04:00–21:40 UTC / midnight–5:40 p.m. ET**, received at approximately 21:43:35 UTC. There were 212 expected five-minute bars per market, including 48 in the initial four hours.
- Quote sample: **21:34:06–21:44:06 UTC / 5:34–5:44 p.m. ET**. Results below are quote-event statistics, not time-weighted spreads or a long-term liquidity study.
- Both historical responses had no next-page token; the sample was complete for the requested period.

| Market | Five-minute bars / 212 | Missing in first 4h | Median spread | 95th-percentile spread | Quote events / 10m | Assessment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| BTC/USD | 212 | 0 | 0.0306% | 0.0443% | 553 | Video baseline; complete source in this sample |
| ETH/USD | 201 | 2 | 0.0354% | 0.0941% | 69 | Watch; 11 daily gaps block this source today |
| SOL/USD | 210 | 1 | 0.0870% | 0.1539% | 69 | Watch; two daily gaps block this source today |
| LINK/USD | 211 | 0 | 0.2351% | 0.3206% | 194 | Watch; one post-range gap blocks this source today |
| XRP/USD | 212 | 0 | 0.4059% | 0.5670% | 154 | Watch; complete source, but costs sometimes exceed the spread screen |
| DOGE/USD | 210 | 2 | 0.3864% | 0.5202% | 236 | Excluded from this small initial expansion |
| AVAX/USD | 212 | 0 | 0.6722% | 0.9633% | 372 | Median spread exceeds the app's 0.5% ceiling |
| LTC/USD | 211 | 0 | 0.7030% | 0.9610% | 90 | Source gap and median spread above the ceiling |

Quotes sometimes remained unchanged for more than 15 seconds—even BTC's maximum interval between quote events was 40.9 seconds. A successful HTTP response is not proof of a new quote. Preserve the source timestamp, positive bid/ask sizes, spread check and current execution freshness checks. SOL's smallest displayed best-ask depth in this sample was only $1.59; a $5 immediate limit order can partially fill. Displayed depth is not a fill guarantee.

## Size, fees and venue

The current Assets API returned 36 active, tradable USD pairs. For all eight audited candidates, `min_trade_increment` and `price_increment` were `0.000000001`; the dynamic minimum order quantities were worth approximately $1 at the sampled ask. Rounding a $5 purchase down to the returned quantity increment remained within 99%–100% of $5 and above each minimum. These are measured metadata, not constants to embed in the app.

Alpaca's current [retail coin-pair FAQ](https://alpaca.markets/support/alpaca-crypto-coin-pair-faq) describes a dynamic $1-equivalent minimum. Some Broker API documentation gives a different minimum, and older tables contain obsolete increments. Validate the actual asset response for each order instead of selecting the most convenient documentation value.

The published starting fee tier is 0.15% maker / 0.25% taker. Immediate marketable limits ordinarily take liquidity. Allowing 0.25% on both entry and exit adds approximately 0.5% before spread and other execution allowance—roughly 2.5 cents on $5. A larger target purchase does not remove this percentage cost. Buy fees reduce the received crypto quantity; sell fees reduce received USD. The supported route is spot long-only, using cash buying power. See [Alpaca's crypto trading and fee documentation](https://docs.alpaca.markets/us/docs/crypto-trading).

The sampled `us` feed is Alpaca's own crypto venue, not a consolidated market-wide volume series. Its native candles can include quote midpoint prices when no trade occurs; zero volume alone is therefore not a missing candle. Missing timestamps are a different problem and must remain visible. See the [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) and [crypto stream documentation](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data).

## Replay versus executable opportunities

Running the existing analyzer against the saved complete source yielded five confirmed BTC events and four XRP events today, **all shorts**. Alpaca spot cannot open those trades. AVAX had an outside close but no confirmed return inside. The other five candidates remained in a data-waiting state because their native day contained gaps.

This is a retrospective analysis of today's received bars, not proof that every bar was available at the historical decision time, a fill simulation, an evaluation of profitability, or permission to replay an expired signal. Preserve the entry deadline and per-symbol event identity.

## Integration requirements

1. Use one reviewed market registry across the reader, broker adapter, ownership aliases, saved settings and interface. Pair symbols and position symbols must map exactly, for example `XRP/USD` ↔ `XRPUSD`.
2. Keep watched markets separate from the owner's saved trading markets. An update must not turn newly watched pairs into live allocations.
3. Retain independent native-candle provenance, missing-slot diagnostics, event identity and history for each market. Do not silently fill missing candles or merge another venue into a day's range.
4. Check current asset eligibility, increments, minimum size, quote age, spread, target costs and shared cash before every new entry. Per-market $5 settings are separate possible allocations, not a $5 portfolio cap.
5. Batch requests or otherwise bound worker load. Alpaca's [historical-bars endpoint](https://docs.alpaca.markets/us/reference/cryptobars-1) applies its result limit across all symbols; pagination must never be mistaken for a missing market or a complete history.
6. Continue managing an owned position if its watch or entry selection changes. A watchlist expansion must not weaken uncertain-order reconciliation or enable crypto shorts.

Longer independent samples and cost-aware out-of-sample evaluation are required before calling any added market a validated strategy improvement.
