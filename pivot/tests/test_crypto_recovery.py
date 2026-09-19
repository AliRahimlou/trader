"""Proof-based recovery changes local incident records only, never broker orders."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from test_crypto_execution import engine, tick, MAIN_POLICY
from test_crypto_audit import seeded
from pivot.feeds import FeedError


def test_definitive_rejected_entry_recovers_when_flat_without_mutation_or_permission_change(engine):
    e,b,store,main,_ = engine
    b.entry_mode = 'reject'; tick(e)
    assert store.incidents() and not store.active_trades()
    crypto_control, master_control = store.control(), main.control()
    sent, canceled = deepcopy(b.sent), deepcopy(b.canceled)
    result = e.recover_incidents()
    assert result['recovered'] and not result['remaining']
    assert b.sent == sent and b.canceled == canceled
    assert store.control() == crypto_control and main.control() == master_control
    assert e.snapshot()['message'] == result['message']
    assert any(event['kind'] == 'incident_resolved' for event in store.events())


def test_failed_protection_safely_closed_then_proof_recovers(engine):
    e,b,store,_,_ = engine
    b.reject_stop = True; tick(e)
    assert not store.active_trades() and store.incidents() and not any(b.holdings.values())
    before = len(b.sent)
    result = e.recover_incidents()
    assert result['recovered'] and not store.incidents() and len(b.sent) == before


def test_active_uncertain_lifecycle_cannot_be_abandoned_by_recovery(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'lost_unseen'; tick(e)
    b.at += timedelta(seconds=31); tick(e)
    before = deepcopy(b.sent)
    result = e.recover_incidents()
    assert not result['recovered'] and result['remaining'] and b.sent == before
    assert store.active_trades()


def test_even_finished_unknown_post_needs_exact_terminal_lookup(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'lost_unseen'; tick(e)
    trade = store.active_trades()[0]
    trade['stage'] = 'finished'; store.save_trade(trade, finished=True)
    store.incident(trade['id']+':entry_unknown', 'Unknown order', trade_id=trade['id'])
    result = e.recover_incidents()
    assert not result['recovered'] and result['remaining']
    assert len(b.sent) == 1 and not b.canceled


def test_foreign_exposure_only_recovers_after_flat_proof(engine):
    e,b,store,_,_ = engine
    b.holdings['BTC/USD'] = Decimal('.001'); tick(e)
    assert store.incidents()
    assert not e.recover_incidents()['recovered']
    b.holdings.clear()
    assert e.recover_incidents()['recovered'] and not b.sent


def test_wrong_connected_account_cannot_clear_rejection(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'reject'; tick(e)
    b.account_data['account_ref'] = 'another-account'
    assert not e.recover_incidents()['recovered'] and store.incidents()


def test_master_off_can_review_same_saved_crypto_account_without_turning_on(engine):
    e,b,store,main,_ = engine
    b.entry_mode = 'reject'; tick(e); main.set_control(False)
    assert e.recover_incidents()['recovered']
    assert not main.control()['enabled'] and not e.enabled()


def test_recovery_network_failure_preserves_all_incidents(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'reject'; tick(e)
    before = store.incidents()
    def offline(): raise FeedError('unavailable')
    b.positions = offline
    assert not e.recover_incidents()['recovered'] and store.incidents() == before


def test_malformed_final_proof_read_does_not_clear_incidents(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'reject'; tick(e)
    calls = 0
    def changed():
        nonlocal calls
        calls += 1
        return [] if calls == 1 else {}
    b.positions = changed
    assert not e.recover_incidents()['recovered'] and store.incidents()


def test_terminal_order_fill_mismatch_cannot_be_cleared_merely_because_flat(engine):
    e,b,store,_,_ = engine
    b.reject_stop = True; tick(e)
    entry = b.book[b.sent[0]['client_order_id']]
    entry['filled_qty'] = str(Decimal(entry['filled_qty'])/2)
    assert not e.recover_incidents()['recovered'] and store.incidents()


def test_unknown_cancellation_stays_incident_until_definitive_terminal_status(engine):
    e,b,store,_,_ = engine
    tick(e); b.bid = b.ask = Decimal('110'); b.cancel_mode = 'pending'
    tick(e); b.at += timedelta(seconds=31); tick(e)
    assert store.incidents()
    tick(e)
    assert store.incidents(), 'A known but uncanceled order is not resolved'
    b.cancel_mode = 'cancel'; b.at += timedelta(seconds=5); tick(e)
    assert not store.active_trades()
    assert not [i for i in store.incidents() if i['id'].endswith('_unknown')]


def test_resolved_incident_can_reopen_with_durable_history(engine):
    _,_,store,_,_ = engine
    store.incident('again', 'first'); store.resolve_incident('again'); store.incident('again', 'second')
    assert store.incidents()[0]['message'] == 'second'
    assert any(e['kind'] == 'incident_reopened' for e in store.events())


def test_recovered_working_stop_starts_cancellation_deadline_at_request_not_original_placement(engine):
    e,b,store,_,_ = engine
    tick(e); b.at += timedelta(hours=1)
    b.bid = b.ask = Decimal('110'); b.cancel_mode = 'pending'
    tick(e)
    assert not store.incidents(), e.message
    b.at += timedelta(seconds=31); tick(e)
    assert store.incidents()


def test_history_is_safe_and_distinguishes_no_fill_from_actual_closed_trade(engine):
    e,b,store,_,_ = engine
    b.entry_mode = 'no_fill'; tick(e)
    history = e.snapshot()['history']
    assert len(history) == 1 and history[0]['stage'] == 'finished'
    assert history[0].get('filled_qty') in (None, '0') and history[0]['completed_at']
    assert all('account_ref' not in row and 'ops' not in row for row in history)


def enlarged(tmp_path):
    e,b,store,*_ = seeded(tmp_path)
    trade = store.active_trades()[0]
    trade.update(amount='190000', source_entry='100000', stop='90000', target='120000',
                 filled_qty='1.9', net_entry_qty='1.9', net_entry_gross_qty='1.9')
    entry, stop = trade['ops']['entry'], trade['ops']['stop']
    for op in (entry, stop):
        op['payload']['qty'] = op['last_seen']['qty'] = '1.9'
        b.book[op['payload']['client_order_id']]['qty'] = '1.9'
    entry['payload']['limit_price'] = entry['last_seen']['limit_price'] = '100000'
    entry['last_seen']['filled_qty'] = '1.9'; entry['last_seen']['filled_avg_price'] = '100000'
    b.book[entry['payload']['client_order_id']].update(limit_price='100000', filled_qty='1.9', filled_avg_price='100000')
    stop['payload'].update(stop_price='90000', limit_price='89100')
    stop['last_seen'].update(stop_price='90000', limit_price='89100')
    b.book[stop['payload']['client_order_id']].update(stop_price='90000', limit_price='89100')
    store.save_trade(trade)
    b.qty, b.bid = Decimal('1.9'), Decimal('130000')
    return e,b,store


def test_appreciated_sell_is_split_into_bounded_children_and_reconciled_to_flat(tmp_path):
    e,b,store = enlarged(tmp_path)
    for _ in range(3):
        e.tick({}); b.at += timedelta(seconds=10)
    sales = [p for p in b.sent if p['type'] == 'market']
    assert len(sales) == 2
    assert all(Decimal(p['qty'])*(b.bid+Decimal('.01')) <= 190000 for p in sales)
    assert sum(Decimal(p['qty']) for p in sales) == Decimal('1.9')
    assert b.qty == 0 and not store.active_trades()


def test_large_exit_waiting_for_quote_keeps_existing_native_protection(tmp_path):
    e,b,store = enlarged(tmp_path)
    trade = store.active_trades()[0]
    e._start_exit(trade, 'Large target exit')
    b.quote_error = True
    e.tick({})
    assert not b.canceled and not b.sent and store.active_trades()


def test_small_emergency_exit_does_not_depend_on_market_data(tmp_path):
    e,b,store,*_ = seeded(tmp_path)
    trade = store.active_trades()[0]
    e._start_exit(trade, 'Emergency closure')
    b.quote_error = True
    e.tick({})
    assert b.qty == 0 and not store.active_trades()


def test_recovered_large_prepared_exit_is_repriced_and_recapped_before_post(tmp_path):
    e,b,store = enlarged(tmp_path)
    trade = store.active_trades()[0]
    stop = trade['ops']['stop']
    stop['last_seen']['status'] = 'canceled'; b.book[stop['payload']['client_order_id']]['status'] = 'canceled'
    trade.update(stage='exiting', exit_pending={'reason':'target','since':b.at.isoformat()}, exit_op='exit')
    trade['ops']['exit'] = {'state':'prepared','prepared_at':b.at.isoformat(),
        'payload':{'symbol':'BTC/USD','side':'sell','qty':'1.9','type':'market','time_in_force':'gtc',
                   'client_order_id':f'cr-{trade["id"]}-exit'}}
    store.save_trade(trade)
    b.bid = Decimal('150000')
    e.tick({})
    assert b.sent and Decimal(b.sent[0]['qty'])*(b.bid+Decimal('.01')) <= 190000


def test_small_residual_after_successful_capped_child_can_close_during_quote_outage(tmp_path):
    e,b,store = enlarged(tmp_path)
    e.tick({})
    assert 0 < b.qty < Decimal('1') and store.active_trades()
    b.quote_error = True
    e.tick({})
    assert b.qty == 0 and not store.active_trades()
