import csv
import json
import os
from pathlib import Path
from typing import Dict, Optional, Set, Tuple
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPBasicAuth

GROUP_MEMBERS_FILE = "group_members.csv"   # Card,Name,UserID,EntryRemaining
RASMUS_FILE        = "rasmus-liste.csv"    # Card

DELETE_JSON        = "to_delete.json"      # {"to_delete": [<UserID>, ...]}
ADD_JSON           = "to_add.json"         # {"to_add":    [<UserID>, ...]}
UPDATE_JSON        = "to_update.json"      # {"to_update": [<UserID>, ...]}
MISSING_JSON       = "missing_cards.json"  # Cards i Rasmus uden mapping i ACCT

# Optional cache (Card -> UserID) for færre API-calls
CACHE_FILE         = "acct_card_user_cache.json"

# --- ACCT config ---
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current").rstrip("/")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")

# Namespaces (bedste bud – vi parser også “namespace-agnostisk”)
NS_MAIN = "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary"
ET.register_namespace("", NS_MAIN)

auth = HTTPBasicAuth(ACCT_USER, ACCT_PASS)


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_first_text(node: ET.Element, local_names: Set[str]) -> str:
    for el in node.iter():
        if _local(el.tag).lower() in {n.lower() for n in local_names}:
            return (el.text or "").strip()
    return ""


def load_cache(path: str) -> Dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items() if k and v}
    except Exception:
        pass
    return {}


def save_cache(path: str, cache: Dict[str, str]) -> None:
    Path(path).write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_users_from_xml(xml_text: str) -> Dict[str, str]:
    """
    Robust parser: forsøger at finde records med felterne Card og (UserID|Guid|Id).
    Returnerer mapping: Card -> UserID/Guid/Id (string).
    """
    out: Dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except Exception:
        return out

    # Vi prøver at finde “record-noder” ved at lede efter noder der indeholder både Card og UserID/Guid/Id
    for node in root.iter():
        card = ""
        uid = ""

        # hurtigt filter: kun noder der har et Card child et sted under sig
        card = _find_first_text(node, {"Card"})
        if not card:
            continue

        uid = _find_first_text(node, {"UserID", "UserId", "Guid", "ID", "Id"})
        if card and uid:
            out[card] = uid

    return out


def lookup_userid_by_card(card: str, cache: Dict[str, str]) -> Optional[str]:
    card = (card or "").strip()
    if not card:
        return None
    if card in cache:
        return cache[card]

    if not ACCT_USER or not ACCT_PASS:
        raise RuntimeError("ACCT_USER/ACCT_PASS mangler i env. Sæt dem før du kører.")

    headers = {"Accept": "application/xml"}

    # Prøv et par almindelige patterns (GET er safe)
    candidates = [
        f"{ACCT_BASE}/users?card={card}",
        f"{ACCT_BASE}/users/card/{card}",
        f"{ACCT_BASE}/users/{card}",
    ]

    for url in candidates:
        try:
            r = requests.get(url, auth=auth, headers=headers, timeout=30)
        except requests.RequestException:
            continue

        if r.status_code in (404, 204):
            continue

        if 200 <= r.status_code < 300:
            mapping = parse_users_from_xml(r.text)
            uid = mapping.get(card)
            if uid:
                cache[card] = uid
                return uid

            # fallback: hvis response indeholder præcis 1 bruger, tag dens uid
            if len(mapping) == 1:
                only_uid = next(iter(mapping.values()))
                cache[card] = only_uid
                return only_uid

            continue

    return None


def load_rasmus_cards(path: str) -> Set[str]:
    cards: Set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return cards
        card_col = "Card" if "Card" in r.fieldnames else r.fieldnames[0]
        for row in r:
            c = (row.get(card_col) or "").strip()
            if c:
                cards.add(c)
    return cards


def load_group_members(path: str) -> Dict[str, Dict[str, str]]:
    """Returner {Card: {"UserID": ..., "EntryRemaining": ...}} for nuværende gruppemedlemmer."""
    by_card: Dict[str, Dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise ValueError("group_members.csv mangler headers.")
        fields = {(h or "").strip().lower(): h for h in r.fieldnames}
        col_card = fields.get("card")
        col_uid = fields.get("userid") or fields.get("guid")
        col_entry = fields.get("entryremaining")
        if not (col_card and col_uid):
            raise ValueError("group_members.csv skal have kolonnerne 'Card' og 'UserID' (eller 'guid').")
        for row in r:
            card = (row.get(col_card) or "").strip()
            uid = (row.get(col_uid) or "").strip()
            entry = (row.get(col_entry) or "").strip() if col_entry else ""
            if card and uid:
                by_card[card] = {"UserID": uid, "EntryRemaining": entry}
    return by_card


def main():
    cache = load_cache(CACHE_FILE)

    rasmus_cards = load_rasmus_cards(RASMUS_FILE)
    group_by_card = load_group_members(GROUP_MEMBERS_FILE)
    group_cards = set(group_by_card.keys())

    # Diffs by Card
    to_delete_cards = sorted(group_cards - rasmus_cards)
    to_add_cards = sorted(rasmus_cards - group_cards)

    # to_delete: kan altid mappes fra group_members.csv
    to_delete = sorted({group_by_card[c]["UserID"] for c in to_delete_cards if c in group_by_card})

    # to_add: slå Card -> UserID op via API
    to_add: list[str] = []
    missing: list[str] = []
    for c in to_add_cards:
        uid = lookup_userid_by_card(c, cache)
        if uid:
            to_add.append(uid)
        else:
            missing.append(c)
    to_add = sorted(set(to_add))

    # to_update: EntryRemaining == "0" for current members, excluding anything slated for delete
    to_delete_set = set(to_delete)
    to_update = sorted({
        data["UserID"]
        for _, data in group_by_card.items()
        if data.get("UserID")
        and data["UserID"] not in to_delete_set
        and (data.get("EntryRemaining") or "").strip() == "0"
    })

    # Write JSON outputs
    Path(ADD_JSON).write_text(json.dumps({"to_add": to_add}, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(DELETE_JSON).write_text(json.dumps({"to_delete": to_delete}, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(UPDATE_JSON).write_text(json.dumps({"to_update": to_update}, indent=2, ensure_ascii=False), encoding="utf-8")

    save_cache(CACHE_FILE, cache)

    print(f" to_add.json:    {len(to_add)} UserIDs")
    print(f" to_delete.json: {len(to_delete)} UserIDs")
    print(f" to_update.json: {len(to_update)} UserIDs (EntryRemaining=0, excl. to_delete)")

    if missing:
        Path(MISSING_JSON).write_text(json.dumps(sorted(missing), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"⚠️  {len(missing)} Cards fra rasmus-liste blev ikke fundet via API → {MISSING_JSON}")
        print("   Kør create_missing_users.py først, og kør derefter member_rasmus_diff.py igen.")


if __name__ == "__main__":
    main()
