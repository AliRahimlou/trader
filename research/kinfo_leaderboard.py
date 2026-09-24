"""Read-only collection and analysis of Kinfo's public verified-trader leaderboard.

Only unauthenticated, public data is fetched: the leaderboard API the public
page calls, and each portfolio's public performance page (profile, summary
statistics and the daily profit series it renders). Kinfo shows full trade
lists and tickers only to subscribers; this module does not attempt to obtain
them. Requests are sequential and throttled.

    python -m research.kinfo_leaderboard collect --out <dir> [--top 150]
    python -m research.kinfo_leaderboard analyze --data <dir> --out <file.json>
"""
import argparse
import gzip
import json
import math
import re
import statistics
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

API = 'https://api.kinfo.com/api/Portfolio/tradePortfoliosByPerformanceFiltered'
PAGE = 'https://kinfo.com/portfolio/{}/performance'
HEADERS = {'User-Agent': 'Mozilla/5.0 (research; read-only)', 'Host': 'kinfo.com'}
# Path flags after the page index: incEquities/incOptions/incFutures/excEquities/excOptions/excFutures.
BOARDS = {
    'all': 'false/false/false/false/false/false',
    'futures': 'false/false/true/false/false/false',
    'equities_only': 'true/false/false/false/true/true',
}
DELAY = 1.0


def _get(url, as_json=True):
    headers = HEADERS if 'api.kinfo.com' in url else {'User-Agent': HEADERS['User-Agent']}
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=headers), timeout=45) as r:
                body = r.read().decode('utf8')
            time.sleep(DELAY)
            return json.loads(body) if as_json else body
        except OSError:
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f'failed: {url}')


def collect_board(flags, limit):
    rows, page = [], 0
    while len(rows) < limit:
        batch = _get(f'{API}/profit/at/{page}/{flags}').get('result') or []
        if not batch:
            break
        rows += batch
        page += 1
    return rows[:limit]


def _flight(html):
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, flags=re.S)
    return ''.join(json.loads('"' + c + '"') for c in chunks)


def _array(text, key):
    i = text.find(f'"{key}":[')
    if i < 0:
        return []
    return json.JSONDecoder().raw_decode(text, i + len(key) + 3)[0]


def collect_portfolio(pid):
    text = _flight(_get(PAGE.format(pid), as_json=False))
    return {
        'id': pid,
        'daily': _array(text, 'tradeProfitDaily'),
        'recent_trades': [{k: v for k, v in t.items() if k not in ('tradesjson', 'profile', 'comments', 'logo')} | {'fills': len(t.get('tradesjson') or [])}
                          for t in _array(text, 'tradePerformanceHistory')],
    }


def collect(out, top, board_limit):
    out.mkdir(parents=True, exist_ok=True)
    boards = {name: collect_board(flags, board_limit) for name, flags in BOARDS.items()}
    (out / 'boards.json').write_text(json.dumps(boards, indent=1))
    ids = []
    for name in ('all', 'futures', 'equities_only'):
        for row in boards[name][:top]:
            if row['id'] not in ids:
                ids.append(row['id'])
    pdir = out / 'portfolios'
    pdir.mkdir(exist_ok=True)
    for pid in ids:
        target = pdir / f'{pid}.json.gz'
        if not target.exists():
            target.write_bytes(gzip.compress(json.dumps(collect_portfolio(pid)).encode()))
    print(f'{sum(map(len, boards.values()))} board rows, {len(ids)} portfolios -> {out}')


def daily_metrics(daily):
    pnl = [d['gainAmount'] for d in daily if d.get('gainAmount')]
    if len(pnl) < 20:
        return None
    days = [date.fromisoformat(d['date']) for d in daily]
    equity = peak = maxdd = 0.0
    for x in pnl:
        equity += x
        peak = max(peak, equity)
        maxdd = max(maxdd, peak - equity)
    mean, sd = statistics.fmean(pnl), statistics.pstdev(pnl)
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x < 0]
    years = {}
    for d, x in zip(days, pnl):
        years[d.year] = years.get(d.year, 0) + x
    ordered = sorted(pnl, reverse=True)
    top_share = sum(ordered[:max(1, len(pnl) // 20)]) / sum(pnl) if sum(pnl) > 0 else None
    return {
        'first': days[0].isoformat(), 'last': days[-1].isoformat(), 'active_days': len(pnl),
        'total': sum(pnl), 'green_day_pct': len(wins) / len(pnl),
        'avg_green': statistics.fmean(wins) if wins else 0, 'avg_red': statistics.fmean(losses) if losses else 0,
        'daily_sharpe_ann': mean / sd * math.sqrt(252) if sd else None,
        'max_drawdown': maxdd, 'recovery_factor': sum(pnl) / maxdd if maxdd else None,
        'worst_day': min(pnl), 'best_day': max(pnl),
        'top5pct_days_share_of_profit': top_share,
        'profitable_years': sum(v > 0 for v in years.values()), 'years': len(years),
        'yearly': {str(k): round(v, 2) for k, v in sorted(years.items())},
    }


def analyze(data, out):
    boards = json.loads((data / 'boards.json').read_text())
    board_of = {}
    for name, rows in boards.items():
        for rank, row in enumerate(rows, 1):
            board_of.setdefault(row['id'], {'row': row, 'ranks': {}})['ranks'][name] = rank
    traders = []
    for path in sorted((data / 'portfolios').glob('*.json.gz')):
        p = json.loads(gzip.decompress(path.read_bytes()))
        info = board_of.get(p['id'])
        if not info:
            continue
        row, tp = info['row'], info['row']['tradePerformance']
        recent = p['recent_trades']
        holds = [(_minutes(t['entryDateTime'], t['exitDateTime'])) for t in recent if t.get('hasTimestamp')]
        traders.append({
            'id': p['id'], 'name': row['name'], 'ranks': info['ranks'],
            'url': PAGE.format(p['id']), 'twitter': row.get('twitter'), 'site': row.get('site'),
            'youtube': row.get('youtube'), 'description': row.get('description'),
            'kinfluencer': row.get('kinfluencer'), 'tickers_hidden': bool(row.get('lockTickers') or row.get('hideTickers')),
            'trades': tp['trades'], 'win_rate': tp['winningTradesPercent'], 'profit': tp['profit'],
            'profit_factor': tp['profitFactor'], 'avg_trade': tp['avgGain'],
            'avg_win': tp['gainWon'] / tp['tradesWon'] if tp['tradesWon'] else None,
            'avg_loss': tp['gainLost'] / tp['tradesLost'] if tp['tradesLost'] else None,
            'profit_1y': tp.get('profit1y'), 'profit_90': tp.get('profit90'),
            'recent_asset_mix': dict(Counter(t.get('assetCategory') for t in recent)),
            'recent_side_mix': dict(Counter(t.get('type') for t in recent)),
            'recent_median_hold_min': statistics.median(holds) if holds else None,
            'daily': daily_metrics(p['daily']),
        })
    out.write_text(json.dumps(traders, indent=1))
    print(f'{len(traders)} traders -> {out}')


def _minutes(a, b):
    from datetime import datetime
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('collect')
    c.add_argument('--out', type=Path, required=True)
    c.add_argument('--top', type=int, default=150)
    c.add_argument('--board-limit', type=int, default=400)
    a = sub.add_parser('analyze')
    a.add_argument('--data', type=Path, required=True)
    a.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.cmd == 'collect':
        collect(args.out, args.top, args.board_limit)
    else:
        analyze(args.data, args.out)


if __name__ == '__main__':
    main()
