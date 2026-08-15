import pytest

from app import live_state, valve_timers


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Les modules valve_timers/live_state gardent un état en mémoire au
    niveau module (voulu en prod) : on le remet à zéro entre chaque test pour
    ne pas dépendre de l'ordre d'exécution."""
    valve_timers._close_at.clear()
    live_state._latest.clear()
    yield
    valve_timers._close_at.clear()
    live_state._latest.clear()
