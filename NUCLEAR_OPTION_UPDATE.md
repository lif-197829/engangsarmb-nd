# Nuclear Option: Simplificeret pipeline

Denne plan erstatter den nuvaerende pipeline med et markant simplere flow
der reducerer API-kald fra ~1600+ til ~413 per koersel.

## Forudsaetning

Armbaandsbrugere er KUN i GROUP_ID — ingen andre grupper.
Det betyder vi kan hardcode Groups=[GROUP_ID] i alle PUT-kald
og droppe alle individuelle group-lookups.

## Nyt flow

```
1. Hent Rasmus-listen        (1 GET Google Sheets -> rasmus-liste.csv)
2. Hent gruppemedlemmer       (1 GET /groups/{GROUP_ID}/users -> group_members.csv)
3. Beregn diff lokalt         (0 API-kald, ren CSV/set-logik)
4. UPDATE                     (N x 1 PUT /users/{guid} — EntryRemaining=1)
5. DELETE                     (N x 1 DELETE /groups/{GROUP_ID}/users/{guid})
6. CREATE                     (N x 1 POST /users — med Groups=[GROUP_ID], EntryRemaining=1)
7. Verificer                  (1 GET /groups/{GROUP_ID}/users — tjek alt stemmer)
```

## Hvad der aendres

### Filer der SLETTES / goeres overflodige
- `member_rasmus_diff.py` — diff-logik flyttes ind i det nye flow
- `create_missing_users.py` — oprettelse flyttes ind i det nye flow
- `find_users.py` — bruges ikke laengere
- `lookup_userid_by_card()` — unodvendig (vi har ikke brug for UserID-lookup per kort)

### Filer der OMSKRIVES
- `changing_state_of_group.py` — simplificeres drastisk:
  - `set_entry_remaining()`: drop GET /users/{guid} og GET /users/{guid}/groups.
    Card, Name og Groups kendes allerede fra trin 2. Kun 1 PUT per bruger.
  - `add_user_to_group()`: erstattes af `create_user()` (POST /users med Groups=[GROUP_ID]).
    Brugeren oprettes direkte med korrekt gruppe og EntryRemaining=1.
  - `remove_user_from_group()`: simplificeres til 1 DELETE-kald.
    Drop fallback-stien med GET+GET+PUT.
  - `_get_user_groups()`: slettes helt.
  - `_verify_entry_remaining()`: beholdes — 1 GET der verificerer alt.

- `main.py` — ny pipeline-struktur med 7 trin (se ovenfor).
  Drop `run_script_with_args()` da vi ikke laengere kalder argparse-scripts.

- `build_members_csv.py` — beholdes uaendret (trin 2).

- `rasmus_liste_til_csv.py` — beholdes uaendret (trin 1).

### Nyt diff-trin (trin 3) — ren lokal logik

```python
# Pseudo-kode for det nye diff-trin
rasmus_cards = set(laes rasmus-liste.csv["Card"])
group = {row["Card"]: row for row in laes group_members.csv}
group_cards = set(group.keys())

to_update = [
    {"uid": group[c]["UserID"], "card": c, "name": group[c]["Name"]}
    for c in (group_cards & rasmus_cards)
    if group[c]["EntryRemaining"] != "1"
]

to_delete = [
    {"uid": group[c]["UserID"], "card": c}
    for c in (group_cards - rasmus_cards)
]

to_create = [
    {"card": c, "name": rasmus[c].get("Name", c)}
    for c in (rasmus_cards - group_cards)
]
```

### Nyt UPDATE-trin (trin 4) — kun PUT

```python
# Card, Name, Groups kendes fra trin 2 — intet GET nodvendigt
def set_entry_remaining(uid, card, name, group_id, target="1"):
    ud = build_userdata_xml(card, name, group_id, entry_remaining=target)
    r = requests.put(f"{ACCT_BASE}/users/{uid}", data=ud, ...)
    return r.status_code in (200, 202, 204)
```

### Nyt DELETE-trin (trin 5) — kun DELETE

```python
def remove_from_group(uid):
    r = requests.delete(f"{ACCT_BASE}/groups/{GROUP_ID}/users/{uid}", ...)
    return r.status_code in (200, 204, 404)  # 404 = allerede fjernet
```

### Nyt CREATE-trin (trin 6) — kun POST

```python
def create_user(card, name, group_id):
    ud = build_userdata_xml(card, name, group_id, entry_remaining="1")
    r = requests.post(f"{ACCT_BASE}/users", data=ud, ...)
    return r.status_code in (200, 201, 202, 204)
```

### Verificering (trin 7)

```python
def verify(rasmus_cards, group_id):
    # 1 GET — hent alle gruppemedlemmer
    members = fetch_group_members(group_id)
    member_cards = {m["card"] for m in members}

    # Tjek 1: Alle Rasmus-kort er i gruppen
    mangler = rasmus_cards - member_cards
    if mangler:
        log.error(f"{len(mangler)} kort mangler i gruppen efter sync")

    # Tjek 2: Ingen uventede kort i gruppen
    uventede = member_cards - rasmus_cards
    if uventede:
        log.warning(f"{len(uventede)} kort i gruppen som ikke er i Rasmus-listen")

    # Tjek 3: Alle har EntryRemaining = 1
    forkerte = [m for m in members if m["card"] in rasmus_cards and m["entry_remaining"] != "1"]
    if forkerte:
        log.error(f"{len(forkerte)} brugere har forkert EntryRemaining")

    return not mangler and not forkerte
```

## API-kald sammenligning

| Trin | Nyt flow | Gammelt flow |
|------|----------|--------------|
| Hent Rasmus | 1 GET | 1 GET |
| Hent gruppe | 1 GET | 1 GET |
| Diff | 0 | ~N x 3 GET (lookup) |
| UPDATE (N=400) | 400 PUT | 400 x (2 GET + 1 PUT) = 1200 |
| DELETE (N=5) | 5 DELETE | 5 x (1-3 kald) |
| CREATE (N=5) | 5 POST | 5 x (3 GET + 1 POST) = 20 |
| Verificer | 1 GET | 1 GET |
| **Total** | **~413** | **~1600+** |

## Risici

1. **Andre grupper**: Planen antager brugere KUN er i GROUP_ID.
   Hvis det aendres, skal PUT-kaldet igen hente brugerens grupper foerst.

2. **DELETE-endpoint**: Hvis DELETE /groups/{GROUP_ID}/users/{guid}
   returnerer 400/405, har vi ingen fallback. Test dette foerst.

3. **Fejl midt i pipeline**: Hvis trin 4 (UPDATE) fejler halvvejs,
   har nogle brugere EntryRemaining=1 og andre ikke.
   Trin 7 (verificering) fanger dette og logger det.

4. **Timeout**: Med ~413 kald a ~0.5s = ~200s. Inden for Cloud Runs 300s.
   Det gamle flow med ~1600 kald var taet paa eller over graensen.
