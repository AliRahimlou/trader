from copy import deepcopy
import pytest
from pivot.crypto_store import CryptoStore, ConcurrentChange
from pivot.store import Store


def trade(identity='abc', symbol='BTC/USD'):
    return {'id': identity, 'symbol': symbol, 'stage': 'entering', 'amount': '5.00',
            'ops': {'entry': {'state': 'prepared', 'payload': {'client_order_id': identity}}}}


def test_defaults_are_distinct_from_existing_socrates_permission(tmp_path):
    main = Store(tmp_path/'app.db'); main.set_control(True, 'legacy', 'owner')
    crypto = CryptoStore(main.path)
    assert not crypto.control()['enabled'] and main.control()['enabled']
    assert crypto.control()['symbols'] == ['BTC/USD'] and crypto.control()['target_dollars'] == '5.00'


@pytest.mark.parametrize('value', [True, '', '0', '-1', 'NaN', 'Infinity', '200001', '5.001'])
def test_invalid_amount_cannot_be_saved(tmp_path, value):
    s = CryptoStore(tmp_path/'app.db')
    with pytest.raises(ValueError): s.configure({'target_dollars': value})
    assert s.control()['target_dollars'] == '5.00'


@pytest.mark.parametrize('settings', [{'enabled': 'true'}, {'enabled': True}, {'symbols': []}, {'symbols': ['DOGE/USD']},
                                     {'symbols': ['BTC/USD', 'BTC/USD']}, {'unexpected': 1}])
def test_invalid_control_shape_rejected(tmp_path, settings):
    s = CryptoStore(tmp_path/'app.db')
    with pytest.raises(ValueError): s.configure(settings)
    assert s.control()['generation'] == 0


def test_control_generation_survives_reopen_and_cannot_be_overridden(tmp_path):
    s = CryptoStore(tmp_path/'app.db')
    s.configure({'target_dollars': '10', 'generation': 1000})
    s.configure({'target_dollars': '5'})
    assert CryptoStore(s.path).control()['generation'] == 2


def test_configure_participates_in_shared_transaction_and_rolls_back(tmp_path):
    main = Store(tmp_path/'app.db'); s = CryptoStore(main.path)
    with pytest.raises(RuntimeError):
        with main.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            s.configure({'target_dollars': '10'}, db=db)
            assert s.control(db)['target_dollars'] == '10.00'
            raise RuntimeError('Abort all families')
    assert s.control()['target_dollars'] == '5.00'


def test_symbol_and_event_uniqueness_are_durable(tmp_path):
    s = CryptoStore(tmp_path/'app.db')
    first = trade(); assert s.reserve_trade(first)
    assert not s.reserve_trade(trade('other'))
    assert s.reserve_trade(trade('eth', 'ETH/USD'))
    first.update(stage='finished'); s.save_trade(first, finished=True)
    assert not s.reserve_trade(trade())
    assert s.reserve_trade(trade('next'))


def test_exact_claim_is_atomic_and_stale_save_cannot_reset_attempt(tmp_path):
    s = CryptoStore(tmp_path/'app.db'); first = trade(); s.reserve_trade(first)
    stale = deepcopy(first)
    assert s.claim_operation(first, 'entry')
    assert not s.claim_operation(stale, 'entry')
    with pytest.raises(ConcurrentChange): s.save_trade(stale)
    assert CryptoStore(s.path).get_trade('abc')['ops']['entry']['state'] == 'attempted'


def test_crypto_settings_generation_invalidates_claim(tmp_path):
    s = CryptoStore(tmp_path/'app.db'); first = trade(); s.reserve_trade(first)
    old = s.control(); s.configure({'target_dollars': '6'})
    assert not s.claim_operation(first, 'entry', expected_control=old)


def test_global_generation_checked_in_same_reservation_and_claim_transaction(tmp_path):
    main = Store(tmp_path/'app.db'); s = CryptoStore(main.path)
    first = trade(); first['authorization'] = {'global': main.entry_authorization()}
    assert s.reserve_trade(first)
    main.set_control(False)
    assert not s.claim_operation(first, 'entry')
    assert not s.reserve_trade({**trade('second', 'ETH/USD'), 'authorization': first['authorization']})


def test_incidents_are_durable_deduplicated_and_not_cleared_by_toggle(tmp_path):
    s = CryptoStore(tmp_path/'app.db')
    s.incident('uncertain', 'Order unknown', symbol='BTC/USD')
    s.incident('uncertain', 'Duplicate read', symbol='BTC/USD')
    s.configure({'enabled': False})
    assert len(CryptoStore(s.path).incidents()) == 1
    s.resolve_incident('uncertain')
    assert not s.incidents()
