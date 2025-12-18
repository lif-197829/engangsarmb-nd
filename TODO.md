# TODO - Engangsarmbånd Sync

## Planlagte forbedringer

### 1. Fjern unødvendig hentning af alle brugere

**Problem:** Scriptet henter alle brugere fra ACCT (`find_users.py` → `all_users.csv`) for at tjekke om et Card allerede eksisterer før oprettelse. Dette er unødvendigt.

**Løsning:** Fjern `find_users.py` fra flowet. Hvis en bruger allerede eksisterer, returnerer API'et 409 Conflict ved oprettelse - dette håndteres allerede som fejl.

**Filer der skal ændres:**
- `main.py` - fjern kald til `find_users.main()`
- `run_sync` - fjern kald til `find_users.py`
- `member_rasmus_diff.py` - fjern afhængighed af `all_users.csv`
- `create_missing_users.py` - fjern `all_users_csv` parameter

**Resultat:** Simplere flow, færre API-kald, hurtigere synkronisering.

---

### 2. Sæt EntryRemaining til 1 (én adgang)

**Problem:** Koden sætter `<EntryRemaining i:nil="true"/>` hvilket betyder ubegrænset adgang. For engangsarmbånd skal der kun være én adgang.

**Løsning:** Ændr til `<EntryRemaining>1</EntryRemaining>`.

**Filer der skal ændres:**
- `create_missing_users.py` - i `build_userdata_xml()`:
  ```python
  # Fra:
  el_er.set(ET.QName(NS_XSI, "nil"), "true")

  # Til:
  el_er.text = "1"
  ```

---

### 3. Fjern unødvendig boolean parameter for EntryRemaining

**Problem:** `build_userdata_xml()` har parameteren `set_entry_remaining_nil=True` som styrer om EntryRemaining sættes. Denne er unødvendig da EntryRemaining altid skal sættes for engangsarmbånd.

**Løsning:** Fjern parameteren og sæt altid `<EntryRemaining>1</EntryRemaining>`.

**Filer der skal ændres:**
- `create_missing_users.py` - fjern `set_entry_remaining_nil` parameter fra `build_userdata_xml()`

---

## Prioritering

1. **Høj:** EntryRemaining=1 (punkt 2 + 3) - påvirker funktionalitet
2. **Medium:** Fjern all_users hentning (punkt 1) - optimering/forenkling
