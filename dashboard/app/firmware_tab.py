"""Onglet Firmware : mise à jour OTA d'un device choisi dans une liste.

L'IP et la version de firmware de chaque device sont publiées par le device
lui-même (clés MQTT "ip" et "fw_version", stockées dans la table devices par
ingest) : l'utilisateur choisit un appareil, dépose un .bin et envoie, sans
avoir à connaître l'IP. La saisie manuelle de l'IP reste possible en repli
(firmware ancien qui ne publie pas encore son IP), repliée par défaut.

Tous les composants interactifs sont statiques (présents dès le chargement
de la page) : seuls les textes descriptifs sont reconstruits, ce qui évite
les callbacks à motifs (ALL) et les clics perdus pendant un rafraîchissement.
"""

import base64

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, dcc, html, no_update

from . import mqtt_control, ota_client, queries
from .formatting import format_last_seen

# Délais avant de recharger la liste des devices : le temps que le device
# republie ses infos (get_status) puis qu'ingest les écrive en base (~2s de
# batching), ou, après un OTA, qu'il redémarre et se reconnecte au broker.
_RELOAD_AFTER_REFRESH_MS = 4_000
_RELOAD_AFTER_OTA_MS = 30_000


# --- Données -----------------------------------------------------------------

def load_devices(devices_df: pd.DataFrame, readings_df: pd.DataFrame) -> list[dict]:
    """Une entrée sérialisable (pour un dcc.Store) par device, avec l'horodatage
    de sa dernière mesure reçue (ISO 8601, ou None si aucune)."""
    last_seen = {}
    if not readings_df.empty:
        times = pd.to_datetime(readings_df["time"], utc=True)
        last_seen = times.groupby(readings_df["device_id"]).max().to_dict()

    devices = []
    for _, row in devices_df.iterrows():
        seen = last_seen.get(row["device_id"])
        devices.append(
            {
                "device_id": row["device_id"],
                "name": row.get("name") or row["device_id"],
                "ip_address": row.get("ip_address") or None,
                "fw_version": row.get("fw_version") or None,
                "last_seen": seen.isoformat() if seen is not None else None,
            }
        )
    return devices


def find_device(devices, device_id):
    return next((d for d in devices or [] if d["device_id"] == device_id), None)


def has_valid_ip(device) -> bool:
    return bool(device) and ota_client.is_valid_ip(device.get("ip_address") or "")


def pick_selected_device(devices, current_device_id):
    """Garde la sélection courante si elle existe toujours, sinon choisit le
    premier device dont l'IP est connue (le seul cas où un envoi est
    possible), à défaut le premier tout court."""
    if find_device(devices, current_device_id):
        return current_device_id
    for device in devices:
        if has_valid_ip(device):
            return device["device_id"]
    return devices[0]["device_id"] if devices else None


# --- Rendu ---------------------------------------------------------------------

def _version_text(device):
    return device.get("fw_version") or "version inconnue"


def build_device_option(device):
    """Option du menu déroulant : nom en gras, puis IP et version en petit."""
    ip_known = has_valid_ip(device)
    details = f"{device['ip_address'] if ip_known else 'IP inconnue'} · {_version_text(device)}"
    return {
        "label": html.Div(
            [
                html.Span(device["name"], className="fw-semibold"),
                html.Span(details, className=f"ms-2 small {'text-muted' if ip_known else 'text-danger'}"),
            ]
        ),
        "value": device["device_id"],
        # Le label étant un composant, `search` indique le texte filtrable.
        "search": f"{device['name']} {device['device_id']} {device.get('ip_address') or ''}",
    }


def _detail(label, value, muted=False):
    return dbc.Col(
        [
            html.Div(label, className="text-uppercase text-muted fw-semibold", style={"fontSize": "0.7rem"}),
            html.Div(value, className="text-muted" if muted else "fw-semibold"),
        ],
        xs=6,
        md=3,
    )


def build_device_details(device):
    if device is None:
        return dbc.Alert("Aucun appareil n'a encore envoyé de données.", color="info", className="mb-0")

    ip_known = has_valid_ip(device)
    last_seen = pd.Timestamp(device["last_seen"]) if device.get("last_seen") else None
    children = [
        dbc.Row(
            [
                _detail("Identifiant", device["device_id"]),
                _detail("Adresse IP", device["ip_address"] if ip_known else "inconnue", muted=not ip_known),
                _detail("Firmware", _version_text(device), muted=not device.get("fw_version")),
                _detail("Dernière mesure", format_last_seen(last_seen)),
            ],
            className="g-3",
        )
    ]
    if not ip_known:
        children.append(
            dbc.Alert(
                "IP inconnue : cliquez « Actualiser » pour demander au device de republier son IP "
                "(get_status), ou saisissez-la manuellement.",
                color="warning",
                className="mt-3 mb-0 py-2 small",
            )
        )
    return children


def build_devices_table(devices):
    if not devices:
        return None
    rows = []
    for device in devices:
        last_seen = pd.Timestamp(device["last_seen"]) if device.get("last_seen") else None
        rows.append(
            html.Tr(
                [
                    html.Td(
                        [
                            html.Div(device["name"], className="fw-semibold"),
                            # Sans nom lisible, name vaut déjà device_id : inutile de le répéter.
                            html.Div(device["device_id"], className="small text-muted")
                            if device["name"] != device["device_id"]
                            else None,
                        ]
                    ),
                    html.Td(device["ip_address"] or html.Span("inconnue", className="text-muted")),
                    html.Td(
                        dbc.Badge(device["fw_version"], color="light", text_color="dark", className="border")
                        if device.get("fw_version")
                        else html.Span("inconnue", className="text-muted")
                    ),
                    html.Td(format_last_seen(last_seen), className="text-muted"),
                ]
            )
        )
    header = html.Thead(html.Tr([html.Th("Appareil"), html.Th("IP"), html.Th("Firmware"), html.Th("Dernière mesure")]))
    return dbc.Table([header, html.Tbody(rows)], hover=True, responsive=True, className="mb-0 align-middle")


def _upload_prompt(filename=None):
    if filename:
        return html.Div(
            [html.I(className="bi bi-file-earmark-binary me-2"), html.Span(filename, className="fw-semibold"),
             html.Div("Cliquer ou glisser un autre fichier pour le remplacer", className="small text-muted")]
        )
    return html.Div(
        [html.I(className="bi bi-cloud-arrow-up fs-4 d-block"),
         html.Span("Glisser un fichier .bin ici ou "), html.Span("parcourir", className="text-primary fw-semibold")]
    )


def build_firmware_tab():
    return html.Div(
        [
            dcc.Store(id="ota-pending-upload"),
            dcc.Store(id="ota-devices", data=[]),
            # Recharge ponctuelle de la liste des devices (après une demande
            # get_status ou un OTA) : réarmé avec max_intervals=1 par les
            # callbacks concernés, désactivé sinon.
            dcc.Interval(id="ota-reload-timer", interval=_RELOAD_AFTER_REFRESH_MS, max_intervals=0),
            html.Div(id="ota-feedback"),
            dbc.Card(
                [
                    dbc.CardHeader(
                        [html.I(className="bi bi-cpu me-2"), "Mise à jour du firmware"], className="fw-semibold"
                    ),
                    dbc.CardBody(
                        [
                            dbc.Row(
                                [
                                    dbc.Col(
                                        [
                                            dbc.Label("Appareil", html_for="ota-device-select"),
                                            dcc.Dropdown(
                                                id="ota-device-select",
                                                placeholder="Choisir un appareil…",
                                                clearable=False,
                                                optionHeight=40,
                                            ),
                                        ],
                                        md=6,
                                    ),
                                    dbc.Col(
                                        [
                                            dbc.Label("Fichier firmware"),
                                            dcc.Upload(
                                                id="ota-upload",
                                                children=_upload_prompt(),
                                                accept=".bin",
                                                className="ota-dropzone",
                                            ),
                                        ],
                                        md=6,
                                    ),
                                ],
                                className="g-3",
                            ),
                            html.Div(id="ota-device-details", className="ota-device-details mt-3"),
                            dbc.Collapse(
                                dbc.InputGroup(
                                    [
                                        dbc.InputGroupText("IP manuelle"),
                                        dbc.Input(id="ota-manual-ip-input", placeholder="192.168.1.50"),
                                        dbc.Button("Enregistrer", id="ota-manual-ip-save-btn", color="secondary"),
                                    ],
                                    className="mt-3",
                                ),
                                id="ota-manual-ip-collapse",
                                is_open=False,
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        [html.I(className="bi bi-arrow-clockwise me-1"), "Actualiser"],
                                        id="ota-device-refresh-btn",
                                        color="link",
                                        size="sm",
                                        className="px-0 me-3",
                                        title="Demander au device de republier son IP et sa version (get_status)",
                                    ),
                                    dbc.Button(
                                        [html.I(className="bi bi-pencil me-1"), "Saisir l'IP manuellement"],
                                        id="ota-manual-ip-toggle",
                                        color="link",
                                        size="sm",
                                        className="px-0",
                                    ),
                                    dbc.Button(
                                        [html.I(className="bi bi-send me-2"), "Envoyer la mise à jour"],
                                        id="ota-send-btn",
                                        color="warning",
                                        disabled=True,
                                        className="ms-auto",
                                    ),
                                ],
                                className="d-flex align-items-center flex-wrap gap-2 mt-3",
                            ),
                        ]
                    ),
                ],
                className="shadow-sm",
            ),
            dbc.Card(
                [
                    dbc.CardHeader([html.I(className="bi bi-hdd-network me-2"), "Appareils connus"],
                                   className="fw-semibold"),
                    html.Div(id="ota-devices-table"),
                ],
                className="shadow-sm mt-4",
            ),
            dbc.Modal(
                [
                    dbc.ModalHeader("Confirmer la mise à jour"),
                    dbc.ModalBody(id="ota-modal-body"),
                    dbc.ModalFooter(
                        dcc.Loading(
                            dbc.ButtonGroup(
                                [
                                    dbc.Button("Annuler", id="ota-cancel-btn", color="secondary"),
                                    dbc.Button("Confirmer et envoyer", id="ota-confirm-btn", color="danger"),
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


def _alert(message, color, duration=6000):
    return dbc.Alert(message, color=color, dismissable=True, duration=duration)


def _reload_timer(delay_ms):
    """Valeurs (interval, n_intervals, max_intervals) qui réarment
    ota-reload-timer pour un unique déclenchement dans `delay_ms`."""
    return delay_ms, 0, 1


# --- Callbacks -----------------------------------------------------------------

def register_callbacks(app):
    @app.callback(
        Output("ota-devices", "data"),
        Input("tabs", "active_tab"),
        Input("ota-reload-timer", "n_intervals"),
    )
    def reload_devices(active_tab, _n):
        if active_tab != "firmware":
            return no_update
        return load_devices(queries.get_devices(), queries.get_latest_readings())

    @app.callback(
        Output("ota-device-select", "options"),
        Output("ota-device-select", "value"),
        Output("ota-devices-table", "children"),
        Input("ota-devices", "data"),
        State("ota-device-select", "value"),
    )
    def update_device_select(devices, current_device_id):
        devices = devices or []
        options = [build_device_option(device) for device in devices]
        return options, pick_selected_device(devices, current_device_id), build_devices_table(devices)

    @app.callback(
        Output("ota-device-details", "children"),
        Output("ota-manual-ip-input", "value"),
        Input("ota-device-select", "value"),
        Input("ota-devices", "data"),
    )
    def show_device_details(device_id, devices):
        device = find_device(devices, device_id)
        return build_device_details(device), (device or {}).get("ip_address") or ""

    @app.callback(
        Output("ota-send-btn", "disabled"),
        Output("ota-device-refresh-btn", "disabled"),
        Output("ota-manual-ip-toggle", "disabled"),
        Input("ota-device-select", "value"),
        Input("ota-devices", "data"),
        Input("ota-upload", "contents"),
    )
    def update_buttons(device_id, devices, contents):
        device = find_device(devices, device_id)
        no_device = device is None
        return not (has_valid_ip(device) and contents), no_device, no_device

    @app.callback(
        Output("ota-upload", "children"),
        Input("ota-upload", "filename"),
    )
    def show_uploaded_filename(filename):
        return _upload_prompt(filename)

    @app.callback(
        Output("ota-manual-ip-collapse", "is_open"),
        Input("ota-manual-ip-toggle", "n_clicks"),
        State("ota-manual-ip-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_manual_ip(_n, is_open):
        return not is_open

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Output("ota-reload-timer", "interval", allow_duplicate=True),
        Output("ota-reload-timer", "n_intervals", allow_duplicate=True),
        Output("ota-reload-timer", "max_intervals", allow_duplicate=True),
        Output("ota-manual-ip-collapse", "is_open", allow_duplicate=True),
        Input("ota-manual-ip-save-btn", "n_clicks"),
        State("ota-device-select", "value"),
        State("ota-manual-ip-input", "value"),
        prevent_initial_call=True,
    )
    def save_manual_ip(_n, device_id, ip_value):
        if not device_id:
            return no_update, no_update, no_update, no_update, no_update
        ip_value = (ip_value or "").strip()
        if not ota_client.is_valid_ip(ip_value):
            return _alert(f"Adresse IP invalide pour {device_id}.", "danger"), no_update, no_update, no_update, True
        queries.set_device_ip(device_id, ip_value)
        # Recharge quasi immédiate : l'IP est déjà en base.
        return (_alert(f"IP enregistrée pour {device_id} : {ip_value}", "success", 4000), *_reload_timer(100), False)

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Output("ota-reload-timer", "interval", allow_duplicate=True),
        Output("ota-reload-timer", "n_intervals", allow_duplicate=True),
        Output("ota-reload-timer", "max_intervals", allow_duplicate=True),
        Input("ota-device-refresh-btn", "n_clicks"),
        State("ota-device-select", "value"),
        prevent_initial_call=True,
    )
    def refresh_device_info(_n, device_id):
        if not device_id:
            return no_update, no_update, no_update, no_update
        mqtt_control.send_get_status(device_id)
        return (
            _alert(f"Demande envoyée à {device_id} : ses infos vont s'actualiser dans quelques secondes.", "info"),
            *_reload_timer(_RELOAD_AFTER_REFRESH_MS),
        )

    @app.callback(
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Output("ota-modal-body", "children"),
        Output("ota-pending-upload", "data"),
        Output("ota-feedback", "children", allow_duplicate=True),
        Input("ota-send-btn", "n_clicks"),
        State("ota-device-select", "value"),
        State("ota-devices", "data"),
        State("ota-upload", "contents"),
        State("ota-upload", "filename"),
        prevent_initial_call=True,
    )
    def open_ota_confirm(_n, device_id, devices, contents, filename):
        device = find_device(devices, device_id)
        if device is None:
            return no_update, no_update, no_update, _alert("Choisissez d'abord un appareil.", "warning")
        if not contents or not filename:
            return no_update, no_update, no_update, _alert("Sélectionnez d'abord un fichier .bin.", "warning")
        if not has_valid_ip(device):
            return no_update, no_update, no_update, _alert(
                f"Adresse IP manquante ou invalide pour {device_id}.", "warning"
            )

        body = html.Div(
            [
                html.P(
                    [
                        "Envoyer ", html.Strong(filename), " vers ", html.Strong(device["name"]),
                        f" ({device['ip_address']}) ?",
                    ]
                ),
                html.P(f"Version actuelle : {_version_text(device)}", className="text-muted small"),
                html.P(
                    "Le device va redémarrer et appliquer le nouveau firmware. "
                    "Toute vanne actuellement ouverte sera fermée pendant le redémarrage.",
                    className="text-danger mb-0",
                ),
            ]
        )
        pending = {
            "device_id": device_id,
            "ip_address": device["ip_address"],
            "filename": filename,
            "contents": contents,
        }
        return True, body, pending, no_update

    @app.callback(
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Input("ota-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_ota(_n):
        return False

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Output("ota-reload-timer", "interval", allow_duplicate=True),
        Output("ota-reload-timer", "n_intervals", allow_duplicate=True),
        Output("ota-reload-timer", "max_intervals", allow_duplicate=True),
        Input("ota-confirm-btn", "n_clicks"),
        State("ota-pending-upload", "data"),
        prevent_initial_call=True,
    )
    def confirm_ota(_n, pending):
        if not pending:
            return no_update, False, no_update, no_update, no_update

        device_id = pending["device_id"]
        ip_address = pending["ip_address"]
        filename = pending["filename"]

        try:
            _header, b64data = pending["contents"].split(",", 1)
            data = base64.b64decode(b64data)
        except Exception:
            return _alert(f"Fichier illisible pour {device_id}.", "danger"), False, no_update, no_update, no_update

        ok, message = ota_client.send_firmware(device_id, ip_address, data)
        alert = _alert(f"{filename} → {device_id} ({ip_address}) : {message}", "success" if ok else "danger", 10000)
        if not ok:
            return alert, False, no_update, no_update, no_update
        # Le device redémarre puis republie sa version à la reconnexion MQTT :
        # recharge la liste un peu plus tard pour afficher la nouvelle version.
        return alert, False, *_reload_timer(_RELOAD_AFTER_OTA_MS)

