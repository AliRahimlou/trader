"""Inspectable decisions sharing the production analyzer, never an order client."""
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import json
from pivot.strategy import analyze, leader_diagnostics, vix_confirmation, AnalysisPolicy, ET
from pivot.history_health import frame_gaps
from pivot.models import timestamp
from pivot.policy import POLICY_VERSION


def decision(markets, vix, sessions, at, variant):
    policy = AnalysisPolicy(**{k:variant[k] for k in ('zone_tolerance','persistence_bars','minimum_leaders','maximum_opposition')})
    setup = analyze(markets['QQQ'], markets, vix, at, policy)
    event_at = timestamp(setup['event_at']) if setup['event_at'] else None
    leaders = leader_diagnostics(markets, at, policy, event_at)
    day = at.astimezone(ET).date()
    scoped = {d:s for d,s in sessions.items() if (day-timedelta(days=14)).isoformat() <= d <= day.isoformat()}
    gaps = frame_gaps(vix.bars[15], scoped, 15, at)
    # All equity histories must have the same coverage required by production.
    stock_start = (at.astimezone(ET)-timedelta(days=60)).date().isoformat()
    stock_sessions = {d:s for d,s in sessions.items() if stock_start <= d <= day.isoformat()}
    stock_gaps = {symbol: frame_gaps(m.bars[15],stock_sessions,15,at)['missing_count'] for symbol,m in markets.items()}
    direction = setup.get('direction')
    individual_passed = [c['name'] for c in setup['checks'] if c['passed']]
    passed = []
    for check in setup['checks']:
        if not check['passed']: break
        passed.append(check['name'])
    first_failed = next((c['name'] for c in setup['checks'] if not c['passed']), None)
    qualified = setup['state'] == 'SETUP_READY'
    # Match Executor._enter exactly; changing an exit level is not a new event.
    key = f'{POLICY_VERSION}|QQQ|{setup.get("event_at")}|{direction}'
    setup_id = sha256(key.encode()).hexdigest()[:24] if event_at and direction else None
    blockers = []
    if any(stock_gaps.values()): blockers.append('incomplete_stock_history')
    if gaps['missing_count']: blockers.append('incomplete_vix_history')
    if first_failed: blockers.append(first_failed)
    if qualified and direction != 'long': blockers.append('account_shorting_disabled_and_target_below_one_share')
    if qualified: blockers.append('historical_entry_quote_and_broker_acceptance_unverified')
    return {'at':at.isoformat(),'day':day.isoformat(),'variant':variant['id'],'policy':asdict(policy),
            'setup_id':setup_id,'setup':setup,'leaders':leaders,
            'vix':{'latest_at':vix.bars[15][-1].end.isoformat() if vix.bars[15] else None,
                   'bar_count':len(vix.bars[15]),'missing_count':gaps['missing_count'],
                   'long_confirmation':vix_confirmation(vix,'long',at)[0],
                   'short_confirmation':vix_confirmation(vix,'short',at)[0],
                   'historical_quote_available':None},
            'stock_missing_bars':stock_gaps,'passed_gates':passed,'individual_passed_gates':individual_passed,'first_failed_gate':first_failed,
            'blockers':blockers,'signal_qualified':qualified,
            'simulation_admissible':qualified and not any(stock_gaps.values()) and not gaps['missing_count'] and direction=='long',
            'live_order_authorized':False,'evidence_type':'historical_reconstruction'}
