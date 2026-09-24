# Verified-trader leaderboards: what profitable traders' records say about our setups

Research date: September 24, 2026. This is research only. It changes no live rules.

Sources: Kinfo's broker-verified all-time profit leaderboard (collected and analysed here), interviews with and writing by the top Kinfo traders, and a survey of other public sources of trading records. The per-trader metrics are in [kinfo-leaderboard-20260924.csv](kinfo-leaderboard-20260924.csv). The collector is `research/kinfo_leaderboard.py`.

## Summary

1. **Kinfo exposes a lot without a login, but not individual trades.** The leaderboard API and each profile's performance page publish summary statistics and the trader's complete **daily realized P&L series**, going back as far as 2014. Only the most recent five trades are shown, and most top profiles mask the ticker (`***`). Kinfo's own page code loads full trade lists only for Pro subscribers, and several traders sell their tickers separately (`lockTickers`). We did not try to get around either restriction.
2. **The most profitable verified traders are not trading our instrument.** Of the top 15 all-time, 11 trade small-cap stocks or sell options premium. The traders who do trade Nasdaq index products use 0DTE NDX options or MNQ futures (Jay Gamma Trader, jurn), hold NQ/ES for days (Trader5000), or make a once-a-day TQQQ/SQQQ decision at the close (Mallik/TQQQTrader). None published a rulebook we can copy.
3. **What does transfer is the shape of a durable edge.** Across the 196 traders with at least 250 trading days and 300 trades:
   - **Profits are concentrated.** For the median trader, the best 5% of days produce 120% of net profit, so the other 95% of days lose money on balance. An exit that caps winners, such as a fixed target, removes the days that carry the account.
   - **A high win rate hides a fat left tail.** Among traders who win 80% or more of their trades, the median worst day is **33 times** the average green day. For traders under 50% it is 10 times. The biggest disasters on the board all come from high-win-rate, small-payoff styles. madaz won 93% of trades with a profit factor of 1.17 and a $7.9M drawdown. jurn won 76% with a profit factor of 1.07 and lost $1.23M in one day. Aikido Trading Enigma won 70% with a profit factor of 1.18 and lost $3.63M in one day.
   - **The survivors are asymmetric or tightly capped.** Kyle Williams was profitable in 8 of 8 years (55% win rate, profit factor 1.86, average winning trade 1.5 times the average loser). edu_trades was profitable in 8 of 8 years with a maximum drawdown of $134k on $3.4M of profit, trading only the morning with position size set from the planned stop.
4. **The strongest QQQ/NQ-specific evidence comes from published, reproducible research, not leaderboards.** These are the 5-minute opening-range breakout on QQQ and the time-of-day "noise area" momentum strategy on SPY and NQ. Both have a low win rate and a large payoff, trade with volatility-normalised breakouts, and exit with a trailing stop or at the close rather than at a fixed target.
5. **Base rates.** Academic studies find that under 1% of day traders are predictably profitable after fees (Taiwan), and that 97% of people who day-traded Brazilian index futures for more than 300 days lost money. A leaderboard is mostly the survivors of a lottery. Use it to generate hypotheses and test them in our replay framework.

## 1. Kinfo: what is public and how it was collected

| Data | Public without login | Where it comes from |
| --- | --- | --- |
| Leaderboard: profit, trades, win %, profit factor, average gain, gross won/lost, and the same for this week, 7/30/90 days, 1 year and year to date | Yes | `GET api.kinfo.com/api/Portfolio/tradePortfoliosByPerformanceFiltered/{sort}/{period}/{page}/{6 asset flags}`, the call the public page makes |
| Profile: description, X/YouTube/site, privacy flags | Yes | same API, and the performance page |
| Daily realized P&L for the whole history | Yes | `tradeProfitDaily`, embedded in the server-rendered `kinfo.com/portfolio/{id}/performance` page |
| Last 5 closed trades (entry/exit time, long or short, asset class, P&L, fills) | Yes, but the ticker is usually masked | `tradePerformanceHistory` on the same page |
| Full trade list, and the Trades tab beyond its first page | **No.** Needs a subscription, and some tickers are sold separately | `api/Portfolio/{id}/trades/{page}` returns only the latest trade when unauthenticated |

We collected three boards on September 24, 2026: all-time profit (400 rows), the futures filter (79 rows) and equities-only (400 rows). The futures filter means "has traded futures", not "trades only futures". We then fetched the performance page for the top 120 of each board, 264 portfolios in total. All metrics are computed from **realized P&L by exit date**, so drawdowns are lower bounds (open losses are invisible), and hour-of-day values are approximate because Kinfo timestamps carry no time zone.

## 2. The all-time top 15 (broker-verified, realized)

| # | Trader | Profit | Trades | Win % | PF | Green days | Max drawdown | Profitable years | What they trade |
| - | --- | --: | --: | --: | --: | --: | --: | --: | --- |
| 1 | Steven Dux | $11.77M | 4,334 | 61% | 1.77 | 72% | $0.90M | 9/10 | Small-cap shorts, panic dip-buys. Inactive since 2025 |
| 2 | Kyle Williams | $10.03M | 5,151 | 55% | 1.86 | 55% | $0.75M | **8/8** | Small-cap first-red-day shorts, panic dip-buys |
| 3 | David Veprek | $10.02M | 22,708 | 48% | 1.29 | 61% | $1.59M | 10/11 | Small-cap catalyst trades, long and short |
| 4 | madaz | $9.02M | 53,701 | 93% | 1.17 | 83% | **$7.91M** | 6/10 | Small-cap scalps held about 2 minutes |
| 5 | Aikido Trading Enigma | $7.21M | 11,147 | 70% | 1.18 | 64% | $4.21M | 5/7 | Microcap shorts driven by dilution |
| 6 | jurn | $5.56M | 6,988 | 76% | **1.07** | 64% | $4.06M | 2/3 | **Selling 0–1DTE NDX options** |
| 7 | edu_trades | $3.40M | 28,348 | 73% | 1.75 | 81% | **$0.13M** | **8/8** | Premarket-high breakouts, 7:30–10:30 ET |
| 8 | Bobdog | $3.40M | 4,445 | 75% | 2.67 | 76% | $0.33M | 2/2 | Selling options premium |
| 9 | ravenloft | $2.97M | 2,197 | 85% | 3.41 | 85% | $0.25M | 3/3 | Options and stock swing trades |
| 10 | NeilStrikes | $2.72M | 1,525 | 62% | 1.49 | 62% | $1.56M | 7/13 | Options spreads |
| 15 | Jay Gamma Trader | $1.77M | 4,371 | 46% | 1.43 | 58% | $0.30M | 2/3 | **NDXP 0DTE spreads, MNQ futures** |

"Profitable years" counts calendar years with positive realized P&L out of the years with trading activity.

The traders with the steadiest risk-adjusted results are not at the top by dollars. Among those with at least 3 years, 250 trading days and $200k of profit, ranked by annualised daily Sharpe:

| Trader | Profit | Win % | Average green day ÷ average red day | Daily Sharpe | Max drawdown | Style |
| --- | --: | --: | --: | --: | --: | --- |
| V85 | $0.76M | 53% | 1.96 | 6.9 | $8k | 171,784 stock scalps held about 16 seconds |
| Gex | $1.30M | 82% | 1.19 | 6.8 | $45k | Options premium selling guided by dark-pool and gamma data |
| DipNrip | $0.77M | 48% | 1.76 | 6.5 | $15k | Stock scalps held about 5 minutes |
| edu_trades | $3.40M | 73% | 0.74 | 4.9 | $134k | Morning premarket-high breakouts |
| stratataaa | $1.62M | 39% | 1.94 | 3.1 | $59k | Stock day trades held about 1 hour |
| DonJuan | $0.85M | 38% | 1.93 | 2.7 | $63k | Stock scalps held about 7 minutes |
| Kyle Williams | $10.03M | 55% | 1.94 | 2.6 | $751k | Small-cap day and swing trades |

These are Sharpe ratios on daily dollar P&L, not on account returns. Treat them as a ranking of consistency, not as returns an investor could earn.

## 3. Traders on the index products we trade

| Trader | Verified | Instrument and horizon | Stated method | What it suggests for us |
| --- | --- | --- | --- | --- |
| **Jay Gamma Trader** (102193) | $1.77M. PF 1.43, 46% wins. $1.70M of it in 2026. Profile created in July 2026 with history imported from 2024 | NDXP 0DTE debit spreads held to settlement; MNQ intraday (e.g. a 17-minute short) | Nothing public. The name suggests dealer-gamma levels, which is unverified | Directional NDX intraday trading with defined risk can work, but the record is short and concentrated |
| **jurn** (25623) | $5.56M. PF 1.07. Worst day −$1.23M. On 2026-09-22 his short calls lost $340k and $310k | Selling 0–1DTE NDX options | None public | Warning: **fading an index trend day is where the fat tail lives** |
| **Trader5000** (27008) | $1.37M. PF 1.15. Max drawdown $2.02M. Worst day −$665k, around the August 2024 yen-carry unwind | Long NQ and ES held from days to months | None public | Not intraday. A reminder about how leveraged index exposure behaves on shock days |
| **Mallik / TQQQTrader** (14410, [@RealTQQQTrader](https://x.com/RealTQQQTrader)) | $888k. 39% wins, PF 1.63 | TQQQ, and **SQQQ for shorts, the same inverse-ETF approach as our PSQ** | Seven systems. One decision about 10 minutes before the close. Regime from the 20 and 250-day moving averages. Size scales with how far NDX is from those averages ([Kinfo episode](https://www.youtube.com/watch?v=RrrF1DkXoi8)) | A regime filter and size tied to distance from the mean |
| **Tori.Trades** (80063, [@toritrades](https://www.youtube.com/watch?v=VTEQ2fhGLqE)) | $515k on 137 trades. PF 2.76 | Futures (lately platinum), held for hours to days | **Trendlines on the 4h chart with 2–3 touches.** Trades both bounces and breaks. **The line itself is the stop**, exit on a close through it, then trail behind swing points | The closest published method to our 4h levels touched twice. Her edge comes from few, large, held trades |

## 4. What the top traders say they do (self-reported)

Everything in this section is the traders' own claim, taken from interviews and their own sites. Rule lists found on forex.in.rs and aitradingkit.com appear to be AI-written summaries with no primary quotes, so they are excluded.

- **Steven Dux** ([blog](https://www.stevenduxi.com/blog/8-steven-dux-strategies-for-achieving-consistent-profits), [Humbled Trader interview](https://creators.spotify.com/pod/profile/humbled-traders0/episodes/Verified-8-Figure-Trader-Explains-Statistics--Trader-Psychology--Steven-Dux-e2c35nd)):
  - Backtests each pattern on about a year of examples and **sizes by the pattern's measured win rate**.
  - Risks 1–2% per trade and **cuts size by half after any mistake**.
  - His widely cited $6M day on DWAC does not appear in his Kinfo portfolio; his largest verified day is +$384k.
- **Kyle Williams** ([Investors Underground](https://www.investorsunderground.com/kyle-williams-interview/), [Friendly Bear](https://www.friendlybearpodcast.com/1782340/episodes/9997661-kyle-williams-detailed-explanation-on-first-red-day-short-sell-setup)):
  - Raises risk slowly, by about 10% a month, and has a hard maximum loss per day.
  - "Let strength come to you before shorting": he waits for the failure instead of predicting it.
- **edu_trades** ([x-trader interview](https://www.x-trader.net/entrevista-a-edu-trades/)):
  - Trades breakouts above the premarket high, from 7:30 to 10:30 ET, and is done by 15:00.
  - **Sets position size from the distance between the planned exit and the stop.** Sizes up only on A+ setups; B and C setups are steady "cash flow".
- **madaz** ([Bookmap interview](https://bookmap.com/blog/trading-depth-interview-15-madaz-money)):
  - Targets 20–30 cents, 20–30 times a day, in the first one to two hours. VWAP is his only indicator.
  - No explicit stop rule, and the record above shows the result: a $7.9M drawdown from June 2021 to November 2023.
- **Alex Temiz** (≈$16M on Kinfo according to [this episode](https://www.youtube.com/watch?v=Q1SEFBswZ4w)): trades one hour a day and uses broker auto-liquidation as a hard daily loss limit.

## 5. Other sources, ranked by usefulness to us

| Source | Verification | Public data | Can we collect it? | Relevance to QQQ intraday |
| --- | --- | --- | --- | --- |
| **Hyperliquid** (`api.hyperliquid.xyz/info`; [leaderboard](https://stats-data.hyperliquid.xyz/Mainnet/leaderboard)) | On-chain, cannot be faked | **Every fill of every wallet.** Leaderboard of 46,703 wallets. The HIP-3 `xyz` venue lists **`xyz:XYZ100` (a Nasdaq-100 perpetual)** and perpetuals on AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA and VIX | Yes, a JSON API with no auth. Only each wallet's latest 10k fills are served, so they must be archived continuously | **High.** It is the only fully verifiable per-trade dataset on the Nasdaq-100 and our seven leaders. The top wallets by 30-day P&L trade crypto, not XYZ100, so XYZ100 specialists have to be found from its trade stream |
| **Published rules** (Zarattini, Aziz, Barbon; [SSRN 4416622](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622), [4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284), [SPY momentum](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/), [NQ replication](https://www.quantitativo.com/p/intraday-momentum-for-es-and-nq)) | Backtests, not live P&L | Complete rules | We can reproduce them ourselves | **High** |
| Kinfo (this study) | Broker-linked; realized only; traders choose which accounts to connect | Daily P&L and summary statistics | Yes, via the public endpoints above | Medium: few index traders |
| [World Cup Trading Championships](https://www.worldcupchampionships.com/) | Real money, audited | Returns only | — | Methods from books: Williams' volatility breakout (open ± k × prior-day range); Unger's prior-session level breakouts with time filters, winning 4 times; Davey's robustness testing |
| [Profit.ly](https://profit.ly/leaderboard/user/top/alltime) | Posted trades are verified, but the trader chooses what to post | Entry and exit for each posted trade | Yes (robots.txt allows it with a 2-second crawl delay) | Low: small caps |
| Darwinex, Collective2, Myfxbook, FXBlue, Tradervue, TopstepX Octagon, TradingView, crypto copy-trading | Varies | Trade history hidden, behind Cloudflare, disallowed by robots.txt, or login-only | Not without accounts | Low to medium |
| X accounts: [@alphatrends](https://x.com/alphatrends) (anchored VWAP from pivots), [@ripster47](https://x.com/ripster47) (EMA clouds on SPY/QQQ), [@TomHougaard](https://x.com/tomhougaard) (DAX/NQ, adding to winners), [@AnthonyCrudele](https://x.com/AnthonyCrudele) (ES/NQ prior-day levels) | Their P&L is not verified | Ideas and charts | Needs a login to read | Useful for ideas about retests |

### Published QQQ/NQ rules worth reproducing

- **5-minute opening-range breakout on QQQ.** The direction of the first 5-minute candle sets the side. Skip doji candles. The stop is the candle's opposite extreme. The target is 10R, or the close if it comes first.
  - Results: about a 24% win rate and +0.13R per trade on average, 2016–2023.
  - The paper assumed no slippage.
  - A version on stocks with unusual opening volume shows the edge fading sharply beyond 15 minutes: Sharpe 2.81 with a 5-minute range, 1.43 with 15 minutes, and near zero with 30 or 60 minutes.
- **Noise-area momentum.**
  - The band is the open × (1 ± the average absolute move from the open at the same time of day over the previous N days). Trade breaks outside the band.
  - Trail the stop at max(band, VWAP), flat at the close, and size to a volatility target.
  - Results: SPY 2007–2024 Sharpe 1.33. On NQ with a 90-day lookback, Sharpe 1.67, a 38% win rate and a payoff ratio of 2.25. A critique finds it works better when VIX is above 20.

## 6. What to test in our replay framework

These are ordered by evidence strength and by fit with the current Socrates flow. Each is an experiment to add to `research/`, not a live rule change.

1. **Trailing exit versus the fixed target.** Today the target is fixed and must meet the minimum reward-to-risk. The leaderboard (the best 5% of days produce more than all net profit) and the published QQQ/NQ strategies (low win rate, large payoff, trailing exits) both say the right tail matters most.
   - Test (a) the current target; (b) half off at the target and the rest trailed behind hourly swing points or session VWAP; (c) all of it trailed; (d) held until the forced close.
   - Report win rate, average R, and how much of total R comes from the top 5% of trades.
2. **Time-of-day buckets.** The morning-only traders (edu_trades, madaz, Temiz) and the ORB decay evidence suggest the edge is front-loaded.
   - Group our events by entry time: 09:30–10:30, 10:30–11:30, 11:30–14:00 and 14:00–15:30.
   - Test an 11:30 cutoff for new entries against today's rule of no entries in the final 30 minutes.
3. **Trend-day guard for counter-trend retests.** jurn's losses came from being short into an index trend. Flag a trend day when:
   - the opening hour's range is more than 1.5 times its 20-day average, and
   - at least 6 of our 7 leaders are on the same side of their session VWAP.

   On flagged days, test disabling retest trades against the trend.
4. **Stop at the level, not beyond a buffer.** Following Tori.Trades, exit on a 1h (or 5m) close back through the traded area. For sweeps, place the stop beyond the sweep's extreme. Compare this with the current stop and add a no-progress exit after N bars.
5. **Regime filter from Mallik.** Allow PSQ shorts only when QQQ is below its 20-day average, or at half size. Allow longs at full size only when it is above. Also test the noise-area band as a gate: only take a retest when QQQ is outside its time-of-day noise band.
6. **Premarket high and low as another type of level.** Every small-cap source treats the premarket high and low, VWAP and the prior-day high as the key decision points. We already use the prior-day extremes and session VWAP. Add premarket extremes for QQQ and the leaders, noting that our data is regular-session only (a known limit).
7. **Size by measured edge, with drawdown throttles.** This follows Dux, Kyle Williams and edu_trades.
   - Keep live statistics per setup type: retest or sweep, 4h area or prior-day extreme, number of leaders agreeing.
   - Scale size with each setup's rolling expectancy. Halve size after two red days, and add a hard daily stop of −2R.
   - Our purchase amounts are fixed today, so this is for later, once replay shows which setups carry the edge.
8. **An opening-range breakout baseline.** Run the published 5-minute QQQ ORB through the same replay, costs and PSQ short proxy. If our level-retest method cannot beat it after costs, that is decisive.
9. **Hyperliquid XYZ100 wallet study (longer term).**
   - Archive regular-hours XYZ100 fills.
   - Select wallets that were profitable in at least 3 of 4 separate windows with at least 100 round trips.
   - Measure how often their entries sit at our 4h areas once XYZ100 prices are mapped to QQQ.
   - This is the only way we found to check, on verified individual trades, whether persistently profitable Nasdaq traders act at our levels.

Also keep the minimum reward-to-risk floor. The data argues against any change that raises the win rate by shrinking targets or widening stops.

## 7. Caveats

- **Kinfo verifies what traders connect.** Traders can connect only some accounts and choose a starting point, and Kinfo reports realized P&L, so losing positions left open do not count. Kinfo's own posts acknowledge this ([how to game Kinfo](https://kinfo.com/blog/how-to-game-kinfo-or-at-least-try), [facts and misinformation](https://kinfo.com/blog/facts-and-misinformation)). Large unrealized balances (Veprek +$15.2M, jurn −$0.77M) show how much sits outside the realized figures.
- **The all-time board rewards past market regimes.** Dux has been inactive since 2025. madaz, Aikido and Veprek are down over the last year or month. 137 of the 196 traders with long records were profitable over the last year.
- **Survivorship bias.** We only see traders who chose to share and are still listed. The academic base rates ([Barber et al.](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf), [Chague et al.](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101)) are the right prior.
- **Most strategy descriptions are self-reported.** None of the index-product traders published their rules.

## Reproduce

```sh
python -m research.kinfo_leaderboard collect --out <dir> --top 120 --board-limit 400   # about 15 minutes, 1 request per second
python -m research.kinfo_leaderboard analyze --data <dir> --out <dir>/analysis.json
```

The collector only makes read-only GET requests to the public endpoints listed in section 1. Raw downloads are not committed. The CSV next to this document is the analysed snapshot.
