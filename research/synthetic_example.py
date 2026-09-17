"""Positive logic fixture only: manufactured prices are not market evidence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pivot.models import Bar, Market, MAG7
from pivot.strategy import analyze, leader_diagnostics


def accepted_long():
    now=datetime(2026,9,16,16,tzinfo=timezone.utc)
    def bar(at,o,h,l,c,minutes):return Bar(at,minutes,o,h,l,c,100)
    ranges=[(105,95),(110,94),(105,95),(106,90),(105,95),(110,94),(105,95),(106,90),(105,95)]
    history=[bar(now-timedelta(hours=4*(14-i)),100,h,l,100,240) for i,(h,l) in enumerate(ranges)]
    market=Market('QQQ',{240:history,60:[bar(now-timedelta(hours=2),92,93,91,92,60),
                    bar(now-timedelta(hours=1),92,93,88,89,60),bar(now,89.5,90,88,89,60)]},'alpaca_iex',True,now)
    previous=bar(now-timedelta(minutes=15),100,106,94,100,15)
    long=bar(now,100,105,89.9,104,15)
    leaders={s:Market(s,{240:history,15:[previous,long]},'alpaca_iex',True,now) for s in MAG7}
    short=bar(now,100,110.1,95,96,15)
    vix=Market('I:VIX',{15:[replace(b,minutes=15) for b in history]+[previous,short]},'massive_indices',True,now)
    setup=analyze(market,leaders,vix,now)
    assert setup['state']=='SETUP_READY' and setup['direction']=='long'
    return {'evidence_type':'synthetic_unit_fixture','prices':'manufactured, not fetched',
            'meaning':'Analyzer accepts this deliberately constructed geometry. No order, performance or video-equivalence evidence.',
            'live_order_authorized':False,'setup':setup,'leaders':leader_diagnostics(leaders,now)}
