import sys
import csv
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth

# --- ACCT config (tilpas til dit miljø) ---
ACCT_BASE = "https://test.acct.dk/rest/current"
ACCT_USER = "LystSvoemSandbox_rest"
ACCT_PASS = "NF2Vd"
GROUP_ID  = "e9d39db7-b38f-43db-bfe1-d9a3a8f4b177"   # den gruppe nye brugere skal i

# namespaces
NS_MAIN = "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary"
NS_ARR  = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"

# register namespaces once; avoid 'ns\d' prefixes
ET.register_namespace("", NS_MAIN)
ET.register_namespace("arr", NS_ARR)

auth = HTTPBasicAuth(ACCT_USER, ACCT_PASS)

def read_cards_from_rasmus(path, card_col="Card", name_col=None, pid_col=None):
    """
    Returnerer dict: card -> {"name": <str or ''>, "pid": <str or ''>}
    """
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        # fleksibel fallback hvis kolonnenavnene ikke passer
        fields = { (h or "").strip().lower(): h for h in (r.fieldnames or []) }
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

def read_existing_cards(path, card_col="Card"):
    """Returnerer set af kort, der allerede findes i systemet (fra all_users.csv)."""
    cards = set()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fields = { (h or "").strip().lower(): h for h in (r.fieldnames or []) }
        col_card = fields.get(card_col.lower()) or (r.fieldnames[0] if r.fieldnames else None)
        if not col_card:
            raise ValueError("all_users.csv mangler en 'Card'-kolonne.")
        for row in r:
            c = (row.get(col_card) or "").strip()
            if c:
                cards.add(c)
    return cards

def build_userdata_xml(card: str, name: str, pid: str|None, group_id: str) -> bytes:
    """
    Bygger <UserData> XML som serveren forventer:
    <UserData xmlns=".../AcctPublicRestCommunicationLibrary" xmlns:ns2=".../Arrays">
      <Card>...</Card>
      <Groups><ns2:string>GROUP_ID</ns2:string></Groups>
      <Name>...</Name>
      <Pid>...</Pid>             (udelades hvis tom)
      <UType>Normal</UType>
    </UserData>
    """
    root = ET.Element(ET.QName(NS_MAIN, "UserData"))

    el_card = ET.SubElement(root, ET.QName(NS_MAIN, "Card"))
    el_card.text = card

    el_groups = ET.SubElement(root, ET.QName(NS_MAIN, "Groups"))
    # use the Arrays namespace with the 'arr' prefix we registered
    el_g_str  = ET.SubElement(el_groups, ET.QName(NS_ARR, "string"))
    el_g_str.text = group_id

    el_name = ET.SubElement(root, ET.QName(NS_MAIN, "Name"))
    el_name.text = (name or card)

    if pid:
        el_pid = ET.SubElement(root, ET.QName(NS_MAIN, "Pid"))
        el_pid.text = pid

    el_utype = ET.SubElement(root, ET.QName(NS_MAIN, "UType"))
    el_utype.text = "Normal"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def create_user(card: str, name: str, pid: str|None) -> tuple[bool, str|None]:
    """
    POST /users med UserData XML.
    Succecodes: 200/201/204. 409 = konflikt (findes måske allerede).
    Returnerer (ok, info)
    """
    url = f"{ACCT_BASE}/users"
    body = build_userdata_xml(card, name, pid, GROUP_ID)
    headers = {"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml"}
    r = requests.post(url, data=body, auth=auth, headers=headers, timeout=30)

    if r.status_code in (200, 201, 204):
        return True, None
    if r.status_code == 409:
        return False, "already_exists"
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return False, f"{r.status_code} {(r.text or '')[:300]}"
    return True, None

def main():
    ap = argparse.ArgumentParser(description="Opret manglende brugere fra rasmus-liste.csv")
    ap.add_argument("rasmus_csv", help="fx rasmus-liste.csv (Card[,Name,Pid])")
    ap.add_argument("all_users_csv", help="fx all_users.csv (Card,UserID,...) for at kende eksisterende kort")
    ap.add_argument("--card-col", default="Card")
    ap.add_argument("--name-col", default="engangsarmbånd")
    ap.add_argument("--pid-col",  default="EventIT" + card)
    args = ap.parse_args()

    rasmus = read_cards_from_rasmus(args.rasmus_csv, args.card_col, args.name_col, args.pid_col)
    existing_cards = read_existing_cards(args.all_users_csv, args.card_col)

    to_create = [c for c in rasmus.keys() if c not in existing_cards]
    print(f"🔎 Mangler i systemet: {len(to_create)} kort (oprettes som brugere)")

    ok = 0
    conflicts = 0
    errs = []
    for card in to_create:
        name = rasmus[card]["name"]
        pid  = rasmus[card]["pid"]
        success, info = create_user(card, name, pid)
        if success:
            ok += 1
            print(f"✅ Oprettet bruger – Card {card} (Name: {name or card})")
        else:
            if info == "already_exists":
                conflicts += 1
                print(f"• Springes over – Card {card} findes allerede (409)")
            else:
                errs.append({"card": card, "error": info})
                print(f"❌ Fejl for Card {card}: {info}")

    print("\n--- Resultat ---")
    print(f"Oprettet: {ok}  | Allerede fandtes: {conflicts}  | Fejl: {len(errs)}")
    if errs:
        Path("create_user_errors.json").write_text(
            __import__("json").dumps(errs, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print("📝 Fejl gemt i create_user_errors.json")

if __name__ == "__main__":
    main()
