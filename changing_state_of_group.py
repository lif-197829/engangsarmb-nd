# changing_state_of_group.py
import sys, os
import json
import csv
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
import xml.etree.ElementTree as ET
from utils.xml_utils import sort_children_alphabetically

NS_USERDATA = "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_S = "http://schemas.microsoft.com/2003/10/Serialization"

NS_MAIN = NS_USERDATA
NS_ARR  = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"

# Sørg for pæn namespace-serialisering
ET.register_namespace("", NS_USERDATA)
ET.register_namespace("i", NS_XSI)
ET.register_namespace("arr", NS_ARR)

DELETE_STRATEGY = os.getenv("DELETE_STRATEGY", "group_only")  # "group_only" | "delete_user"

# --- ACCT config ---
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")
GROUP_ID  = os.getenv("GROUP_ID", "")

auth = HTTPBasicAuth(ACCT_USER, ACCT_PASS)

# ---------- helpers ----------
def load_ids_from_json_or_csv(path: Path):
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        if isinstance(data, dict):
            for key in ("user_ids", "to_add", "to_delete", "to_update", "ids"):
                vals = data.get(key)
                if isinstance(vals, list):
                    return [str(x).strip() for x in vals if str(x).strip()]
            for v in data.values():
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()]
        raise ValueError(f"{path.name}: JSON must be a list of GUIDs or contain 'user_ids'/'to_add'/'to_delete'/'to_update'.")
    if path.suffix.lower() == ".csv":
        ids = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return ids
            lower = [c.lower() for c in reader.fieldnames]
            col = None
            if "userid" in lower:
                col = reader.fieldnames[lower.index("userid")]
            elif "guid" in lower:
                col = reader.fieldnames[lower.index("guid")]
            if not col:
                raise ValueError(f"{path.name}: CSV must have 'UserID' or 'guid' column.")
            for row in reader:
                v = (row.get(col) or "").strip()
                if v:
                    ids.append(v)
        return ids
    if path.suffix.lower() in (".txt", ""):
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    raise ValueError(f"{path.name}: unsupported file type. Use .json / .csv / .txt")

def _parse_group_ids_from_xml(xml_text: str) -> list[str]:
    # håndter både ArrayOfstring og evt. simple <string>-lister
    NS = {"s": "http://schemas.microsoft.com/2003/10/Serialization/Arrays"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    gids = []
    for el in root.findall(".//s:string", NS):
        v = (el.text or "").strip()
        if v:
            gids.append(v.rsplit("/", 1)[-1])
    if not gids:
        for el in root.findall(".//string"):
            v = (el.text or "").strip()
            if v:
                gids.append(v.rsplit("/", 1)[-1])
    out, seen = [], set()
    for g in gids:
        if g not in seen:
            seen.add(g); out.append(g)
    return out

def _get_user_groups(user_guid: str) -> list[str]:
    url = f"{ACCT_BASE}/users/{user_guid}/groups"
    try:
        r = requests.get(url, auth=auth, headers={"Accept": "application/xml"}, timeout=15)
        if r.status_code != 200:
            return []
        return _parse_group_ids_from_xml(r.text)
    except requests.RequestException:
        return []

def _lname(tag: str) -> str:
    return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()

def _get_card_name(user_guid: str) -> tuple[str, str]:
    url = f"{ACCT_BASE}/users/{user_guid}"
    r = requests.get(url, auth=auth, headers={"Accept":"application/xml"}, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    card = name = ""
    for el in root:
        t = _lname(el.tag)
        if t == "card":
            card = (el.text or "").strip()
        elif t == "name":
            name = (el.text or "").strip()
    return card, (name or card or "")

# ---------- API ops ----------
def add_user_to_group(user_guid: str) -> tuple[bool, str | None]:
    """
    Tilføj bruger til GROUP_ID uden at miste andre gruppemedlemskaber.
    - Bevarer EntryRemaining (nil='true' eller tekst)
    - Unionerer eksisterende grupper med GROUP_ID
    - PUT'er minimal <UserData> (alfabetisk sorteret)
    """
    url_user = f"{ACCT_BASE}/users/{user_guid}"

    # 1) Hent aktuel bruger (Card/Name/EntryRemaining)
    try:
        g = requests.get(url_user, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
        if g.status_code == 404:
            return False, "user_not_found"
        g.raise_for_status()
    except requests.RequestException as e:
        return False, f"GET failed: {e}"

    try:
        cur = ET.fromstring(g.content)
    except ET.ParseError as e:
        return False, f"parse_error: {e}"

    def lname(tag: str) -> str:
        return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()

    cur_card = ""
    cur_name = ""
    entry_nil = False
    entry_text = None

    for el in cur:
        t = lname(el.tag)
        if t == "card":
            cur_card = (el.text or "").strip()
        elif t == "name":
            cur_name = (el.text or "").strip()
        elif t == "entryremaining":
            if el.attrib.get(f"{{{NS_XSI}}}nil", "").lower() == "true":
                entry_nil = True
            else:
                entry_text = (el.text or "").strip() or None

    if not cur_card:
        cur_card = user_guid
    if not cur_name:
        cur_name = cur_card

    # 2) Hent nuværende grupper → union med GROUP_ID
    current_groups = set()
    try:
        current_groups.update(_get_user_groups(user_guid))
    except Exception:
        pass

    # fallback: parse GroupCollection/<GroupID> hvis ovenstående gav tomt
    if not current_groups:
        try:
            rgrp = requests.get(f"{ACCT_BASE}/users/{user_guid}/groups",
                                auth=auth, headers={"Accept": "application/xml"}, timeout=15)
            if rgrp.status_code == 200:
                try:
                    gr = ET.fromstring(rgrp.content)
                    for e in gr.iter():
                        if lname(e.tag) == "groupid":
                            url = (e.text or "").strip()
                            if url:
                                current_groups.add(url.rsplit("/", 1)[-1])
                except ET.ParseError:
                    pass
        except requests.RequestException:
            pass

    if GROUP_ID in current_groups:
        return True, "already_in_group"
    current_groups.add(GROUP_ID)

    # 3) Byg minimal <UserData> med bevaret EntryRemaining + ALLE grupper
    ud = ET.Element(ET.QName(NS_MAIN, "UserData"))

    el_card = ET.SubElement(ud, ET.QName(NS_MAIN, "Card"))
    el_card.text = cur_card

    el_entry = ET.SubElement(ud, ET.QName(NS_MAIN, "EntryRemaining"))
    if entry_nil:
        el_entry.set(ET.QName(NS_XSI, "nil"), "true")
    elif entry_text is not None:
        el_entry.text = entry_text

    el_groups = ET.SubElement(ud, ET.QName(NS_MAIN, "Groups"))
    for gid in sorted(current_groups):
        ET.SubElement(el_groups, ET.QName(NS_ARR, "string")).text = gid

    ET.SubElement(ud, ET.QName(NS_MAIN, "Name")).text = cur_name or cur_card
    ET.SubElement(ud, ET.QName(NS_MAIN, "UType")).text = "Normal"

    sort_children_alphabetically(ud)
    put_xml = ET.tostring(ud, encoding="utf-8", xml_declaration=True)

    # 4) PUT med fallback uden XML-deklaration ved 400
    try:
        p = requests.put(
            url_user,
            data=put_xml,
            auth=auth,
            headers={"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml"},
            timeout=20,
        )
        if p.status_code not in (200, 202, 204):
            if p.status_code == 400:
                put_xml_no_decl = ET.tostring(ud, encoding="utf-8", xml_declaration=False)
                p2 = requests.put(
                    url_user,
                    data=put_xml_no_decl,
                    auth=auth,
                    headers={"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml"},
                    timeout=20,
                )
                if p2.status_code not in (200, 202, 204):
                    return False, f"{p2.status_code} {(p2.text or '')[:200]}"
            else:
                return False, f"{p.status_code} {(p.text or '')[:200]}"
    except requests.RequestException as e:
        return False, f"PUT failed: {e}"

    # 5) Re-check membership (tåler eventual consistency)
    try:
        gg = requests.get(f"{ACCT_BASE}/users/{user_guid}/groups",
                          auth=auth, headers={"Accept": "application/xml"}, timeout=15)
        if gg.status_code == 200 and f"/groups/{GROUP_ID}" in (gg.text or ""):
            return True, None
    except requests.RequestException:
        pass

    return True, None

def delete_user(user_guid: str) -> tuple[bool, str | None]:
    """
    Slet brugeren helt: DELETE /users/{user_guid}
    Returnerer (ok, info). info kan være "already_deleted" ved 404.
    """
    url = f"{ACCT_BASE}/users/{user_guid}"
    r = requests.delete(url, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
    if r.status_code in (200, 204):
        return True, None
    if r.status_code == 404:
        return True, "already_deleted"
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return False, f"{r.status_code} {(r.text or '')[:200]}"
    return True, None

def _write_debug_xml(path: str, content: bytes):
    try:
        from pathlib import Path
        Path(path).write_text(content.decode("utf-8", errors="ignore"), encoding="utf-8")
    except Exception:
        pass

def remove_user_from_group(user_guid: str) -> tuple[bool, str | None]:
    """
    Fjern user fra GROUP_ID.
    1) Forsøg officielt endpoint: DELETE /groups/{GROUP_ID}/users/{user_guid}
    2) Fallback hvis 400/405: PUT /users/{guid} med <Groups> = eksisterende minus GROUP_ID
    """
    # --- 1) Prøv DELETE membership endpoint ---
    url_del = f"{ACCT_BASE}/groups/{GROUP_ID}/users/{user_guid}"
    try:
        r = requests.delete(url_del, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
        if r.status_code in (200, 204):
            return True, None
        if r.status_code == 404:
            return True, "already_not_in_group"
        # hvis 400/405 → prøv fallback
    except requests.RequestException:
        pass

    # --- 2) Fallback: PUT UserData uden denne gruppe ---
    # GET nuværende bruger + grupper
    try:
        g = requests.get(f"{ACCT_BASE}/users/{user_guid}", auth=auth, headers={"Accept":"application/xml"}, timeout=20)
        if g.status_code == 404:
            return True, "already_deleted"
        g.raise_for_status()
    except requests.RequestException as e:
        return False, f"GET failed: {e}"

    try:
        root = ET.fromstring(g.content)
    except ET.ParseError as e:
        return False, f"parse_error: {e}"

    def lname(tag: str) -> str:
        return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()

    cur_card = cur_name = ""
    entry_nil = False
    entry_text = None
    for el in root:
        t = lname(el.tag)
        if t == "card":
            cur_card = (el.text or "").strip()
        elif t == "name":
            cur_name = (el.text or "").strip()
        elif t == "entryremaining":
            if el.attrib.get(f"{{{NS_XSI}}}nil", "").lower() == "true":
                entry_nil = True
            else:
                entry_text = (el.text or "").strip() or None
    if not cur_card: cur_card = user_guid
    if not cur_name: cur_name = cur_card

    current = set()
    try:
        current.update(_get_user_groups(user_guid))
    except Exception:
        pass

    if GROUP_ID not in current:
        return True, "already_not_in_group"
    remaining = sorted(current - {GROUP_ID})

    # Byg UserData uden target-gruppen
    ud = ET.Element(ET.QName(NS_MAIN, "UserData"))
    ET.SubElement(ud, ET.QName(NS_MAIN, "Card")).text = cur_card

    el_entry = ET.SubElement(ud, ET.QName(NS_MAIN, "EntryRemaining"))
    if entry_nil:
        el_entry.set(ET.QName(NS_XSI, "nil"), "true")
    elif entry_text is not None:
        el_entry.text = entry_text

    el_groups = ET.SubElement(ud, ET.QName(NS_MAIN, "Groups"))
    for gid in remaining:
        ET.SubElement(el_groups, ET.QName(NS_ARR, "string")).text = gid

    ET.SubElement(ud, ET.QName(NS_MAIN, "Name")).text = cur_name
    ET.SubElement(ud, ET.QName(NS_MAIN, "UType")).text = "Normal"

    sort_children_alphabetically(ud)
    put_xml = ET.tostring(ud, encoding="utf-8", xml_declaration=True)

    try:
        p = requests.put(
            f"{ACCT_BASE}/users/{user_guid}",
            data=put_xml,
            auth=auth,
            headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml"},
            timeout=20,
        )
        if p.status_code not in (200,202,204):
            if p.status_code == 400:
                put_xml2 = ET.tostring(ud, encoding="utf-8", xml_declaration=False)
                p2 = requests.put(
                    f"{ACCT_BASE}/users/{user_guid}",
                    data=put_xml2,
                    auth=auth,
                    headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml"},
                    timeout=20,
                )
                if p2.status_code not in (200,202,204):
                    return False, f"{p2.status_code} {(p2.text or '')[:200]}"
            else:
                return False, f"{p.status_code} {(p.text or '')[:200]}"
    except requests.RequestException as e:
        return False, f"PUT failed: {e}"

    # verify: ikke længere i gruppen
    gg = requests.get(f"{ACCT_BASE}/users/{user_guid}/groups", auth=auth, headers={"Accept":"application/xml"}, timeout=15)
    ok = (gg.status_code == 200 and f"/groups/{GROUP_ID}" not in (gg.text or ""))
    return (True, None) if ok else (False, "still_in_group_after_put")

def set_entry_remaining(user_guid: str, target: str = "1") -> tuple[bool, str | None]:
    """
    Sæt EntryRemaining til '1' (unlimited) eller '0' (tekst "0").
    Multi-phase fallback for at tvinge serveren til at persistere:
      Phase A: nil=true
      Phase B: text "1"
      Phase C: helt uden <EntryRemaining/>
    Returnerer False med forklaring hvis alt fejler.
    """
    import time

    url_user = f"{ACCT_BASE}/users/{user_guid}"

    # 1) GET bruger (Card/Name)
    try:
        g = requests.get(url_user, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
        g.raise_for_status()
    except requests.RequestException as e:
        return False, f"GET failed: {e}"

    try:
        root_cur = ET.fromstring(g.content)
    except ET.ParseError as e:
        return False, f"parse_error: {e}"

    def lname(tag: str) -> str:
        return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()

    cur_card = cur_name = ""
    for el in root_cur:
        t = lname(el.tag)
        if t == "card": cur_card = (el.text or "").strip()
        elif t == "name": cur_name = (el.text or "").strip()
    if not cur_card: cur_card = user_guid
    if not cur_name: cur_name = cur_card

    groups_now = _get_user_groups(user_guid)

    def _build_and_put(mode: str) -> tuple[bool, str]:
        """
        mode:
          "nil"  -> <EntryRemaining i:nil="true"/>
          "text" -> <EntryRemaining>1</EntryRemaining> (eller "0")
          "none" -> (intet EntryRemaining-element)
        """
        ud = ET.Element(ET.QName(NS_USERDATA, "UserData"))
        ET.SubElement(ud, ET.QName(NS_USERDATA, "Card")).text = cur_card

        # CardPin i:nil="true" hjælper nogle WCF set-ups
        el_pin = ET.SubElement(ud, ET.QName(NS_USERDATA, "CardPin"))
        el_pin.attrib[ET.QName(NS_XSI, "nil")] = "true"

        if mode != "none":
            el_entry = ET.SubElement(ud, ET.QName(NS_USERDATA, "EntryRemaining"))

            if target == "1":
                el_entry.text = "1"        # <-- ALTID tekst "1"
            else:
                el_entry.text = "0"


        if groups_now:
            el_groups = ET.SubElement(ud, ET.QName(NS_USERDATA, "Groups"))
            for gid in sorted(set(groups_now)):
                ET.SubElement(el_groups, ET.QName(NS_ARR, "string")).text = gid

        ET.SubElement(ud, ET.QName(NS_USERDATA, "Name")).text = cur_name
        ET.SubElement(ud, ET.QName(NS_USERDATA, "UType")).text = "Normal"

        sort_children_alphabetically(ud)
        put_xml = ET.tostring(ud, encoding="utf-8", xml_declaration=True)
        try:
            p = requests.put(
                url_user, data=put_xml, auth=auth,
                headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml"},
                timeout=20
            )
            if p.status_code not in (200,202,204):
                if p.status_code == 400:
                    put_xml2 = ET.tostring(ud, encoding="utf-8", xml_declaration=False)
                    p2 = requests.put(
                        url_user, data=put_xml2, auth=auth,
                        headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml"},
                        timeout=20
                    )
                    if p2.status_code not in (200,202,204):
                        return False, f"{p2.status_code}"
                else:
                    return False, f"{p.status_code}"
        except requests.RequestException as e:
            return False, f"PUT failed: {e}"
        return True, "ok"

    def _verify() -> bool:
        NS = {"n": NS_USERDATA, "i": NS_XSI}
        try:
            r = requests.get(url_user, auth=auth, headers={"Accept": "application/xml"}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            er = root.find("n:EntryRemaining", NS)
            nil = (er is not None and er.attrib.get(f"{{{NS['i']}}}nil", "").lower() == "true")
            txt = (er.text or "").strip() if er is not None else ""
            if target == "1":
                return (txt == "1")
            else:
                return (txt == "0")
        except Exception:
            return False

    # Phase A: altid skriv tal som tekst (1 eller 0) — aldrig nil
    phaseA = "text"
    ok, _ = _build_and_put(phaseA); time.sleep(0.4)
    if _verify(): return True, None


    # Phase B: text "1" (kun hvis target == "1")
    if target == "1":
        ok, _ = _build_and_put("text"); time.sleep(0.4)
        if _verify(): return True, None

        # Phase C: fjern elementet helt
        ok, _ = _build_and_put("none"); time.sleep(0.4)
        if _verify(): return True, None

    return False, "persist_failed"

# ---------- main ----------
def main():
    # standardfilnavne (kan overrides via args)
    to_add_path     = Path("to_add.json")
    to_delete_path  = Path("to_delete.json")
    to_update_path  = Path("to_update.json")

    # valgfri: python changing_state_of_group.py to_add.json to_delete.json to_update.json
    if len(sys.argv) >= 2:
        to_add_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        to_delete_path = Path(sys.argv[2])
    if len(sys.argv) >= 4:
        to_update_path = Path(sys.argv[3])

    # Indlæs lister
    to_add_ids     = load_ids_from_json_or_csv(to_add_path)    if to_add_path.exists()    else []
    to_delete_ids  = load_ids_from_json_or_csv(to_delete_path) if to_delete_path.exists() else []
    to_update_ids  = load_ids_from_json_or_csv(to_update_path) if to_update_path.exists() else []

    print(f"Indlæst {len(to_add_ids)} GUIDs fra {to_add_path.name} (to_add)")
    print(f"Indlæst {len(to_delete_ids)} GUIDs fra {to_delete_path.name} (to_delete)")
    print(f"Indlæst {len(to_update_ids)} GUIDs fra {to_update_path.name} (to_update)")

    # ADD
    add_ok = add_already = 0
    add_errs = []
    for uid in to_add_ids:
        ok, info = add_user_to_group(uid)
        if ok and info == "already_in_group":
            add_already += 1
            print(f"ADD {uid}: allerede i gruppen (409)")
        elif ok:
            add_ok += 1
            print(f"ADD {uid}: tilføjet")
        else:
            add_errs.append({"user_id": uid, "error": info})
            print(f"ADD {uid}: fejl – {info}")

    # DELETE (afmelding fra gruppen eller fuld sletning)
    del_ok = del_already = 0
    del_errs = []
    for uid in to_delete_ids:
        if DELETE_STRATEGY == "group_only":
            ok, info = remove_user_from_group(uid)
        else:
            ok, info = delete_user(uid)
        if ok and info in ("already_deleted", "already_not_in_group"):
            del_already += 1
            print(f"DEL {uid}: {info.replace('_',' ')}")
        elif ok:
            del_ok += 1
            # FIX: korrekt f-string i begge grene
            print(f"DEL {uid}: fjernet fra gruppe" if DELETE_STRATEGY=="group_only" else f"DEL {uid}: bruger slettet")
        else:
            del_errs.append({"user_id": uid, "error": info})
            print(f"DEL {uid}: fejl – {info}")

    # UPDATE entryRemaining -> -1 (nil=true)
    upd_ok = upd_err = 0
    upd_errs = []
    for uid in to_update_ids:
        ok, info = set_entry_remaining(uid, "1")
        if ok:
            upd_ok += 1
            print(f"UPD {uid}: entryRemaining sat til 1")
        else:
            upd_err += 1
            upd_errs.append({"user_id": uid, "error": info})
            print(f"UPD {uid}: fejl – {info}")

    print("\n--- Resultat ---")
    print(f"Tilføjet: {add_ok}  | Allerede i gruppen: {add_already}  | ADD fejl: {len(add_errs)}")
    print(f"Slettet (brugere): {del_ok}  | Allerede slettet/ikke i gruppe: {del_already} | DEL fejl: {len(del_errs)}")
    print(f"Opdateret entryRemaining=1: {upd_ok} | UPD fejl: {upd_err}")

    if add_errs:
        Path("add_errors.json").write_text(json.dumps(add_errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("ADD-fejl gemt i add_errors.json")
    if del_errs:
        Path("delete_errors.json").write_text(json.dumps(del_errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("DEL-fejl gemt i delete_errors.json")
    if upd_errs:
        Path("update_errors.json").write_text(json.dumps(upd_errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("UPD-fejl gemt i update_errors.json")

if __name__ == "__main__":
    main()
