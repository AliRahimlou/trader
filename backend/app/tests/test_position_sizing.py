from backend.app.risk.position_sizing import calculate_position_size, profile_for


def test_conservative_position_sizing_caps_risk():
    result = calculate_position_size(
        entry_price=100,
        stop_loss=95,
        capital_allocation=10_000,
        account_equity=100_000,
        buying_power=50_000,
        risk_level="conservative",
    )
    assert result.shares == 100
    assert result.risk_dollars == 500
    assert profile_for("conservative")["max_trade_risk_pct"] == 0.01


def test_position_sizing_rejects_bad_stop():
    result = calculate_position_size(
        entry_price=100,
        stop_loss=101,
        capital_allocation=10_000,
        account_equity=100_000,
        buying_power=50_000,
        risk_level="balanced",
    )
    assert result.shares == 0
    assert "Invalid" in result.reason
