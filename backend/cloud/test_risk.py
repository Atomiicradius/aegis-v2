from backend.cloud.risk import is_critical, mission_value, risk


def test_bounds():
    assert risk(0, 0, 1) == 1.0
    assert risk(5, 24, 0) == 0.0
    assert risk(50, 500, -3) == 0.0       # inputs are clamped


def test_closer_miss_raises_risk():
    assert risk(1.0, 10, 0.5) > risk(3.0, 10, 0.5) > risk(5.0, 10, 0.5)


def test_sooner_tca_raises_risk():
    assert risk(2.0, 1, 0.5) > risk(2.0, 12, 0.5) > risk(2.0, 24, 0.5)


def test_higher_mvi_raises_risk():
    assert risk(2.0, 10, 0.9) > risk(2.0, 10, 0.5) > risk(2.0, 10, 0.1)


def test_critical_threshold():
    assert is_critical(0.8) and not is_critical(0.79)
    assert is_critical(0.5, threshold=0.5)


def test_mission_value_is_max_of_pair():
    # 25544 -> mvi 0.95, 49271 -> mvi 0.15
    assert mission_value("49271", "25544") == 0.95
    assert mission_value("25544", "49271") == 0.95
