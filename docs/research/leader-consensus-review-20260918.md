# Leader agreement, market overview, and trading evidence

Reviewed September 18, 2026. Research and read-only logic verification; no execution rules, live settings, orders, or deployment changed.

This is the initial **3.0.1 baseline** review. Later local changes and testing are documented in the [candidate implementation audit](leader-context-implementation-20260918.md); the [channel review](socrates-channel-review-20260918.md) records the subsequent creator-source verification.

## Conclusions

1. The current rule does **not** require all seven leaders to agree. It requires at least four agreeing and **zero opposing** reactions. Five Down / one Up / one Waiting therefore fails because of the opposing reaction.
2. The primary videos describe both a majority and avoiding mixed evidence. They do not specify a numerical dissent limit. Our zero-opposition veto is an engineering interpretation, not a recovered creator formula or an empirically established optimum.
3. The app uses higher-timeframe Nasdaq **location and event context**. It does not separately classify the overall trend as up, down, or ranging. Such a display would require a defined measurement rule; making it an entry requirement would be a strategy change.
4. Broader research supports testing specific hypotheses with explicit instruments, horizons, costs, and market conditions. It does not validate this app's particular combination of QQQ levels, seven leader reactions, and VIX confirmation.

## What the code actually does

Audited checked-out source: `ad28f1029426f691a6bc9af5c0a5f4cb967b5b92`, app version 3.0.1. This is a source audit, not a fresh verification of the hosted broker state.

- [`AnalysisPolicy`](../../pivot/strategy.py) specifies `minimum_leaders=4` and `maximum_opposition=0`.
- `_leader_confirmation` also rejects missing current five-minute observations and internally conflicting leader reactions.
- Each of seven companies receives one vote. The labels describe qualifying reactions at the app's estimated zones, not daily price changes, general trends, or measured net order flow.
- [`web/model.mjs`](../../pivot/web/model.mjs) labels every absent direction as `Waiting`. A healthy absence of a qualifying reaction differs from missing/stale data; the underlying reason matters.

Pure-function checks against the current implementation passed:

| Synthetic leader readings | Bearish leader confirmation |
| --- | --- |
| Four Down, three healthy neutral | Pass |
| Five Down, one Up, one healthy neutral | Block |
| Seven Down | Pass |
| Three Down, four healthy neutral | Block |
| Five Down, one missing observation, one healthy neutral | Block |

These checks establish software behavior only. Passing this gate neither proves profitability nor establishes that the remaining Nasdaq, VIX, price geometry, or broker checks pass. They do not reproduce the user's live snapshot or diagnose every reason for no orders that day.

### Broader overview versus entry direction

`analyze` reads completed QQQ hourly candles, four-hour interaction areas, and previous-session high/low. The two location methods are evaluated independently. `location_events` identifies breaks/retests or previous-day boundary sweeps. `_evaluate_event` chooses a candidate trade direction from qualifying leader reactions and then checks opposite VIX evidence.

The Nasdaq break direction and candidate trade direction can differ. Neither is a formal overall-trend classification. The UI shows watched areas, events, confirmation checks, and eventual candidate direction; it has no separate general-trend display.

An informative future overview could distinguish:

| Item | Meaning | Status |
| --- | --- | --- |
| Broader structure | A precisely defined higher-timeframe up/down/range measure | Not implemented; definition and evaluation needed |
| Current location | Position relative to four-hour areas and previous-day boundaries | Implemented with declared zone approximations |
| Setup | Break/retest or previous-day boundary sweep, including age and invalidation | Implemented |
| Participation | Counts and reasons for leader reactions; dissent and missing data shown separately | Counts/reasons exist; no index-weighted contribution measure |
| VIX evidence | Actual index observation, freshness, and qualifying reaction | Implemented; predictive value remains a hypothesis |
| Readiness | Exact remaining analysis or execution blocker | Existing diagnostics; trend context should not conceal blockers |

This is a proposed explanatory structure, not a claim that adding a trend filter improves returns. Displaying measured context and forbidding a trade based on that context are separate decisions.

## Primary recordings

These passages were checked in the complete reading transcripts and corresponding raw ASR. The transcripts are machine-assisted, not certified verbatim; linking words in the mixed-evidence passage are unclear. This review did not newly retranscribe the recordings.

| Source | Evidence | Limit |
| --- | --- | --- |
| [V2, 00:41–00:51](../video-transcripts/video-2.srt) | Majority buying supports looking for longs; majority selling supports looking for shorts. | No exact voting cutoff, weighting, or calibrated probability. |
| V2, 00:51–01:06 | Discusses mismatched signals and standing aside. | Does not say whether one dissenter among five agreeing leaders always disqualifies a setup. |
| [V3, 00:18–00:57](../video-transcripts/video-3.srt) | Starts at repeatedly interacted four-hour areas, then hourly breaks/retests. | Defines location context, not a formal trend classifier. Manual zone construction is incompletely specified. |
| V3, 01:01–01:25 | Considers a short following an upward break, with leaders selling from supply; emphasizes Nvidia and Apple in the example. | Does not establish mandatory trend alignment or mandatory agreement from those two companies for every trade. |
| V3, 02:18–02:27 | Several leaders selling provide confirmation. | Does not specify all seven, four-of-seven, or zero opposition. |

The explicit four-hour-bias sequence belongs to supplementary V1, as recorded in [`rulebook.py`](../../pivot/rulebook.py). Importing that sequence as mandatory would mix the source families the owner asked us to keep separate.

The creator's [October 2024 Nasdaq analysis](https://socratesinvestments.com/2024/10/28/analysis-of-nasdaq-gold-using-supply-and-demand-levels-october-28-2024/) also describes conditional reactions at supply/demand and changing direction when levels break. It is qualitative supporting context, not validation of a numeric leader threshold or evidence of net returns.

## Broader research: evidence and limits

| Topic and primary source | Finding | Implication for this app |
| --- | --- | --- |
| [Nasdaq: Understanding the Nasdaq-100 Index](https://www.nasdaq.com/articles/global-indexes/nasdaq-100-index), August 2026 | The index uses modified market-capitalization weighting. Constituents have unequal influence. | Seven equal votes are a participation measure, not a reconstruction of index performance. Actual component contribution also depends on move size and consistent measurement windows. Weighting alone does not prove predictive value. |
| [Gao et al.: Market Intraday Momentum](https://profiles.wustl.edu/en/publications/market-intraday-momentum/), 2018 | In 1993–2013 SPY data, the opening half-hour return measured from the previous close predicts the final half-hour return. Results vary with volatility, volume, and news conditions. | Supports a time-specific momentum hypothesis. It does not establish arbitrary five-minute momentum entries or this leader-voting system. |
| [Baltussen et al.: Hedging Demand and Market Intraday Momentum](https://academicweb.nd.edu/~zda/intramom.pdf), 2021 | Across more than 60 futures markets, earlier-session returns predict the final half-hour. The main results omit trading costs; the paper discusses the limits this places on exploitability. | Entry and exit windows matter. Predictability and achievable net profit are different claims; futures results do not directly establish QQQ execution results. |
| [Heston, Korajczyk and Sadka: Intraday Patterns in the Cross-section of Stock Returns](https://arxiv.org/pdf/1005.3535), 2010 | Identifies short-lived reversal associated with liquidity imbalances and bid–ask bounce, plus same-time-of-day continuation across days. | Reversal and continuation can coexist at different horizons. Apparent reversals in last-trade prices require checking executable quotes and costs. This is not validation of our zone detector. |
| [Cboe: What VIX and VIX1D Attempt to Measure](https://www.cboe.com/insights/posts/what-the-vix-and-vix-1-d-indices-attempt-to-measure-and-how-they-differ), 2023 | VIX measures annualized, non-directional expected S&P 500 volatility over 30 days. Its inverse relationship with equities has exceptions. | Opposite VIX movement is a testable strategy condition, not a mathematical guarantee of the next Nasdaq move. |
| [Bailey et al.: The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Choosing among many historical configurations creates selection bias; an ordinary holdout alone does not account for the number of trials. | Record every tried rule and reserve genuinely unseen evaluation. Reusing the same period to fix each disappointing outcome makes it development data. |

None of these sources specifies an optimal count of agreeing Magnificent Seven stocks. Five agreeing readings also do not represent five independent observations or a five-in-seven probability of profit. A win-rate claim is insufficient without average win, average loss, and costs.

## Bounded next experiment, not a production change

Keep the two primary video methods separate and hold Nasdaq event logic, VIX, zones, sizing, exits, and data-quality rules constant. A small proposed leader comparison is:

- Baseline: at least four agreeing, zero opposing.
- Candidate A: at least five agreeing, at most one opposing.
- Candidate B: at least four agreeing, at most one opposing.

These are hypotheses prompted by the wording ambiguity, not endorsed live thresholds. Freeze and register the complete comparison before evaluating it. Do not pick whichever variant admits the user's current example.

Measure distinct opportunities, rejected reasons, executable orders, net expectancy after costs, drawdown, and results by method/direction/session. Adjacent five-minute observations of the same event are not independent trades. Existing six-session v2 replay data has already been examined and cannot become a fresh unseen test simply by rerunning another threshold. The old v1 comparison that generated no trades cannot settle this v2 question either.

Log any proposed overview measure first as context and evaluate its relationship with outcomes. A hard trend filter, index-weighted voting, and adding new strategy families would be additional experiments, not simultaneous fixes. Retain strict checks for missing or contradictory data in all comparisons.

Research can maintain a sourced strategy playbook. Memorizing more named strategies does not establish an edge; reproducible evidence for the actual implementation is the useful standard.
