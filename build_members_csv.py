# build_members_csv.py
import csv
import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth
import os
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")
GROUP_ID  = os.getenv("GROUP_ID", "")

OUTPUT_CSV = "group_members.csv"

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
    Samme logik som export_all_users.py:
    - '1' hvis elementet findes og har i:nil="true"
    - tallet (fx '0', '3', …) hvis elementet har tekst
    - '0' hvis elementet ikke findes eller er tomt
    """
    er = user_el.find("n:EntryRemaining", NS)
    if er is None:
        return "0"
    is_nil = er.attrib.get(f"{{{NS['i']}}}nil", "").lower() == "true"
    if is_nil:
        return "nil"  
    value = (er.text or "").strip()
    return value if value else "0"

def parse_users(xml_root: ET.Element):
    users = []
    for u in xml_root.findall("n:User", NS):
        user_id_uri = (u.findtext("n:UserID", default="", namespaces=NS) or "").strip()
        guid = user_id_uri.rsplit("/", 1)[-1] if user_id_uri else ""
        card = (u.findtext("n:Card", default="", namespaces=NS) or "").strip()
        name = (u.findtext("n:Name", default="", namespaces=NS) or "").strip()
        entry_remaining = get_entry_remaining(u)
        if guid:
            users.append({
                "guid": guid,
                "card": card,
                "name": name,
                "entry_remaining": entry_remaining
            })
    return users

def main():
    print(" Henter gruppens medlemmer…")
    group_users_root = get_xml(f"{ACCT_BASE}/groups/{GROUP_ID}/users")
    group_users = parse_users(group_users_root)
    print(f"• Fundet {len(group_users)} medlemmer i gruppen")

    wrote = 0
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Card", "Name", "UserID", "EntryRemaining"])
        for u in group_users:
            if not u["card"]:
                continue
            w.writerow([u["card"], u["name"], u["guid"], u["entry_remaining"]])
            wrote += 1

    print(f" Skrev {wrote} medlemmer til {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
