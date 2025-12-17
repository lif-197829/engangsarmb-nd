# tests/test_changing_state_add_remove_update.py
import os
import importlib
import xml.etree.ElementTree as ET
import responses

from tests.conftest import xml_user, xml_groups_array, xml_groupcollection, ACCT_BASE, GROUP_ID

GUID = "01234567-89ab-cdef-0123-456789abcdef"

def _assert_has_group_and_entry(xml_bytes, expect_gid: str, expect_nil: bool | None, expect_text: str | None):
    root = ET.fromstring(xml_bytes)
    ns  = {"n": "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary",
           "i": "http://www.w3.org/2001/XMLSchema-instance",
           "arr": "http://schemas.microsoft.com/2003/10/Serialization/Arrays"}
    # EntryRemaining
    er = root.find("n:EntryRemaining", ns)
    if expect_nil is not None:
        assert er is not None
        is_nil = er.attrib.get("{%s}nil" % ns["i"], "").lower() == "true"
        assert is_nil == expect_nil
    if expect_text is not None:
        assert er is not None
        assert (er.text or "").strip() == expect_text

    # Groups
    g = root.find("n:Groups", ns)
    assert g is not None
    vals = [ (e.text or "").strip() for e in g.findall("arr:string", ns) ]
    assert expect_gid in vals

@responses.activate
def test_add_user_to_group_adds_union_and_preserves_nil(monkeypatch):
    # Import efter env er sat
    import changing_state_of_group as mod
    importlib.reload(mod)

    # 1) GET /users/{guid}
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}",
        body=xml_user("1234567890", "Alice", "nil"),
        status=200, content_type="application/xml",
    )
    # 2) _get_user_groups (pre): ArrayOfstring (tom)
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groups_array([]), status=200, content_type="application/xml",
    )

    # 3) PUT /users/{guid} – assert body har Groups=GROUP_ID og EntryRemaining nil
    def put_cb(req):
        _assert_has_group_and_entry(req.body, GROUP_ID, expect_nil=True, expect_text=None)
        return (202, {"Content-Type":"application/xml"}, "<ok/>")

    responses.add_callback(
        responses.PUT, f"{ACCT_BASE}/users/{GUID}",
        callback=put_cb, content_type="application/xml",
    )

    # 4) Re-check GET /users/{guid}/groups -> nu med gruppen
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groupcollection(ACCT_BASE, [GROUP_ID]),
        status=200, content_type="application/xml",
    )

    ok, info = mod.add_user_to_group(GUID)
    assert ok is True
    assert info in (None, "already_in_group")

@responses.activate
def test_add_user_to_group_already_in_group_short_circuits(monkeypatch):
    import changing_state_of_group as mod
    importlib.reload(mod)

    # GET /users/{guid}
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}",
        body=xml_user("999", "Bob", "0"),
        status=200, content_type="application/xml",
    )
    # _get_user_groups pre-check indeholder GROUP_ID
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groups_array([GROUP_ID]), status=200, content_type="application/xml",
    )

    ok, info = mod.add_user_to_group(GUID)
    assert ok is True
    assert info == "already_in_group"

@responses.activate
def test_remove_user_from_group_puts_minus_group_and_verifies(monkeypatch):
    import changing_state_of_group as mod
    importlib.reload(mod)

    # GET /users/{guid}
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}",
        body=xml_user("CARD", "Name", "nil"),
        status=200, content_type="application/xml",
    )
    # _get_user_groups pre-check: GROUP_ID + another
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groups_array([GROUP_ID, "other-group"]), status=200, content_type="application/xml",
    )

    def put_cb(req):
        # efter remove skal GROUP_ID IKKE længere være i listen, men "other-group" skal
        root = ET.fromstring(req.body)
        ns = {"n":"http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary",
              "arr":"http://schemas.microsoft.com/2003/10/Serialization/Arrays"}
        vals = [ (e.text or "").strip() for e in root.find("n:Groups", ns).findall("arr:string", ns) ]
        assert "other-group" in vals
        assert GROUP_ID not in vals
        return (202, {"Content-Type":"application/xml"}, "<ok/>")

    responses.add_callback(
        responses.PUT, f"{ACCT_BASE}/users/{GUID}",
        callback=put_cb, content_type="application/xml",
    )
    # re-check: uden GROUP_ID
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groupcollection(ACCT_BASE, ["other-group"]),
        status=200, content_type="application/xml",
    )

    ok, info = mod.remove_user_from_group(GUID)
    assert ok is True
    assert info is None

@responses.activate
def test_set_entry_remaining_preserves_groups_and_persists(monkeypatch):
    import changing_state_of_group as mod
    importlib.reload(mod)

    # GET /users/{guid} (før)
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}",
        body=xml_user("CARDX", "X", "0"),
        status=200, content_type="application/xml",
    )
    # Hent eksisterende grupper (bevares)
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}/groups",
        body=xml_groups_array(["g1","g2"]), status=200, content_type="application/xml",
    )

    # PUT – nil=true forventes når value="1"
    def put_cb(req):
        root = ET.fromstring(req.body)
        ns = {"n":"http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary",
              "i":"http://www.w3.org/2001/XMLSchema-instance",
              "arr":"http://schemas.microsoft.com/2003/10/Serialization/Arrays"}
        er = root.find("n:EntryRemaining", ns)
        assert er is not None and er.attrib.get("{%s}nil" % ns["i"]) == "true"
        groups = [ (e.text or "").strip() for e in root.find("n:Groups", ns).findall("arr:string", ns) ]
        assert set(groups) == {"g1","g2"}  # bevaret
        return (202, {"Content-Type":"application/xml"}, "<ok/>")

    responses.add_callback(
        responses.PUT, f"{ACCT_BASE}/users/{GUID}",
        callback=put_cb, content_type="application/xml",
    )

    # Verify-GET – serveren svarer nu med i:nil="true"
    responses.add(
        responses.GET, f"{ACCT_BASE}/users/{GUID}",
        body=xml_user("CARDX", "X", "nil"),
        status=200, content_type="application/xml",
    )

    ok, info = mod.set_entry_remaining(GUID, "1")
    assert ok is True
    assert info is None
