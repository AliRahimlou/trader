"""Explicit read-only collection command; replay never loads credentials."""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import gzip
import json
from urllib.parse import urlsplit
import requests
from dotenv import dotenv_values
from pivot.feeds import ReadOnlyFeeds, ET
from pivot.models import MAG7
from .data import cached_vix, encode_bar, audit


class ReadOnlySession(requests.Session):
    def request(self, method, url, **kwargs):
        target = urlsplit(url)
        permitted = ((target.netloc in ('api.alpaca.markets', 'paper-api.alpaca.markets') and target.path == '/v2/calendar')
                     or (target.netloc == 'data.alpaca.markets' and target.path == '/v2/stocks/bars'))
        if method.upper() != 'GET' or target.scheme != 'https' or not permitted:
            raise RuntimeError('Research collection allows only calendar and historical-stock GET requests')
        kwargs['allow_redirects'] = False
        return super().request(method, url, **kwargs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', required=True)
    p.add_argument('--vix-cache', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit('Refusing to overwrite immutable dataset; select a new output path')
    vix, provenance = cached_vix(args.vix_cache)
    end = max(b.end for b in vix)
    start = (min(b.end for b in vix).astimezone(ET)-timedelta(days=60)).replace(hour=0,minute=0,second=0,microsecond=0)
    if end-start > timedelta(days=90):
        raise SystemExit('Collection exceeds predeclared 90-day equity-history budget')
    cfg = dotenv_values(args.env_file)
    safe = {k: cfg[k] for k in ('APCA_API_KEY_ID','APCA_API_SECRET_KEY','APCA_API_BASE_URL') if k in cfg}
    safe['APCA_API_FEED'] = 'iex'
    feed = ReadOnlyFeeds(safe, session=ReadOnlySession())
    sessions = feed.stock_calendar(start, end)
    stocks = feed.stock_bars(['QQQ', *MAG7], 15, start, end, sessions)
    vix = [b for b in vix if (session := sessions.get(b.end.astimezone(ET).date().isoformat()))
           and session['open'] <= b.end-timedelta(minutes=15) < b.end <= session['close']]
    coverage = audit(sessions, stocks, vix)
    data = {'schema': 'pivot-research-bars-v1', 'collected_at': datetime.now(timezone.utc).isoformat(),
            'stock_provenance': {'source':'Alpaca IEX','adjustment':'split','requested_start':start.isoformat(),
                                 'requested_end':end.isoformat(),'point_in_time_arrival_verified':False},
            'vix_provenance':provenance,
            'sessions':{d:{k:v.isoformat() for k,v in s.items()} for d,s in sessions.items()},
            'stocks':{s:[encode_bar(b) for b in bars] for s,bars in stocks.items()},
            'vix':[encode_bar(b) for b in vix]}
    out.parent.mkdir(parents=True, exist_ok=True)
    content = gzip.compress(json.dumps(data, sort_keys=True, separators=(',',':')).encode(), mtime=0)
    with out.open('xb') as f: f.write(content)
    out.chmod(0o600)
    manifest = {'dataset_sha256':sha256(content).hexdigest(), 'collected_at':data['collected_at'],
                'stock_provenance':data['stock_provenance'],'vix_provenance':provenance,
                'equity_counts':{s:len(b) for s,b in stocks.items()},'vix_count':len(vix),'coverage':coverage}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
