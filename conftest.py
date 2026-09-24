"""Repository-wide test configuration (all testpaths: pivot/tests, deploy/tests, research/tests)."""
import pytest


@pytest.fixture(autouse=True)
def socrates_rules_for_scenario(request, monkeypatch):
    """Scenario tests written for the 4.5 execution rules keep them unless marked rules46.

    Production reads the 4.6 defaults (10:00-12:00 ET window, longs only, 1.5%
    stop-distance skip, key-level target); pivot/tests/test_v460_rules.py
    covers them with the marker and no environment override.
    """
    monkeypatch.delenv('PIVOT_SOCRATES_SHORTS_LIVE', raising=False)
    if request.node.get_closest_marker('rules46'):
        monkeypatch.delenv('PIVOT_SOCRATES_LEGACY_RULES', raising=False)
    else:
        monkeypatch.setenv('PIVOT_SOCRATES_LEGACY_RULES', '1')


def pytest_configure(config):
    config.addinivalue_line('markers', 'rules46: run with the production 4.6 Socrates execution rules')
