import sys
import csv
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth
import os
import json

# Sørg for at utils kan findes
sys.path.append(str(Path(__file__).resolve().parent))
from utils.xml_utils import sort_children_alphabetically
from utils.sync_log import SyncLog

# --- ACCT config ---
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current").rstrip("/")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")
GROUP_ID  = os.getenv("GROUP_ID", "")

# Namespaces
NS_MAIN = "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary"
NS_ARR  = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
NS_XSI  = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("", NS_MAIN)
ET.register_namespace("arr", NS_ARR)
ET.register_namespace("i", NS_XSI)

auth = HTTPBasicAuth(ACCT_USER, ACCT_PASS)

# Optional cache (Card -> UserID) for færre API-calls
CACHE_FILE = "acct_card_user_cache.json"


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_first_text(node: ET.Element, local_names: set[str]) -> str:
    targets = {n.lower() for n in local_names}
    for el in node.iter():
        if _local(el.tag).lower() in targets:
            return (el.text or "").strip()
    return ""


def load_cache(path: str) -> dict[str, str]:
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


def save_cache(path: str, cache: dict[str, str]) -> None:
    Path(path).write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_users_from_xml(xml_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except Exception:
        return out

    for node in root.iter():
        card = _find_first_text(node, {"Card"})
        if not card:
            continue
        uid = _find_first_text(node, {"UserID", "UserId", "Guid", "ID", "Id"})
        if card and uid:
            out[card] = uid
    return out


def lookup_userid_by_card(card: str, cache: dict[str, str]) -> str | None:
    card = (card or "").strip()
    if not card:
        return None
    if card in cache:
        return cache[card]

    if not ACCT_USER or not ACCT_PASS:
        raise RuntimeError("ACCT_USER/ACCT_PASS mangler i env. Sæt dem før du kører.")
    if not GROUP_ID:
        raise RuntimeError("GROUP_ID mangler i env. Sæt den før du kører.")

    headers = {"Accept": "application/xml"}
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
            if len(mapping) == 1:
                only_uid = next(iter(mapping.values()))
                cache[card] = only_uid
                return only_uid

    return None


def _load_existing_cards(csv_path: str) -> set[str]:
    """Indlaes kort-numre fra group_members.csv (genereret af build_members_csv.py)."""
    cards = set()
    p = Path(csv_path)
    if not p.exists():
        print(f"Advarsel: {csv_path} ikke fundet — kan ikke filtrere eksisterende kort")
        return cards
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            card = (row.get("Card") or "").strip()
            if card:
                cards.add(card)
    return cards


def read_cards_from_rasmus(path: str, card_col="Card", name_col="Name", pid_col=None):
    """
    Returnerer dict: card -> {"name": <str or ''>, "pid": <str or ''>}
    """
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fields = {(h or "").strip().lower(): h for h in (r.fieldnames or [])}
        col_card = fields.get(card_col.lower()) or (r.fieldnames[0] if r.fieldnames else None)
        col_name = fields.get((name_col or "").lower()) if name_col else None
        col_pid  = fields.get((pid_col  or "").lower()) if pid_col  else None

        if not col_card:
            raise ValueError("rasmus-liste.csv skal have en kolonne med kortnumre (fx 'Card').")

        for row in r:
            card = (row.get(col_card) or "").strip()
            if not card:
                continue
            name = (row.get(col_name) or "").strip() if col_name else ""
            pid  = (row.get(col_pid)  or "").strip() if col_pid  else ""
            out[card] = {"name": name, "pid": pid}
    return out


def build_userdata_xml(card: str, name: str, pid: str | None, group_id: str) -> bytes:
    """
    Bygger <UserData> XML som serveren forventer.
    EntryRemaining saettes til 1 (en enkelt adgang).
    """
    root = ET.Element(ET.QName(NS_MAIN, "UserData"))

    # Card
    el_card = ET.SubElement(root, ET.QName(NS_MAIN, "Card"))
    el_card.text = card

    # EntryRemaining (nil="true")
    el_er = ET.SubElement(root, ET.QName(NS_MAIN, "EntryRemaining"))
    el_er.text = "1"

    # Groups
    el_groups = ET.SubElement(root, ET.QName(NS_MAIN, "Groups"))
    el_g_str  = ET.SubElement(el_groups, ET.QName(NS_ARR, "string"))
    el_g_str.text = group_id

    # Name
    el_name = ET.SubElement(root, ET.QName(NS_MAIN, "Name"))
    el_name.text = (name or card)

    # Pid (valgfri)
    if pid:
        el_pid = ET.SubElement(root, ET.QName(NS_MAIN, "Pid"))
        el_pid.text = pid

    # UType
    el_utype = ET.SubElement(root, ET.QName(NS_MAIN, "UType"))
    el_utype.text = "Normal"

    sort_children_alphabetically(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def create_user(card: str, name: str, pid: str | None) -> tuple[bool, str | None]:
    url = f"{ACCT_BASE}/users"
    body = build_userdata_xml(card, name, pid, GROUP_ID)
    headers = {"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml"}
    r = requests.post(url, data=body, auth=auth, headers=headers, timeout=30)

    # ✅ include 202 as a success
    if r.status_code in (200, 201, 202, 204):
        return True, None
    if r.status_code == 409:
        return False, "already_exists"

    try:
        r.raise_for_status()
    except requests.HTTPError:
        return False, f"{r.status_code} {(r.text or '')[:300]}"

    return True, None


def main():
    ap = argparse.ArgumentParser(description="Opret manglende brugere fra rasmus-liste.csv (uden all_users.csv)")
    ap.add_argument("rasmus_csv", help="fx rasmus-liste.csv (Card[,Name,Pid])")
    ap.add_argument("--card-col", default="Card")
    ap.add_argument("--name-col", default="Name")
    ap.add_argument("--pid-col",  default=None)
    ap.add_argument("--dry-run", action="store_true", help="Vis hvad der ville blive oprettet, uden at oprette")
    args = ap.parse_args()

    if not ACCT_USER or not ACCT_PASS:
        raise RuntimeError("ACCT_USER/ACCT_PASS mangler i env.")
    if not GROUP_ID:
        raise RuntimeError("GROUP_ID mangler i env.")

    rasmus = read_cards_from_rasmus(args.rasmus_csv, args.card_col, args.name_col, args.pid_col)

    # Brug group_members.csv til at afgøre hvilke kort der allerede eksisterer
    # (filen er genereret af build_members_csv.py i trin 2)
    existing_cards = _load_existing_cards("group_members.csv")

    to_create: list[str] = []
    already_exists: list[str] = []

    for card in rasmus.keys():
        if card in existing_cards:
            already_exists.append(card)
        else:
            to_create.append(card)

    print(f"Findes allerede i gruppen (via group_members.csv): {len(already_exists)}")
    print(f"Mangler i systemet: {len(to_create)} kort (oprettes som brugere)")

    if args.dry_run:
        Path("to_create_cards.json").write_text(json.dumps(to_create, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Dry-run: gemt liste i to_create_cards.json")
        return

    log = SyncLog()

    ok = 0
    conflicts = 0
    errs = []

    for card in to_create:
        name = rasmus[card]["name"]
        pid  = rasmus[card]["pid"]
        success, info = create_user(card, name, pid)
        if success:
            ok += 1
            log.log("CREATE", card, f"Bruger oprettet (Name: {name or card})")
            print(f"Oprettet bruger – Card {card} (Name: {name or card})")
        else:
            if info == "already_exists":
                conflicts += 1
                print(f"Springes over – Card {card} findes allerede (409)")
            else:
                errs.append({"card": card, "error": info})
                log.log("CREATE_FEJL", card, f"Fejl: {info}")
                print(f"Fejl for Card {card}: {info}")

    print("\n--- Resultat ---")
    print(f"Oprettet: {ok}  | Allerede fandtes (409): {conflicts}  | Fejl: {len(errs)}")

    if errs:
        Path("create_user_errors.json").write_text(json.dumps(errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Fejl gemt i create_user_errors.json")

    log.flush()

    # Tip: efter oprettelser, kør diff-script igen så to_add kan mappes til UserIDs
    print("\nKør nu member_rasmus_diff.py igen for at få to_add.json udfyldt via API.")


if __name__ == "__main__":
    main()
