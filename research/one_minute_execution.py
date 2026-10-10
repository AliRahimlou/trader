"""One bounded IEX request for prespecified execution-only historical QQQ bars.

No account, orders, VIX or live-strategy interfaces. Fixed September 9–16, 2026
range; one page only; no retries or fabricated missing candles.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path

from pivot.feeds import ReadOnlyFeeds, FeedError, ET
from pivot.models import Bar, timestamp
from research.data import encode_bar
from research.validate_v2 import _json, _calendar, ends, load_inputs

SCHEMA='pivot-qqq-native-one-minute-execution-v1'
START=datetime(2026,9,9,9,30,tzinfo=ET)
END=datetime(2026,9,16,16,0,tzinfo=ET)
PARAMS={'symbols':'QQQ','timeframe':'1Min','start':START.isoformat(),'end':END.isoformat(),
        'adjustment':'split','feed':'iex','sort':'asc','limit':10000}
PROVENANCE={'source':'alpaca_iex','source_symbol':'QQQ','adjustment':'split',
            'native_resolution_minutes':1,'purpose':'execution_only',
            'historical_live_arrivals_verified':False,'actual_broker_fills_verified':False}


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def normalize(raw,sessions):
    if (not isinstance(raw,dict) or raw.get('next_page_token') not in (None,'')
            or not isinstance(raw.get('bars'),dict) or set(raw['bars'])!={'QQQ'}):
        raise ValueError('A complete one-page QQQ response is required; no pagination will be attempted')
    rows=raw['bars']['QQQ']
    if not isinstance(rows,list) or not 1<=len(rows)<10000:
        raise ValueError('Empty or potentially truncated native one-minute response')
    output=[]; previous=None; excluded=0
    permitted={t for d,s in sessions.items() if START.date().isoformat()<=d<=END.date().isoformat()
               for t in ends(s,1)}
    for row in rows:
        start=timestamp(row['t'])
        if start.second or start.microsecond:
            raise ValueError('Native execution timestamp is off grid: '+start.isoformat())
        if previous is not None and start<=previous:
            raise ValueError('Native execution timestamps are duplicated or unordered: '+start.isoformat())
        # Alpaca's end parameter can include a bar beginning exactly at END.
        # Keep the original requested interval; exclude that later bar below.
        bar=Bar(start+timedelta(minutes=1),1,row['o'],row['h'],row['l'],row['c'],row['v'],row.get('vw'))
        previous=start
        if bar.end in permitted and START<=start and bar.end<=END:
            output.append(bar)
        else:
            excluded+=1
    if not output:
        raise ValueError('No native execution observations in the verified calendar')
    return output,excluded


def private_write_once(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as handle:
        json.dump(value,handle,sort_keys=True,separators=(',',':'),allow_nan=False)
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())


def capture(path,sessions,env,*,clock=None,reader=None):
    path=Path(path)
    if path.exists():
        raise ValueError('Execution artifact already exists; no request repeated')
    if not env.get('APCA_API_KEY_ID') or not env.get('APCA_API_SECRET_KEY'):
        raise ValueError('Local stock-data credentials are unavailable')
    clock=clock or (lambda:datetime.now(timezone.utc))
    requested=clock()
    claim=path.with_suffix(path.suffix+'.claim')
    private_write_once(claim,{'schema':SCHEMA,'requested_at':requested.isoformat(),
        'request_count':1,'request':PARAMS,'state':'consumed_before_request','retries':0})
    owned=reader is None
    client=reader or ReadOnlyFeeds({'APCA_API_KEY_ID':env['APCA_API_KEY_ID'],
        'APCA_API_SECRET_KEY':env['APCA_API_SECRET_KEY'],'APCA_API_FEED':'iex'})
    try:
        # Fixed stocks destination and endpoint, exactly one call. Parsing never
        # follows pagination, retries, or calls broker/account endpoints.
        raw=client.get('stocks','/v2/stocks/bars',dict(PARAMS))
    finally:
        if owned: client.session.close()
    received=clock()
    if received<requested: raise ValueError('Capture clock moved backwards')
    # Preserve the successful HTTP response before normalization, so a parser
    # failure can be diagnosed offline without repeating the provider request.
    raw_text=json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False)
    if any(value and value in raw_text for value in (env['APCA_API_KEY_ID'],env['APCA_API_SECRET_KEY'])):
        raise ValueError('Provider response reflected a credential; artifact not written')
    private_write_once(path.with_suffix(path.suffix+'.response.json'),{'schema':SCHEMA+'-response',
        'requested_at':requested.isoformat(),'received_at':received.isoformat(),'request':PARAMS,
        'request_count':1,'raw_response':raw,'raw_response_sha256':digest(raw),'retries':0})
    bars,excluded=normalize(raw,sessions)
    rows=[encode_bar(b) for b in bars]
    scoped={d:s for d,s in sessions.items() if START.date().isoformat()<=d<=END.date().isoformat()}
    coverage=[{'day':d,'expected':len(list(ends(s,1))),
        'observed':sum(b.end.astimezone(ET).date().isoformat()==d for b in bars)} for d,s in sorted(scoped.items())]
    artifact={'schema':SCHEMA,'requested_at':requested.isoformat(),'received_at':received.isoformat(),
        'request_count':1,'request':PARAMS,'provenance':PROVENANCE,'raw_response':raw,
        'raw_response_sha256':digest(raw),'bars':rows,'normalized_sha256':digest(rows),
        'sessions':{d:{k:v.isoformat() for k,v in s.items()} for d,s in scoped.items()},
        'coverage':coverage,'excluded_outside_regular_session':excluded,'retries':0}
    private_write_once(path,artifact)
    return artifact


def load_execution(path,base):
    artifact,checksum=_json(path)
    if (artifact.get('schema')!=SCHEMA or artifact.get('request')!=PARAMS
            or artifact.get('provenance')!=PROVENANCE or artifact.get('request_count')!=1
            or artifact.get('retries')!=0):
        raise ValueError('Native one-minute execution identity differs')
    if timestamp(artifact['requested_at'])>timestamp(artifact['received_at']):
        raise ValueError('Invalid execution-capture receipts')
    sessions=_calendar(artifact['sessions'])
    for d,s in sessions.items():
        if d not in base.sessions or base.sessions[d]!=s:
            raise ValueError('Execution calendar differs from frozen base calendar')
    bars,excluded=normalize(artifact['raw_response'],sessions)
    rows=[encode_bar(b) for b in bars]
    if (digest(artifact['raw_response'])!=artifact['raw_response_sha256']
            or rows!=artifact['bars'] or digest(rows)!=artifact['normalized_sha256']
            or excluded!=artifact['excluded_outside_regular_session']):
        raise ValueError('Execution artifact content differs from original native response')
    complete=[d for d,s in sessions.items() if set(ends(s,1)) <= {b.end for b in bars}]
    return bars,complete,{'name':Path(path).name,'sha256':checksum,'provenance':PROVENANCE,
        'received_at':artifact['received_at'],'request_count':1}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('native','warmup','legacy','out'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--env-file',default='.env')
    args=parser.parse_args()
    from dotenv import dotenv_values
    base=load_inputs(args.native,args.warmup,args.legacy)
    result=capture(args.out,base.sessions,dotenv_values(args.env_file))
    print(json.dumps({'native_one_minute_bars':len(result['bars']),'coverage':result['coverage'],
        'request_count':1,'retries':0,'historical_live_arrivals_verified':False}))


if __name__=='__main__': main()
