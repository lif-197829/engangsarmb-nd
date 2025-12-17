# tests/test_load_ids_and_build_userdata.py
import json
import csv
import importlib
from pathlib import Path
import xml.etree.ElementTree as ET


# ---------- tests for load_ids_from_json_or_csv ----------

def _load_func():
    import changing_state_of_group as mod
    importlib.reload(mod)
    return mod.load_ids_from_json_or_csv


def test_load_ids_from_json_list(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.json"
    p.write_text(json.dumps([" a ", "b", "", " c "]), encoding="utf-8")

    ids = load_ids(p)
    assert ids == ["a", "b", "c"]


def test_load_ids_from_json_dict_known_key(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.json"
    data = {"to_add": [" 111 ", "222", " "]}
    p.write_text(json.dumps(data), encoding="utf-8")

    ids = load_ids(p)
    assert ids == ["111", "222"]


def test_load_ids_from_json_dict_fallback_value_list(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.json"
    data = {"something_else": ["x", " y ", ""]}
    p.write_text(json.dumps(data), encoding="utf-8")

    ids = load_ids(p)
    assert ids == ["x", "y"]


def test_load_ids_from_json_invalid_structure_raises(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.json"
    # ingen lister overhovedet -> skal fejle
    data = {"foo": 123, "bar": "baz"}
    p.write_text(json.dumps(data), encoding="utf-8")

    try:
        load_ids(p)
        assert False, "Expected ValueError for invalid JSON structure"
    except ValueError as e:
        assert "JSON must be a list of GUIDs" in str(e)


def test_load_ids_from_csv_userid(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["UserID", "Other"])
        w.writerow(["  id1  ", "x"])
        w.writerow(["id2", "y"])
        w.writerow(["", "z"])

    ids = load_ids(p)
    assert ids == ["id1", "id2"]


def test_load_ids_from_csv_guid_header(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["GUID"])
        w.writerow(["g1"])
        w.writerow([" g2 "])

    ids = load_ids(p)
    assert ids == ["g1", "g2"]


def test_load_ids_from_csv_missing_required_header_raises(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SomethingElse"])
        w.writerow(["x"])

    try:
        load_ids(p)
        assert False, "Expected ValueError for missing UserID/guid header"
    except ValueError as e:
        assert "CSV must have 'UserID' or 'guid' column." in str(e)


def test_load_ids_from_txt(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.txt"
    p.write_text("  a  \n\nb\n c \n", encoding="utf-8")

    ids = load_ids(p)
    assert ids == ["a", "b", "c"]


def test_load_ids_from_unsupported_extension_raises(tmp_path):
    load_ids = _load_func()
    p = tmp_path / "ids.bin"
    p.write_bytes(b"whatever")

    try:
        load_ids(p)
        assert False, "Expected ValueError for unsupported file type"
    except ValueError as e:
        assert "unsupported file type" in str(e)


# ---------- tests for create_missing_users.build_userdata_xml ----------

def _userdata_helpers():
    import create_missing_users as cm
    importlib.reload(cm)
    return cm.build_userdata_xml, cm.NS_MAIN, cm.NS_ARR, cm.NS_XSI


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def test_build_userdata_xml_full_with_pid_and_nil_entry():
    build_userdata_xml, NS_MAIN, NS_ARR, NS_XSI = _userdata_helpers()

    card = "1234567890"
    name = "Alice"
    pid  = "999999-9999"
    gid  = "group-123"

    xml_bytes = build_userdata_xml(card, name, pid, gid, set_entry_remaining_nil=True)
    root = ET.fromstring(xml_bytes)

    # Root tag + namespace
    assert root.tag == f"{{{NS_MAIN}}}UserData"

    children = list(root)
    order = [_local(c.tag) for c in children]
    # alfabetisk: Card, EntryRemaining, Groups, Name, Pid, UType
    assert order == ["Card", "EntryRemaining", "Groups", "Name", "Pid", "UType"]

    # Card
    el_card = root.find(f"{{{NS_MAIN}}}Card")
    assert el_card is not None and el_card.text == card

    # EntryRemaining nil="true"
    el_er = root.find(f"{{{NS_MAIN}}}EntryRemaining")
    assert el_er is not None
    assert el_er.attrib.get(f"{{{NS_XSI}}}nil") == "true"

    # Groups
    el_groups = root.find(f"{{{NS_MAIN}}}Groups")
    assert el_groups is not None
    arr_strings = el_groups.findall(f"{{{NS_ARR}}}string")
    vals = [(e.text or "").strip() for e in arr_strings]
    assert vals == [gid]

    # Name
    el_name = root.find(f"{{{NS_MAIN}}}Name")
    assert el_name is not None and el_name.text == name

    # Pid
    el_pid = root.find(f"{{{NS_MAIN}}}Pid")
    assert el_pid is not None and el_pid.text == pid

    # UType
    el_ut = root.find(f"{{{NS_MAIN}}}UType")
    assert el_ut is not None and el_ut.text == "Normal"


def test_build_userdata_xml_no_pid_no_nil_entry_name_fallback():
    build_userdata_xml, NS_MAIN, NS_ARR, NS_XSI = _userdata_helpers()

    card = "5555555555"
    name = ""     # skal falde tilbage til card
    pid  = None
    gid  = "group-XYZ"

    xml_bytes = build_userdata_xml(card, name, pid, gid, set_entry_remaining_nil=False)
    root = ET.fromstring(xml_bytes)

    children = list(root)
    order = [_local(c.tag) for c in children]
    # uden EntryRemaining & Pid: Card, Groups, Name, UType
    assert order == ["Card", "Groups", "Name", "UType"]

    # EntryRemaining skal ikke være der
    assert root.find(f"{{{NS_MAIN}}}EntryRemaining") is None

    # Groups
    el_groups = root.find(f"{{{NS_MAIN}}}Groups")
    assert el_groups is not None
    arr_strings = el_groups.findall(f"{{{NS_ARR}}}string")
    vals = [(e.text or "").strip() for e in arr_strings]
    assert vals == [gid]

    # Name = fallback til card
    el_name = root.find(f"{{{NS_MAIN}}}Name")
    assert el_name is not None and el_name.text == card

    # Ingen Pid
    assert root.find(f"{{{NS_MAIN}}}Pid") is None

    # UType
    el_ut = root.find(f"{{{NS_MAIN}}}UType")
    assert el_ut is not None and el_ut.text == "Normal"
