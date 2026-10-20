"""Conversion de la distance ultrason en niveau de cuve.

Le firmware publie la distance brute capteur -> surface de l'eau
(arrosage/<device_id>/etat/water_distance_cm) sans rien savoir de la cuve :
c'est ici, avec les réglages TANK_DISTANCE_EMPTY_CM / TANK_DISTANCE_FULL_CM,
qu'elle devient une hauteur d'eau depuis le fond. Comme pour Zigbee (voir
zigbee.py), ingest republie ce niveau dans le contrat device
(arrosage/<device_id>/etat/water_level_cm) : il le stocke ensuite par son
abonnement habituel, et le dashboard comme le scheduler le voient sans
connaître la conversion. La distance brute reste stockée telle quelle,
utile pour recalibrer.
"""

import json

from . import config


def tank_capacity_cm():
    """Hauteur d'eau d'une cuve pleine, ou None si les réglages sont
    incohérents (cuve pleine plus loin du capteur que cuve vide)."""
    capacity = config.TANK_DISTANCE_EMPTY_CM - config.TANK_DISTANCE_FULL_CM
    return capacity if capacity > 0 else None


def water_level_cm(distance_cm):
    """Distance capteur -> surface (cm) -> hauteur d'eau depuis le fond
    (cm), bornée à [0, capacité] : plus loin que le fond (écho parasite) ou
    plus près que la surface pleine (zone morte du capteur, débordement) ne
    donne pas un niveau hors cuve. None si les réglages sont incohérents."""
    capacity = tank_capacity_cm()
    if capacity is None:
        return None
    level = config.TANK_DISTANCE_EMPTY_CM - distance_cm
    return round(min(max(level, 0.0), capacity), 1)


def republish(client, device_id, level_cm):
    """Retenu, comme le firmware : le dashboard retrouve le dernier niveau
    à sa reconnexion."""
    client.publish(
        f"arrosage/{device_id}/etat/{config.TANK_LEVEL_METRIC}",
        json.dumps({"value": level_cm}),
        qos=1,
        retain=True,
    )
