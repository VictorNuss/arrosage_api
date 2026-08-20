from app import valve_timers


def test_mark_opened_reports_remaining_close_to_duration():
    valve_timers.mark_opened("jardin-1", "vanne_1", 600)
    remaining = valve_timers.get_remaining_seconds("jardin-1", "vanne_1")
    assert remaining is not None
    assert 595 <= remaining <= 600


def test_mark_closed_clears_the_timer():
    valve_timers.mark_opened("jardin-1", "vanne_2", 600)
    valve_timers.mark_closed("jardin-1", "vanne_2")
    assert valve_timers.get_remaining_seconds("jardin-1", "vanne_2") is None


def test_unknown_valve_has_no_remaining_time():
    assert valve_timers.get_remaining_seconds("jamais-vu", "vanne_1") is None


def test_already_expired_timer_returns_none_and_is_forgotten():
    valve_timers.mark_opened("jardin-1", "vanne_3", -5)
    assert valve_timers.get_remaining_seconds("jardin-1", "vanne_3") is None
    # Régression : un timer expiré doit être purgé, pas juste ignoré une fois
    # (sinon un bug de fuite mémoire passerait inaperçu).
    assert ("jardin-1", "vanne_3") not in valve_timers._close_at


def test_timers_are_independent_per_device_and_metric():
    valve_timers.mark_opened("jardin-1", "vanne_1", 600)
    valve_timers.mark_closed("jardin-2", "vanne_1")
    assert valve_timers.get_remaining_seconds("jardin-1", "vanne_1") is not None


def test_format_remaining_under_an_hour():
    assert valve_timers.format_remaining(4) == "0:04"
    assert valve_timers.format_remaining(65) == "1:05"
    assert valve_timers.format_remaining(599) == "9:59"


def test_format_remaining_an_hour_or_more():
    assert valve_timers.format_remaining(3600) == "1h 00m"
    assert valve_timers.format_remaining(3725) == "1h 02m"
