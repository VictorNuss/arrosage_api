from app import callbacks


def _find_all(component, predicate):
    """Parcourt récursivement l'arbre de composants Dash (Card/Row/Col/...)
    et renvoie tous les composants qui matchent `predicate`."""
    found = []
    if predicate(component):
        found.append(component)
    children = getattr(component, "children", None)
    if children is None:
        return found
    if isinstance(children, (list, tuple)):
        for child in children:
            found.extend(_find_all(child, predicate))
    else:
        found.extend(_find_all(children, predicate))
    return found


def _find_one(component, predicate):
    matches = _find_all(component, predicate)
    assert len(matches) == 1, f"attendu exactement 1 match, trouvé {len(matches)}"
    return matches[0]


def _is_component_with_id_type(component, id_type):
    component_id = getattr(component, "id", None)
    return isinstance(component_id, dict) and component_id.get("type") == id_type


def test_send_button_disabled_when_ip_unknown():
    row = callbacks._build_firmware_row({"device_id": "jardin-1", "name": "Jardin", "ip_address": None})
    send_btn = _find_one(row, lambda c: _is_component_with_id_type(c, "ota-send-btn"))
    assert send_btn.disabled is True


def test_send_button_enabled_when_ip_known():
    row = callbacks._build_firmware_row(
        {"device_id": "jardin-1", "name": "Jardin", "ip_address": "192.168.1.50"}
    )
    send_btn = _find_one(row, lambda c: _is_component_with_id_type(c, "ota-send-btn"))
    assert send_btn.disabled is False


def test_refresh_button_present_regardless_of_ip_known():
    for ip_address in (None, "192.168.1.50"):
        row = callbacks._build_firmware_row({"device_id": "jardin-1", "name": "Jardin", "ip_address": ip_address})
        _find_one(row, lambda c: _is_component_with_id_type(c, "device-ip-refresh-btn"))


def test_hint_shown_only_when_ip_unknown():
    row_unknown = callbacks._build_firmware_row({"device_id": "jardin-1", "name": "Jardin", "ip_address": None})
    row_known = callbacks._build_firmware_row(
        {"device_id": "jardin-1", "name": "Jardin", "ip_address": "192.168.1.50"}
    )

    def is_hint(c):
        return type(c).__name__ == "FormText"

    assert len(_find_all(row_unknown, is_hint)) == 1
    assert len(_find_all(row_known, is_hint)) == 0
