# export_all_users.py
import csv
import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth

# --- ACCT config ---
ACCT_BASE = "https://test.acct.dk/rest/current"
ACCT_USER = "LystSvoemSandbox_rest"
ACCT_PASS = "NF2Vd"

OUTPUT_CSV = "all_users.csv"

NS = {
    "n": "http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary",
    "i": "http://www.w3.org/2001/XMLSchema-instance",
}
auth = HTTPBasicAuth(ACCT_USER, ACCT_PASS)

def get_xml(url: str) -> ET.Element:
    r = requests.get(url, auth=auth, headers={"Accept": "application/xml"}, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.text)

def get_entry_remaining(user_el: ET.Element) -> str:
    """
    Returnér EntryRemaining som str:
    - '1' hvis elementet findes og har i:nil="true"
    - tallet (fx '0', '3', …) hvis elementet har tekst
    - '0' hvis elementet ikke findes eller er tomt
    """
    er = user_el.find("n:EntryRemaining", NS)
    if er is None:
        return "0"
    is_nil = er.attrib.get(f"{{{NS['i']}}}nil", "").lower() == "true"
    if is_nil:
        return "1"
    value = (er.text or "").strip()
    return value if value else "0"

def parse_users(xml_root: ET.Element):
    """Returnér liste af dicts: {guid, card, name, entry_remaining} fra <UserCollection>."""
    users = []
    for u in xml_root.findall("n:User", NS):
        user_id_uri = (u.findtext("n:UserID", default="", namespaces=NS) or "").strip()
        guid = user_id_uri.rsplit("/", 1)[-1] if user_id_uri else ""
        card = (u.findtext("n:Card", default="", namespaces=NS) or "").strip()
        name = (u.findtext("n:Name", default="", namespaces=NS) or "").strip()
        entry_remaining = get_entry_remaining(u)
        if guid:  # kræv mindst en GUID
            users.append({
                "guid": guid,
                "card": card,
                "name": name,
                "entry_remaining": entry_remaining,
            })
    return users

def main():
    print("🔎 Henter alle brugere…")
    root = get_xml(f"{ACCT_BASE}/users")
    users = parse_users(root)
    print(f"• Fundet {len(users)} brugere i /users")

    wrote = 0
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Card", "Name", "UserID", "EntryRemaining"])
        for u in users:
            w.writerow([u["card"], u["name"], u["guid"], u["entry_remaining"]])
            wrote += 1

    print(f"✅ Skrev {wrote} rækker til {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
