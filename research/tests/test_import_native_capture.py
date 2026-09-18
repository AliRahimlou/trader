"""Offline native capture import integrity and no invented history witnesses."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from pivot.feeds import ET
from pivot.native_history import (_coverage, _digest, _encode, _provenance,
    _request_description, _series, _sessions, EXPERIMENT, SCHEMA)
from research.data import encode_bar
from research.import_native_capture import canonical_sha256, convert
from research.native_method_experiments import load_native, native_at
from research.offline import disconnected


def at(day, hour=9, minute=30, second=0):
    return datetime(2026,9,day,hour,minute,second,tzinfo=ET)


def fixture():
    sessions={at(d).date().isoformat():{'open':at(d),'close':at(d,9,45)} for d in (16,17,18)}
    encoded={d:{k:v.isoformat() for k,v in session.items()} for d,session in sessions.items()}
    raw={'code':'CBOE:VIX','bar_type':'5m','last_update':int(at(18,10,0).timestamp()*1000),
         'bar_end':int(at(18,9,45).timestamp()),
         'series':[{'time':int(at(d,9,m).timestamp()),'open':20.,'high':21.,'low':19.,'close':20.5}
                   for d in (16,17,18) for m in (30,35,40)]}
    received=at(18,10,0,2)
    bars,updated=_series(raw,received,sessions)
    rows=[_encode(b) for b in bars]
    metadata={'code':'CBOE:VIX','type':'INDEX','delay_seconds':0}
    artifact={'schema':SCHEMA,'experiment':EXPERIMENT,'research_only':True,'live_entry_ready':False,
        'requested_at':at(18,9,59).isoformat(),'received_at':received.isoformat(),
        'source_updated_at':updated.isoformat(),'request':_request_description(),
        'metadata':metadata,'metadata_received_at':at(18,9,0).isoformat(),'metadata_sha256':_digest(metadata),
        'raw_response':raw,'raw_response_sha256':_digest(raw),'normalized_sha256':_digest(rows),
        'sessions':encoded,'bars':rows,'provenance':_provenance(),
        'coverage':_coverage(bars,sessions,received)}
    qqq=[{**encode_bar(b),'open':100.,'high':101.,'low':99.,'close':100.5,'volume':1000} for b in bars]
    bundle={'schema':'pivot-native-capture-v1','captured_at':at(18,10,0,3).isoformat(),
        'research_only':True,'live_entry_ready':False,
        'sessions':encoded,'qqq':{'schema':'pivot-native-qqq-five-minute-capture-v1',
            'research_only':True,'status':'captured','requested_at':at(18,9,59).isoformat(),
            'received_at':at(18,10,0,1).isoformat(),
            'provenance':{'source':'alpaca_iex','source_symbol':'QQQ','adjustment':'split','native_resolution_minutes':5},
            'bars':qqq,'normalized_sha256':canonical_sha256(qqq)},'vix':artifact}
    base=SimpleNamespace(sessions={d:s for d,s in sessions.items() if d!='2026-09-18'}, inputs={'fixture':'frozen'})
    return bundle,base


def test_verified_native_import_preserves_prices_times_and_excludes_new_sessions(tmp_path):
    bundle,base=fixture()
    with disconnected():
        result=convert(bundle,base)
    assert result['schema']=='pivot-native-qqq-vix-five-minute-v1'
    assert list(result['sessions'])==['2026-09-16','2026-09-17']
    assert len(result['qqq5'])==len(result['vix5'])==6
    assert result['qqq5']==bundle['qqq']['bars'][:6]
    assert [{k:v for k,v in row.items() if k!='vwap'} for row in result['vix5']]==bundle['vix']['bars'][:6]
    assert result['import_audit']['coverage']['VIX']['excluded_outside_shared_frozen_calendar']==3
    assert result['import_audit']['bars_created']==0
    assert result['vix_provenance']['point_in_time_arrival_verified'] is False
    path=tmp_path/'native.json'; path.write_text(json.dumps(result))
    # Export meets the existing experiment loader's exact input contract.
    loaded=load_native(path,base)
    qqq,vix=native_at(loaded,base,at(17,9,36))
    assert qqq.bars[-1].end==at(17,9,35) and vix.bars[-1].end==at(17,9,35)


@pytest.mark.parametrize('mutation',['qqq_hash','vix_hash','vix_normalized','metadata','frame','receipt','calendar','proxy'])
def test_tampered_wrong_source_or_inconsistent_capture_fails(mutation):
    bundle,base=fixture()
    if mutation=='qqq_hash': bundle['qqq']['bars'][0]['close']=100.6
    elif mutation=='vix_hash': bundle['vix']['raw_response']['series'][0]['close']=20.6
    elif mutation=='vix_normalized':
        bundle['vix']['bars'][0]['close']=20.6
        bundle['vix']['normalized_sha256']=canonical_sha256(bundle['vix']['bars'])
    elif mutation=='metadata':
        bundle['vix']['metadata']['delay_seconds']=900
        bundle['vix']['metadata_sha256']=canonical_sha256(bundle['vix']['metadata'])
    elif mutation=='frame':
        bundle['qqq']['bars'][0]['minutes']=15
        bundle['qqq']['normalized_sha256']=canonical_sha256(bundle['qqq']['bars'])
    elif mutation=='receipt': bundle['qqq']['received_at']=at(18,10,1).isoformat()
    elif mutation=='calendar': bundle['sessions']['2026-09-17']['close']=at(17,10,0).isoformat()
    elif mutation=='proxy': bundle['qqq']['provenance']['source_symbol']='TQQQ'
    with pytest.raises((ValueError,TypeError,KeyError)): convert(bundle,base)


def test_missing_native_interval_is_reported_and_never_reconstructed(tmp_path):
    bundle,base=fixture()
    # A legitimate sparse response remains sparse, with all integrity fields
    # recomputed as they would be by the collector; this is not tampering.
    artifact=bundle['vix']
    artifact['raw_response']['series'].pop(1)
    artifact['raw_response_sha256']=canonical_sha256(artifact['raw_response'])
    source_sessions=_sessions(artifact['sessions'])
    bars,_=_series(artifact['raw_response'],datetime.fromisoformat(artifact['received_at']),source_sessions)
    artifact['bars']=[_encode(b) for b in bars]
    artifact['normalized_sha256']=canonical_sha256(artifact['bars'])
    artifact['coverage']=_coverage(bars,source_sessions,datetime.fromisoformat(artifact['received_at']))
    result=convert(bundle,base)
    assert len(result['vix5'])==5 and result['import_audit']['bars_created']==0
    assert result['import_audit']['coverage']['VIX']['session_coverage'][0]['missing_bars']==1
    path=tmp_path/'native.json'; path.write_text(json.dumps(result))
    loaded=load_native(path,base)
    with pytest.raises(ValueError,match='I:VIX:native_history_missing_1'):
        native_at(loaded,base,at(17,9,36))


def test_separate_validated_vix_override_supported_without_mutating_bundle():
    bundle,base=fixture()
    artifact=bundle.pop('vix')
    before=deepcopy(bundle)
    result=convert(bundle,base,artifact)
    assert len(result['vix5'])==6 and bundle==before
