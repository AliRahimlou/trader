from copy import deepcopy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import json

import pytest

from research.one_minute_execution import START,END,PARAMS,capture,load_execution,normalize


def fixture():
    sessions={START.date().isoformat():{'open':START,'close':START+timedelta(minutes=15)}}
    raw={'bars':{'QQQ':[{'t':(START+timedelta(minutes=i)).isoformat(),
        'o':100.,'h':101.,'l':99.,'c':100.5,'v':1000,'vw':100.2} for i in range(15)]},'next_page_token':None}
    return sessions,raw


class Reader:
    def __init__(self,raw): self.calls=[]; self.raw=raw
    def get(self,provider,path,params):
        self.calls.append((provider,path,params))
        return self.raw


def test_one_fixed_stock_request_and_roundtrip_without_accounts_or_retries(tmp_path):
    sessions,raw=fixture(); reader=Reader(raw)
    now=datetime(2026,9,18,18,tzinfo=timezone.utc)
    path=tmp_path/'execution.json'
    result=capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},
                   clock=lambda:now,reader=reader)
    assert reader.calls==[('stocks','/v2/stocks/bars',PARAMS)]
    assert result['request_count']==1 and result['retries']==0
    bars,days,identity=load_execution(path,SimpleNamespace(sessions=sessions))
    assert len(bars)==15 and days==['2026-09-09']
    assert bars[0].end==START+timedelta(minutes=1) and bars[0].minutes==1
    assert identity['provenance']['purpose']=='execution_only'
    with pytest.raises(ValueError,match='already exists'):
        capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},reader=reader)
    assert len(reader.calls)==1


def test_pagination_outcome_consumes_claim_without_a_second_page_or_retry(tmp_path):
    sessions,raw=fixture(); raw['next_page_token']='more'
    reader=Reader(raw); path=tmp_path/'execution.json'
    with pytest.raises(ValueError,match='one-page'):
        capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},reader=reader)
    assert len(reader.calls)==1 and not path.exists()
    retained=json.loads(path.with_suffix(path.suffix+'.response.json').read_text())
    assert retained['raw_response']==raw and retained['request_count']==1
    with pytest.raises(FileExistsError):
        capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},reader=reader)
    assert len(reader.calls)==1


@pytest.mark.parametrize('bad',['duplicate','off_grid','wrong_symbol','truncated','ohlc'])
def test_invalid_or_potentially_truncated_execution_history_rejected(bad):
    sessions,raw=fixture()
    if bad=='duplicate': raw['bars']['QQQ'].append(raw['bars']['QQQ'][-1])
    if bad=='off_grid': raw['bars']['QQQ'][0]['t']=(START+timedelta(seconds=1)).isoformat()
    if bad=='wrong_symbol': raw['bars']['AAPL']=raw['bars'].pop('QQQ')
    if bad=='truncated': raw['bars']['QQQ']=[raw['bars']['QQQ'][0]]*10000
    if bad=='ohlc': raw['bars']['QQQ'][0]['h']=98
    with pytest.raises(ValueError): normalize(raw,sessions)


def test_missing_one_minute_bar_remains_missing_and_blocks_full_session(tmp_path):
    sessions,raw=fixture(); raw['bars']['QQQ'].pop(5)
    path=tmp_path/'execution.json'
    capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},reader=Reader(raw))
    bars,complete,_=load_execution(path,SimpleNamespace(sessions=sessions))
    assert len(bars)==14 and complete==[]
    assert START+timedelta(minutes=6) not in {b.end for b in bars}


def test_execution_artifact_tampering_cannot_change_recorded_ohlc(tmp_path):
    sessions,raw=fixture(); path=tmp_path/'execution.json'
    artifact=capture(path,sessions,{'APCA_API_KEY_ID':'fixture','APCA_API_SECRET_KEY':'fixture'},reader=Reader(raw))
    artifact['bars'][0]['close']=100.6
    path.write_text(json.dumps(artifact))
    with pytest.raises(ValueError,match='content differs'):
        load_execution(path,SimpleNamespace(sessions=sessions))


def test_inclusive_provider_end_bar_is_retained_raw_but_excluded_from_execution():
    sessions,raw=fixture()
    raw['bars']['QQQ'].append({'t':END.isoformat(),'o':100.,'h':101.,'l':99.,'c':100.,'v':10})
    bars,excluded=normalize(raw,sessions)
    assert len(bars)==15 and excluded==1
    assert all(bar.end<=END for bar in bars)
    raw['bars']['QQQ'].append({'t':(END+timedelta(minutes=1)).isoformat(),'o':100.,'h':101.,'l':99.,'c':100.,'v':10})
    bars,excluded=normalize(raw,sessions)
    assert len(bars)==15 and excluded==2
