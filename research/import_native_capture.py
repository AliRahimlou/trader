"""Validate private native captures and export a frozen-calendar research input.

Offline only. Every retained candle keeps its original timestamp and OHLC.
This does not establish historical quote availability or enable live orders.
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse
import json

from pivot.feeds import ET
from pivot.models import Bar, timestamp
from pivot.native_history import validate_artifact
from research.data import encode_bar
from research.offline import disconnected
from research.validate_v2 import _json, _calendar, ends, load_inputs


def canonical_sha256(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _hash(value, expected, label):
    if not isinstance(expected, str) or canonical_sha256(value) != expected:
        raise ValueError(label + ' content hash mismatch')


def _native_bars(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Native bar array is empty or malformed')
    result=[]
    for row in rows:
        if type(row.get('minutes')) is not int or row['minutes'] != 5:
            raise ValueError('Genuine native five-minute bars required')
        bar=Bar(**{**row, 'end':timestamp(row['end'])})
        if bar.end.second or bar.end.microsecond or bar.end.minute % 5:
            raise ValueError('Native candle timestamp is off the five-minute grid')
        if result and bar.end <= result[-1].end:
            raise ValueError('Native candles must be ordered and unique')
        result.append(bar)
    return result


def _validate_calendar_against_base(source, base):
    calendar=_calendar(source)
    for day, session in calendar.items():
        if day in base.sessions and session != base.sessions[day]:
            raise ValueError('Capture calendar disagrees with frozen exchange calendar')
    return calendar


def validate_qqq(capture, calendar, captured_at):
    if (capture.get('schema') != 'pivot-native-qqq-five-minute-capture-v1'
            or capture.get('research_only') is not True or capture.get('status') != 'captured'):
        raise ValueError('A successful native QQQ capture is required')
    provenance=capture['provenance']
    if (provenance.get('source') != 'alpaca_iex' or provenance.get('source_symbol') != 'QQQ'
            or provenance.get('adjustment') != 'split'
            or type(provenance.get('native_resolution_minutes')) is not int
            or provenance['native_resolution_minutes'] != 5):
        raise ValueError('Expected native split-adjusted Alpaca IEX QQQ')
    received=timestamp(capture['received_at'])
    requested=timestamp(capture['requested_at'])
    if not requested <= received <= captured_at:
        raise ValueError('QQQ request/receipt ordering differs from the bundle timestamp')
    _hash(capture['bars'], capture.get('normalized_sha256'), 'QQQ normalized bars')
    bars=_native_bars(capture['bars'])
    permissible={at for session in calendar.values() for at in ends(session,5)}
    if any(bar.end > requested or bar.end not in permissible for bar in bars):
        raise ValueError('QQQ contains forming or out-of-calendar normalized bars')
    return bars, {**{key:provenance[key] for key in ('source','source_symbol','adjustment','native_resolution_minutes')},
        'requested_at':requested.isoformat(), 'received_at':received.isoformat(),
        'normalized_sha256':capture['normalized_sha256'], 'point_in_time_arrival_verified':False}


def validate_vix(capture, calendar, captured_at):
    # Reconstruct original closed RTH candles from the exact hashed provider
    # response and original receipt; the shared pure validator makes no calls.
    validate_artifact(capture, captured_at)
    bars=_native_bars(capture['bars'])
    permissible={at for session in calendar.values() for at in ends(session,5)}
    if any(bar.end not in permissible for bar in bars):
        raise ValueError('VIX normalized bars are outside their declared calendar')
    return bars, {**capture['provenance'], 'received_at':capture['received_at'],
        'source_updated_at':capture['source_updated_at'],
        'metadata_received_at':capture['metadata_received_at'],
        'raw_response_sha256':capture['raw_response_sha256'],
        'normalized_sha256':capture['normalized_sha256'],
        'metadata_sha256':capture['metadata_sha256'],
        'point_in_time_arrival_verified':False}


def convert(bundle, base, vix_override=None):
    if (bundle.get('schema') != 'pivot-native-capture-v1' or bundle.get('research_only') is not True
            or bundle.get('live_entry_ready') is not False):
        raise ValueError('Unknown server native capture bundle')
    captured_at=timestamp(bundle['captured_at'])
    qcalendar=_validate_calendar_against_base(bundle['sessions'],base)
    vix_capture=vix_override if vix_override is not None else bundle['vix']
    vcalendar=_validate_calendar_against_base(vix_capture['sessions'],base)
    qqq,qp=validate_qqq(bundle['qqq'],qcalendar,captured_at)
    vix,vp=validate_vix(vix_capture,vcalendar,captured_at)
    sessions={day:base.sessions[day] for day in sorted(set(qcalendar)&set(vcalendar)&set(base.sessions))}
    if not sessions:
        raise ValueError('No shared frozen-calendar sessions')
    permitted={at for session in sessions.values() for at in ends(session,5)}
    retained, audit={},{}
    for symbol,bars in [('QQQ',qqq),('VIX',vix)]:
        keep=[b for b in bars if b.end in permitted]
        if not keep:
            raise ValueError(symbol+': no native candles in the frozen calendar')
        retained[symbol]=keep
        byday=Counter(b.end.astimezone(ET).date().isoformat() for b in keep)
        audit[symbol]={'received_bars':len(bars),'retained_bars':len(keep),
            'excluded_outside_shared_frozen_calendar':len(bars)-len(keep),
            'session_coverage':[{'day':day,'expected_bars':len(list(ends(s,5))),
                'actual_bars':byday[day], 'missing_bars':len(list(ends(s,5)))-byday[day]}
                for day,s in sessions.items()]}
    return {'schema':'pivot-native-qqq-vix-five-minute-v1',
        'sessions':{d:{k:t.isoformat() for k,t in s.items()} for d,s in sessions.items()},
        'qqq5':[encode_bar(b) for b in retained['QQQ']], 'vix5':[encode_bar(b) for b in retained['VIX']],
        'qqq_provenance':qp,'vix_provenance':vp,
        'import_audit':{'source_captured_at':captured_at.isoformat(),'source_bundle_sha256':canonical_sha256(bundle),
            'source_vix_capture_sha256':canonical_sha256(vix_capture),'base_inputs':base.inputs,
            'shared_calendar_days':len(sessions),'coverage':audit,
            'historical_live_arrivals_verified':False,'bars_created':0,
            'note':'Only verified original native candles retained; missing bars remain missing. Current receipts are not historical arrivals.'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','native','warmup','legacy','out'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--vix-capture')
    args=parser.parse_args()
    out=Path(args.out)
    if out.exists():
        raise ValueError('Refusing to overwrite an existing native research input')
    with disconnected():
        base=load_inputs(args.native,args.warmup,args.legacy)
        bundle,_=_json(args.capture)
        vix=_json(args.vix_capture)[0] if args.vix_capture else None
        result=convert(bundle,base,vix)
        out.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive create also protects against concurrent import collisions.
        with out.open('x') as handle:
            json.dump(result,handle,indent=2,allow_nan=False)
            handle.write('\n')
        print(json.dumps({'schema':result['schema'], 'coverage':result['import_audit']['coverage'],
                          'historical_live_arrivals_verified':False,'bars_created':0}))


if __name__ == '__main__':
    main()
