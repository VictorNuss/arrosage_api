"""Suivi (en mémoire, côté dashboard) de l'heure de fermeture prévue d'une
vanne ouverte avec une durée.

C'est une estimation, pas une vérité terrain : c'est le firmware qui gère
réellement le minuteur (voir esp32/README.md), le dashboard se contente de
retenir "j'ai demandé une ouverture de N minutes à cet instant" pour pouvoir
afficher un compte à rebours. Limites acceptées : perdu si le dashboard
redémarre, et ne sait rien d'une ouverture déclenchée autrement que par ce
dashboard (ex: un futur automatisme, ou une commande manuelle MQTT).
"""

import threading
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_close_at: dict[tuple[str, str], datetime] = {}


def mark_opened(device_id: str, metric: str, duration_s: int) -> None:
    with _lock:
        _close_at[(device_id, metric)] = datetime.now(timezone.utc) + timedelta(seconds=duration_s)


def mark_closed(device_id: str, metric: str) -> None:
    with _lock:
        _close_at.pop((device_id, metric), None)


def get_remaining_seconds(device_id: str, metric: str) -> float | None:
    """None si aucune fermeture programmée connue, ou si elle est déjà passée."""
    with _lock:
        target = _close_at.get((device_id, metric))
    if target is None:
        return None

    remaining = (target - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        with _lock:
            _close_at.pop((device_id, metric), None)
        return None
    return remaining


def format_remaining(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}:{secs:02d}"
