import dash
import dash_bootstrap_components as dbc

from .callbacks import register_callbacks
from .layout import build_layout

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY], title="Supervision Arrosage")
app.layout = build_layout()
register_callbacks(app)

server = app.server

if __name__ == "__main__":
    # threaded=True : un envoi OTA (POST bloquant, potentiellement plusieurs
    # secondes) ne doit pas geler le dashboard pour les autres requêtes.
    app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
