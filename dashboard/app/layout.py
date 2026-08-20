from dash import dcc, html
import dash_bootstrap_components as dbc

from . import config


def build_valve_panel():
    """Panneau persistant (visible quel que soit l'onglet actif) listant
    l'état de toutes les vannes en un coup d'œil."""
    return html.Div(
        [
            dcc.Interval(id="global-interval", interval=config.OVERVIEW_REFRESH_INTERVAL_MS),
            # Rafraîchit uniquement la structure du panneau (cartes, menu de
            # durée, boutons) : peu fréquent, exprès. Reconstruire les
            # boutons au même rythme que l'état (2s) ferait perdre des clics
            # si l'utilisateur clique juste au moment d'un rafraîchissement.
            dcc.Interval(id="valve-structure-interval", interval=20_000),
            html.Div(id="valve-command-feedback"),
            html.Div(id="valve-panel"),
        ],
        className="pb-2",
    )


def build_overview_tab():
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(html.Div(id="tank-gauge"), md=4),
                    dbc.Col(html.Div(id="rain-outlook"), md=8),
                ],
                className="g-3 pb-3",
            ),
            html.Div(id="overview-cards"),
        ],
        className="pt-3",
    )


def build_history_tab():
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Label("Appareils"),
                            dcc.Dropdown(id="history-devices", multi=True),
                        ],
                        md=4,
                    ),
                    dbc.Col(
                        [
                            html.Label("Métriques"),
                            dcc.Dropdown(id="history-metrics", multi=True),
                        ],
                        md=4,
                    ),
                    dbc.Col(
                        [
                            html.Label("Période"),
                            dcc.DatePickerRange(
                                id="history-date-range",
                                display_format="DD/MM/YYYY",
                                first_day_of_week=1,
                            ),
                        ],
                        md=4,
                    ),
                ],
                className="g-3 pt-3",
            ),
            dcc.Loading(dcc.Graph(id="history-graph"), className="pt-3"),
        ],
        className="pt-3",
    )


def _windy_radar_url():
    return (
        "https://embed.windy.com/embed2.html"
        f"?lat={config.WEATHER_LAT}&lon={config.WEATHER_LON}"
        f"&detailLat={config.WEATHER_LAT}&detailLon={config.WEATHER_LON}"
        "&width=650&height=450&zoom=8&level=surface"
        "&overlay=radar&product=radar"
        "&menu=&message=true&marker=true&calendar=now&pressure="
        "&type=map&location=coordinates&detail="
        "&metricWind=default&metricTemp=default&radarRange=-1"
    )


def build_weather_tab():
    return html.Div(
        [
            dcc.Interval(id="weather-interval", interval=5 * 60_000),
            html.Div(id="weather-content"),
            html.H5("Carte radar (Windy)", className="pt-4"),
            html.Iframe(
                src=_windy_radar_url(),
                style={"border": "none", "width": "100%", "height": "450px"},
            ),
        ],
        className="pt-3",
    )


def build_firmware_tab():
    return html.Div(
        [
            dcc.Store(id="ota-pending-upload"),
            html.Div(id="ota-feedback"),
            html.Div(id="firmware-rows"),
            dbc.Modal(
                [
                    dbc.ModalHeader("Confirmer la mise à jour"),
                    dbc.ModalBody(id="ota-modal-body"),
                    dbc.ModalFooter(
                        dcc.Loading(
                            dbc.ButtonGroup(
                                [
                                    dbc.Button("Annuler", id="ota-cancel-btn", color="secondary"),
                                    dbc.Button(
                                        "Confirmer et envoyer",
                                        id="ota-confirm-btn",
                                        color="danger",
                                        disabled=False,
                                    ),
                                ]
                            ),
                            type="circle",
                        )
                    ),
                ],
                id="ota-confirm-modal",
                is_open=False,
            ),
        ],
        className="pt-3",
    )


_DAY_OPTIONS = [
    {"label": "Lun", "value": 1},
    {"label": "Mar", "value": 2},
    {"label": "Mer", "value": 3},
    {"label": "Jeu", "value": 4},
    {"label": "Ven", "value": 5},
    {"label": "Sam", "value": 6},
    {"label": "Dim", "value": 7},
]


def build_programs_tab():
    return html.Div(
        [
            dcc.Store(id="program-editing-id"),
            dcc.Store(id="programs-version", data=0),
            html.Div(id="programs-feedback"),
            dbc.Button("+ Nouveau programme", id="new-program-btn", color="primary", className="mb-3", n_clicks=0),
            html.Div(id="programs-list"),
            dbc.Modal(
                [
                    dbc.ModalHeader(id="program-modal-title"),
                    dbc.ModalBody(
                        [
                            html.Div(id="program-form-error"),
                            dbc.Label("Nom"),
                            dbc.Input(id="program-form-name", placeholder="Ex: Potager matin"),
                            dbc.Label("Jours", className="mt-3"),
                            dbc.Checklist(id="program-form-days", options=_DAY_OPTIONS, inline=True),
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Heure de déclenchement"),
                                            dbc.Input(id="program-form-start-time", type="time", value="06:30"),
                                        ],
                                        md=6,
                                    ),
                                    dbc.Col(
                                        [
                                            dbc.Label("Durée (min)"),
                                            dbc.Input(
                                                id="program-form-duration", type="number", min=1, value=10
                                            ),
                                        ],
                                        md=6,
                                    ),
                                ],
                                className="mt-3 g-3",
                            ),
                            dbc.Label("Vannes", className="mt-3"),
                            dcc.Dropdown(id="program-form-valves", multi=True),
                            html.Hr(),
                            html.Div("Conditions", className="fw-bold mb-2"),
                            dbc.Switch(
                                id="program-form-cond-rain-enabled",
                                label="Ne pas arroser s'il pleut prochainement",
                                value=True,
                            ),
                            dbc.Row(
                                dbc.Col(
                                    [
                                        dbc.Label("Fenêtre (h)", size="sm"),
                                        dbc.Input(
                                            id="program-form-cond-rain-hours",
                                            type="number",
                                            min=1,
                                            value=3,
                                            size="sm",
                                        ),
                                    ],
                                    md=4,
                                ),
                                className="mb-2",
                            ),
                            dbc.Switch(
                                id="program-form-cond-tank-enabled",
                                label="Ne pas arroser si la cuve est sous",
                                value=True,
                            ),
                            dbc.Row(
                                dbc.Col(
                                    [
                                        dbc.Label("Seuil (%)", size="sm"),
                                        dbc.Input(
                                            id="program-form-cond-tank-pct",
                                            type="number",
                                            min=0,
                                            max=100,
                                            value=10,
                                            size="sm",
                                        ),
                                    ],
                                    md=4,
                                ),
                            ),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Annuler", id="program-cancel-btn", color="secondary"),
                            dbc.Button("Enregistrer", id="program-save-btn", color="primary"),
                        ]
                    ),
                ],
                id="program-modal",
                is_open=False,
                size="lg",
            ),
            html.H5("Historique récent", className="pt-4"),
            html.Div(id="watering-runs-history"),
        ],
        className="pt-3",
    )


def build_layout():
    return dbc.Container(
        [
            html.H2("Supervision Arrosage", className="pt-4 pb-2"),
            build_valve_panel(),
            dbc.Tabs(
                [
                    dbc.Tab(build_overview_tab(), label="Vue d'ensemble", tab_id="overview"),
                    dbc.Tab(build_history_tab(), label="Historique", tab_id="history"),
                    dbc.Tab(build_weather_tab(), label="Météo", tab_id="weather"),
                    dbc.Tab(build_firmware_tab(), label="Firmware", tab_id="firmware"),
                    dbc.Tab(build_programs_tab(), label="Programmes", tab_id="programs"),
                ],
                id="tabs",
                active_tab="overview",
            ),
        ],
        fluid=True,
    )
