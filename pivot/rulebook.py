"""Source-backed rule map, not a claim that the clips define an executable algorithm.

The recordings do not establish a cross-video sequence. Groups below organize
source evidence; they are not two commissioned trading systems.
"""

RULES = [
    {'id':'v1_bias','group':'tape_sequence','name':'Find the 4-hour bias','source':'V1 00:05–00:10','timeframe':'4h','status':'explicit','missing':'How bias is measured is not defined'},
    {'id':'v1_pullback','group':'tape_sequence','name':'Wait for the opposing 30-minute move','source':'V1 00:10–00:13','timeframe':'30m','status':'explicit_example','missing':'4h up / 30m down is described; the full reversal rule is not formalized'},
    {'id':'v1_location','group':'tape_sequence','name':'Wait for supply or demand to be hit','source':'V1 00:14–00:21','timeframe':None,'status':'explicit','missing':'Zone construction and width are not defined'},
    {'id':'v1_structure','group':'tape_sequence','name':'Confirm a 5-minute structure shift','source':'V1 00:21–00:23','timeframe':'5m','status':'explicit','missing':'Swing definition and precise entry trigger are not defined'},
    {'id':'v1_tape','group':'tape_sequence','name':'Inspect tape for absorption OR exhaustion','source':'V1 00:23–00:25','timeframe':'tape','status':'explicit','missing':'Measured trade/quote data and a validated detection rule are required'},
    {'id':'v1_vwap','group':'tape_sequence','name':'Target VWAP','source':'V1 00:25–00:26','timeframe':None,'status':'explicit','missing':'VWAP anchor, stop and invalidation are not specified'},
    {'id':'v23_market','group':'nasdaq_sequence','name':'Use Nasdaq as the traded market','source':'V2 00:25–00:29; V3 chart','timeframe':None,'status':'explicit','missing':'QQQ, NQ and individual stocks must not be treated as interchangeable'},
    {'id':'v2_levels','group':'nasdaq_sequence','name':'Mark previous-day high and low, then wait for a sweep','source':'V2 00:01–00:24','timeframe':'previous day / higher timeframe','status':'explicit','missing':'The clip calls a break beyond the level a sweep; a close-back-inside is not explicitly required'},
    {'id':'v3_levels','group':'nasdaq_sequence','name':'Premark repeated 4-hour touches or breaks','source':'V3 00:18–00:42','timeframe':'4h','status':'explicit','missing':'Levels are manually drawn; wick/body choice, clustering and zone width are not defined'},
    {'id':'v3_event','group':'nasdaq_sequence','name':'Move to 1 hour; wait for break and retest','source':'V3 00:42–00:58','timeframe':'1h','status':'explicit','missing':'The short-after-break-above example is not a complete continuation/reversal rule'},
    {'id':'v23_leaders','group':'nasdaq_sequence','name':'Confirm technology leaders at supply/demand; skip conflicting evidence','source':'V2 00:29–01:14; V3 01:08–01:43','timeframe':None,'status':'explicit','missing':'Majority is described; no exact count, weights, all-seven rule, or mandatory AAPL+NVDA gate is given'},
    {'id':'v23_vix','group':'nasdaq_sequence','name':'For a short, VIX rises from demand; reverse for a long','source':'V2 01:16–01:36; V3 01:43–02:27','timeframe':'15m shown in V3','status':'explicit','missing':'Direct VIX required; the 15m view is extra confirmation, not a proved universal timeframe rule'},
]
GROUPS = {
    'tape_sequence': {'name':'Bias → pullback → structure → tape → VWAP','source':'Recording 1 · ElderTrades post',
                     'relationship':'The recording does not refer to the other two clips.'},
    'nasdaq_sequence': {'name':'Nasdaq location → leaders → VIX','source':'Recordings 2 and 3 · Socrates Dimataris posts',
                       'relationship':'These clips describe related location-first confirmation; their level variants are not explicitly ordered.'}}
UNRESOLVED = [
    'Owner clarified V2 and V3 are primary; V1 is supplementary only.',
    'Exact zone, sweep/retest and leader rejection thresholds are absent.',
    'Stop placement, position risk, invalidation, and exits for the Nasdaq sequence are absent.',
    'V2/V3 do not require the first clip’s tape, 5-minute shift or VWAP target.',
    'QQQ and the documented execution policy are app choices; they are not rules specified by the videos.'
]


def rulebook():
    return {'version':'video-evidence-2026-09-16-v2', 'architecture_status':'one_primary_nasdaq_flow',
            'groups':{k:v for k,v in GROUPS.items() if k=='nasdaq_sequence'}, 'rules':[r for r in RULES if r['group']=='nasdaq_sequence'], 'unresolved':UNRESOLVED[1:], 'supplementary_source':'V1 is background only; no tape, 5m or VWAP execution requirements',
            'can_enter':False, 'reason':'Source evidence is informational; the executor checks permission, fresh signals and broker state',
            'execution_choices':'See the versioned policy shown when enabling live money'}
