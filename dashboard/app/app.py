import logging

import dash
import dash_bootstrap_components as dbc
import plotly.io as pio

from . import firmware_tab, history_tab, live_state, mqtt_control, weather_tab
from .callbacks import register_callbacks
from .layout import build_layout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dashboard.app")

# Fond blanc pour tous les graphiques, assorti aux cartes.
pio.templates.default = "plotly_white"

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY, dbc.icons.BOOTSTRAP], title="Supervision Arrosage")
app.layout = build_layout()
register_callbacks(app)
firmware_tab.register_callbacks(app)
weather_tab.register_callbacks(app)
history_tab.register_callbacks(app)

live_state.start()

try:
    # Filet de sécurité en plus des messages retenus par le broker : demande
    # à chaque device déjà connu de republier son état complet, au cas où le
    # dashboard aurait démarré avec un cache vide (et où les messages
    # retenus auraient été perdus, ex: volume Mosquitto réinitialisé).
    mqtt_control.request_resync_all_known_devices()
except Exception:
    log.exception("Échec de la demande de resynchronisation au démarrage")

server = app.server

if __name__ == "__main__":
    # threaded=True : un envoi OTA (POST bloquant, potentiellement plusieurs
    # secondes) ne doit pas geler le dashboard pour les autres requêtes.
    app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
