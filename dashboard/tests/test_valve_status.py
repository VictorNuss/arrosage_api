import pytest

from app import callbacks


@pytest.mark.parametrize("value,expected", [(1.0, "open"), (0.9, "open"), (0.75, "open")])
def test_valve_status_open(value, expected):
    assert callbacks._valve_status(value) == expected


@pytest.mark.parametrize("value,expected", [(0.0, "closed"), (0.1, "closed"), (0.25, "closed")])
def test_valve_status_closed(value, expected):
    assert callbacks._valve_status(value) == expected


@pytest.mark.parametrize("value", [0.5, 0.26, 0.74, 0.4, 0.6])
def test_valve_status_transition(value):
    assert callbacks._valve_status(value) == "transition"


def test_valve_label_open_and_closed():
    assert callbacks._valve_label("open") == "OUVERTE"
    assert callbacks._valve_label("closed") == "FERMÉE"


def test_valve_label_transition_with_known_direction():
    assert callbacks._valve_label("transition", "opening") == "OUVERTURE…"
    assert callbacks._valve_label("transition", "closing") == "FERMETURE…"


def test_valve_label_transition_unknown_direction_falls_back():
    assert callbacks._valve_label("transition", None) == "EN TRANSITION…"
    assert callbacks._valve_label("transition", "sideways") == "EN TRANSITION…"


@pytest.mark.parametrize(
    "status,direction,expected",
    [("open", None, True), ("transition", "opening", True), ("transition", "closing", False),
     ("transition", None, False), ("closed", None, False)],
)
def test_is_opening(status, direction, expected):
    assert callbacks._is_opening(status, direction) is expected


def test_open_button_becomes_extend_when_valve_is_opening():
    assert callbacks._open_button_label("open") == "Prolonger"
    assert callbacks._open_button_label("transition", "opening") == "Prolonger"
    assert callbacks._open_button_label("closed") == "Ouvrir"
    assert callbacks._open_button_label("transition", "closing") == "Ouvrir"


@pytest.mark.parametrize(
    "status,direction,expected",
    [("open", None, False), ("closed", None, False), ("transition", "opening", False),
     ("transition", "closing", True), ("transition", None, True)],
)
def test_valve_buttons_disabled(status, direction, expected):
    assert callbacks._valve_buttons_disabled(status, direction) is expected


def test_valve_color_matches_status():
    assert callbacks._valve_color("open") == callbacks._VALVE_OPEN_COLOR
    assert callbacks._valve_color("closed") == callbacks._VALVE_CLOSED_COLOR
    assert callbacks._valve_color("transition") == callbacks._VALVE_TRANSITION_COLOR
