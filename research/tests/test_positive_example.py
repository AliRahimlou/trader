from research.synthetic_example import accepted_long


def test_positive_example_is_explicitly_synthetic_and_grants_no_order_permission():
    sample=accepted_long()
    assert sample['evidence_type']=='synthetic_unit_fixture'
    assert sample['setup']['state']=='SETUP_READY'
    assert sample['setup']['direction']=='long'
    assert sample['setup']['stop']<sample['setup']['entry']<sample['setup']['target']
    assert sample['setup']['can_enter'] is False
    assert sample['live_order_authorized'] is False
