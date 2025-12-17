# tests/test_helpers_parse_groups.py
import importlib
from tests.conftest import xml_groups_array

def test_parse_group_ids_from_xml_array(monkeypatch):
    import changing_state_of_group as mod
    importlib.reload(mod)

    xml = xml_groups_array(["g1","g2","g1"])  # dedupe forventes
    gids = mod._parse_group_ids_from_xml(xml)
    assert gids == ["g1","g2"]
