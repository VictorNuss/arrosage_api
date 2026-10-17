from datetime import datetime, timedelta, timezone

import pandas as pd


def format_last_seen(ts) -> str:
    """Durée écoulée depuis `ts` (Timestamp/datetime aware, ou NaT/None),
    formulée pour suivre "Vu ..." : "à l'instant", "il y a 5 min"..."""
    if ts is None or pd.isna(ts):
        return "jamais"
    delta = datetime.now(timezone.utc) - pd.Timestamp(ts).to_pydatetime()
    if delta < timedelta(minutes=1):
        return "à l'instant"
    if delta < timedelta(hours=1):
        return f"il y a {int(delta.total_seconds() // 60)} min"
    if delta < timedelta(days=1):
        return f"il y a {int(delta.total_seconds() // 3600)} h"
    return f"il y a {delta.days} j"
