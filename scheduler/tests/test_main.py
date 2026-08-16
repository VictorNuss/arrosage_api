from datetime import datetime, time as time_type, timezone

from app.main import _is_due


def _program(days_of_week, hour, minute):
    return {"days_of_week": days_of_week, "start_time": time_type(hour, minute)}


def test_is_due_matches_day_and_exact_minute():
    # 2026-08-20 est un jeudi (isoweekday=4)
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)
    assert _is_due(_program([4], 6, 30), now) is True


def test_is_due_false_when_wrong_day():
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)  # jeudi
    assert _is_due(_program([1, 2, 3, 5, 6, 7], 6, 30), now) is False  # tous les jours sauf jeudi


def test_is_due_false_when_wrong_minute():
    now = datetime(2026, 8, 20, 6, 31, tzinfo=timezone.utc)
    assert _is_due(_program([4], 6, 30), now) is False


def test_is_due_true_every_day_of_week():
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)
    assert _is_due(_program([1, 2, 3, 4, 5, 6, 7], 6, 30), now) is True
