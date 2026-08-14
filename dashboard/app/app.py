import logging

import dash
import dash_bootstrap_components as dbc

from . import live_state
from .callbacks import register_callbacks
from .layout import build_layout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY], title="Supervision Arrosage")
app.layout = build_layout()
register_callbacks(app)

live_state.start()

server = app.server

if __name__ == "__main__":
    # threaded=True : un envoi OTA (POST bloquant, potentiellement plusieurs
    # secondes) ne doit pas geler le dashboard pour les autres requêtes.
    app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
