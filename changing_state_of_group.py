# changing_state_of_group.py
import sys
import json
import csv
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
import xml.etree.ElementTree as ET

NS_S = "http://schemas.microsoft.com/2003/10/Serialization/"

# --- ACCT config ---
import os
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

# ---------- API ops ----------
def add_user_to_group(user_guid: str) -> tuple[bool, str | None]:
    # 1) POST /groups/{GROUP_ID}/users/{USER_GUID}
    url_group = f"{ACCT_BASE}/groups/{GROUP_ID}/users/{user_guid}"
    r = requests.post(url_group, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
    if r.status_code in (200, 204, 201, 409):
        return True, ("already_in_group" if r.status_code == 409 else None)
    if r.status_code == 405:
        allow = r.headers.get("Allow")
        print(f"ℹ️  405 på POST {url_group} — Allow: {allow}")

    # 2) PUT samme URL
    r2 = requests.put(url_group, auth=auth, headers={"Accept": "application/xml"}, timeout=20)
    if r2.status_code in (200, 204, 409):
        return True, ("already_in_group" if r2.status_code == 409 else None)
    if r2.status_code == 405:
        allow = r2.headers.get("Allow")
        print(f"ℹ️  405 på PUT {url_group} — Allow: {allow}")

    # 3) PUT /users/{uid}/groups med WCF string-body
    url_user = f"{ACCT_BASE}/users/{user_guid}/groups"
    body_xml = f'<string xmlns="http://schemas.microsoft.com/2003/10/Serialization/">{GROUP_ID}</string>'
    r3 = requests.put(
        url_user,
        data=body_xml.encode("utf-8"),
        auth=auth,
        headers={"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml"},
        timeout=20,
    )
    if r3.status_code in (200, 204, 409):
        return True, ("already_in_group" if r3.status_code == 409 else None)

    # 4) text/plain fallback
    if r3.status_code == 400:
        r4 = requests.put(
            url_user,
            data=GROUP_ID.encode("utf-8"),
            auth=auth,
            headers={"Content-Type": "text/plain; charset=utf-8", "Accept": "application/xml"},
            timeout=20,
        )
        if r4.status_code in (200, 204, 409):
            return True, ("already_in_group" if r4.status_code == 409 else None)
        try:
            r4.raise_for_status()
        except requests.HTTPError:
            return False, f"{r4.status_code} {r4.text[:200]}"

    try:
        r3.raise_for_status()
    except requests.HTTPError:
        return False, f"{r3.status_code} {r3.text[:200]}"
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

# ---------- main ----------
def main():
    # standardfilnavne (kan overrides via args)
    to_add_path    = Path("to_add.json")
    to_delete_path = Path("to_delete.json")

    # valgfri: python sync_group_from_json.py to_add.json to_delete.json
    if len(sys.argv) >= 2:
        to_add_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        to_delete_path = Path(sys.argv[2])

    # Indlæs lister
    to_add_ids    = load_ids_from_json_or_csv(to_add_path) if to_add_path.exists() else []
    to_delete_ids = load_ids_from_json_or_csv(to_delete_path) if to_delete_path.exists() else []

    print(f"➡️  Indlæst {len(to_add_ids)} GUIDs fra {to_add_path.name} (to_add)")
    print(f"➡️  Indlæst {len(to_delete_ids)} GUIDs fra {to_delete_path.name} (to_delete)")

    # ADD
    add_ok = add_already = 0
    add_errs = []
    for uid in to_add_ids:
        ok, info = add_user_to_group(uid)
        if ok and info == "already_in_group":
            add_already += 1
            print(f"• ADD {uid}: allerede i gruppen (409)")
        elif ok:
            add_ok += 1
            print(f"✅ ADD {uid}: tilføjet")
        else:
            add_errs.append({"user_id": uid, "error": info})
            print(f"❌ ADD {uid}: fejl – {info}")

    # DELETE (fuld sletning af bruger)
    del_ok = del_already = 0
    del_errs = []
    for uid in to_delete_ids:
        ok, info = delete_user(uid)
        if ok and info == "already_deleted":
            del_already += 1
            print(f"• DEL {uid}: var allerede slettet (404)")
        elif ok:
            del_ok += 1
            print(f"🗑️  DEL {uid}: bruger slettet")
        else:
            del_errs.append({"user_id": uid, "error": info})
            print(f"❌ DEL {uid}: fejl – {info}")

    print("\n--- Resultat ---")
    print(f"Tilføjet: {add_ok}  | Allerede i gruppen: {add_already}  | ADD fejl: {len(add_errs)}")
    print(f"Slettet (brugere): {del_ok}  | Allerede slettet: {del_already} | DEL fejl: {len(del_errs)}")

    if add_errs:
        Path("add_errors.json").write_text(json.dumps(add_errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("📝 ADD-fejl gemt i add_errors.json")
    if del_errs:
        Path("delete_errors.json").write_text(json.dumps(del_errs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("📝 DEL-fejl gemt i delete_errors.json")

if __name__ == "__main__":
    main()
