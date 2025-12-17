# member_rasmus_diff.py
import csv
import json
from pathlib import Path

GROUP_MEMBERS_FILE = "group_members.csv"   # Card,Name,UserID,EntryRemaining
RASMUS_FILE        = "rasmus-liste.csv"    # Card
ALL_USERS_FILE     = "all_users.csv"       # Card,UserID (eller guid)

DELETE_JSON        = "to_delete.json"      # {"to_delete": [<UserID>, ...]}
ADD_JSON           = "to_add.json"         # {"to_add":    [<UserID>, ...]}
UPDATE_JSON        = "to_update.json"      # {"to_update": [<UserID>, ...]}
MISSING_JSON       = "missing_cards.json"  # Cards i Rasmus uden mapping i all_users.csv

def load_rasmus_cards(path: str) -> set[str]:
    cards = set()
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

def load_group_members(path: str) -> dict[str, dict]:
    """Returner {Card: {"UserID": ..., "EntryRemaining": ...}} for nuværende gruppemedlemmer."""
    by_card = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise ValueError("group_members.csv mangler headers.")
        fields = { (h or "").strip().lower(): h for h in r.fieldnames }
        col_card  = fields.get("card")
        col_uid   = fields.get("userid") or fields.get("guid")
        col_entry = fields.get("entryremaining")
        if not (col_card and col_uid):
            raise ValueError("group_members.csv skal have kolonnerne 'Card' og 'UserID' (eller 'guid').")
        for row in r:
            card = (row.get(col_card) or "").strip()
            uid  = (row.get(col_uid)  or "").strip()
            entry= (row.get(col_entry) or "").strip() if col_entry else ""
            if card and uid:
                by_card[card] = {"UserID": uid, "EntryRemaining": entry}
    return by_card

def build_card_to_userid(path: str) -> dict[str, str]:
    """Returner Card -> UserID (eller guid) fra all_users.csv."""
    mapping = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise ValueError("all_users.csv mangler headers.")
        fields = { (h or "").strip().lower(): h for h in r.fieldnames }
        col_card = fields.get("card")
        col_uid  = fields.get("userid") or fields.get("guid")
        if not (col_card and col_uid):
            raise ValueError("all_users.csv skal have kolonnerne 'Card' og 'UserID' (eller 'guid').")
        for row in r:
            card = (row.get(col_card) or "").strip()
            uid  = (row.get(col_uid)  or "").strip()
            if card and uid:
                mapping[card] = uid
    return mapping

def main():
    # Load inputs
    rasmus_cards   = load_rasmus_cards(RASMUS_FILE)
    group_by_card  = load_group_members(GROUP_MEMBERS_FILE)
    card_to_userid = build_card_to_userid(ALL_USERS_FILE)

    group_cards = set(group_by_card.keys())

    # Diffs by Card
    to_delete_cards = sorted(group_cards - rasmus_cards)
    to_add_cards    = sorted(rasmus_cards - group_cards)

    # Map to GUIDs
    to_delete = sorted({ group_by_card[c]["UserID"] for c in to_delete_cards if c in group_by_card })
    to_add    = []
    missing   = []
    for c in to_add_cards:
        uid = card_to_userid.get(c)
        if uid:
            to_add.append(uid)
        else:
            missing.append(c)
    to_add = sorted(set(to_add))  # dedup + sort

    # to_update = EntryRemaining == "0" for current members, excluding anything slated for delete
    to_delete_set = set(to_delete)
    to_update = sorted({
        data["UserID"]
        for c, data in group_by_card.items()
        if data.get("UserID")
            and data["UserID"] not in to_delete_set
            and (data.get("EntryRemaining") or "").strip() == "0"
    })

    # Write JSON outputs
    Path(ADD_JSON).write_text(   json.dumps({"to_add": to_add}, indent=2, ensure_ascii=False),   encoding="utf-8")
    Path(DELETE_JSON).write_text(json.dumps({"to_delete": to_delete}, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(UPDATE_JSON).write_text(json.dumps({"to_update": to_update}, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f" to_add.json:    {len(to_add)} GUIDs")
    print(f" to_delete.json: {len(to_delete)} GUIDs")
    print(f" to_update.json: {len(to_update)} GUIDs (EntryRemaining=0, excl. to_delete)")

    # Log cards from Rasmus with no mapping in all_users.csv
    if missing:
        Path(MISSING_JSON).write_text(json.dumps(sorted(missing), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"⚠️  {len(missing)} Cards fra rasmus-liste fandtes ikke i all_users.csv → {MISSING_JSON}")

if __name__ == "__main__":
    main()
