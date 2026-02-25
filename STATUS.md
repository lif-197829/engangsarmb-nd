# Engangsarmbånd Sync — Opgaveoversigt

Hej! Dette dokument er din guide til at arbejde videre med koden. Det beskriver hvad systemet gør, hvad der virker, hvad der er problemer med, og hvad du skal fixe. Læs det hele igennem før du begynder.

---

## Forretningslogik

Lystrup Svømmehal sælger engangsadgang via armbånd. Flowet er:

1. Kunde køber adgang og får et armbånd med et unikt kort-ID
2. Rasmus vedligeholder et Google Sheet med alle aktive armbånd-ID'er
3. Et natligt script synkroniserer armbåndene til **to ACCT-databaser**:
   - **Indgangsdatabasen** — styrer dørene der lukker folk ind
   - **Udgangsdatabasen** — styrer udgangsdøren
4. Hvert armbånd skal kunne scannes **1 gang i indgang** og **1 gang i udgang** (`EntryRemaining = 1`)
5. Efter brug afleveres armbåndet

### Synkronisering af to databaser

Scriptet køres **to gange** med forskellige env vars (forskellige `GROUP_ID`). Hvert kald synkroniserer mod én gruppe/database. Dermed håndterer den simple `GROUP_ID` env var begge systemer uden at koden behøver kende til flere grupper.

### Cloud-infrastruktur

Scriptet kører som en **Google Cloud Function** (`main.py`, entry point: `entry_point(request)`), trigget af **Google Cloud Scheduler**. Koden deployes fra dette repo. Miljøet er isoleret — filer skrives til `/tmp` som er tomt ved cold start. Artefakter uploades til en GCS bucket med timestamp i stien (`logs/{timestamp}/`).

### Hvad scriptet gør hver nat (pr. database)

| Handling | Beskrivelse |
|----------|-------------|
| **Nulstil brugte** | Find armbånd hvor `EntryRemaining != 1` (dvs. brugt) → sæt tilbage til `1` |
| **Tilføj nye** | Armbånd tilføjet i Rasmus' ark → opret i databasen med `EntryRemaining = 1` |
| **Fjern slettede** | Armbånd fjernet fra Rasmus' ark → fjern fra databasen |
| **Log ændringer** | For hver ændring → skriv til logfil i Google Drive |

---

## Nuværende implementation

### Pipeline-trin (rækkefølge)

| # | Script | Hvad det gør | Status |
|---|--------|-------------|--------|
| 1 | `rasmus_liste_til_csv.py` | Henter Google Sheet → `rasmus-liste.csv` | OK |
| 2 | `find_users.py` | Henter ALLE brugere fra ACCT → `all_users.csv` | Ubrugt i produktion — kun `run_sync` kalder den |
| 3 | `build_members_csv.py` | Henter gruppemedlemmer → `group_members.csv` | OK |
| 4 | `member_rasmus_diff.py` | Sammenligner lister → `to_add.json`, `to_delete.json`, `to_update.json` | OK |
| 5 | `create_missing_users.py` | Opretter nye brugere i ACCT via POST | OK |
| 6 | `changing_state_of_group.py` | Udfører ADD/DELETE/UPDATE mod ACCT API | OK |

### Orkestrering

- **`main.py`** — Google Cloud Function (HTTP trigger, upload til GCS bucket) — **produktion**
- **`run_sync`** (bash) — Antages kun brugt til lokal udvikling/test. Er ude af sync med `main.py` (se noter nedenfor).

### Hjælpefiler

- **`utils/xml_utils.py`** — Sorterer XML-children alfabetisk (ACCT API kræver det)
- **`utils/sync_log.py`** — Logger sync-handlinger til Google Sheet (ny)
- **`tests/`** — 4 testfiler med pytest

### Env vars

Se `.env.example` for alle env vars. De vigtigste:

| Variabel | Beskrivelse | Påkrævet |
|----------|-------------|----------|
| `ACCT_BASE` | ACCT API base-URL | Ja |
| `ACCT_USER` | REST API brugernavn | Ja |
| `ACCT_PASS` | REST API password | Ja |
| `GROUP_ID` | GUID for gruppen der synkroniseres | Ja |
| `LOG_SHEET_ID` | Google Sheet ID til logning | Nej — uden den printes log til stdout |
| `ENV_NAME` | `"indgang"` eller `"udgang"` — bruges i loggen | Nej — default `"ukendt"` |

For at logningen virker i produktion:
1. Opret et Google Sheet i samme Drive som Rasmus' ark
2. Del det med Cloud Function's service account (Editor-adgang)
3. Sæt `LOG_SHEET_ID` til sheet'ets ID (fra URL'en: `https://docs.google.com/spreadsheets/d/{ID}/edit`)
4. Sæt `ENV_NAME` til `"indgang"` eller `"udgang"` for hver af de to kørsler

---

## Hvad der allerede er lavet

- Google Drive logging: `utils/sync_log.py` er implementeret og integreret i `changing_state_of_group.py` og `create_missing_users.py`. Logger CREATE, ADD, UPDATE, DELETE (og fejl) med Card-nummer, miljønavn og tidsstempel til et Google Sheet med månedlige faneblade.

---

## Kundens problem: Armbånd der ikke nulstilles

Kunden rapporterer at nogle armbånd ikke får `EntryRemaining` sat til `1` efter natlig kørsel — på indgang, udgang eller begge. Følgende logiske huller kan forklare dette:

### Problem A: Unødvendig verify-logik kan forårsage aktiv skade (KRITISK — fix dette først!)

`set_entry_remaining()` i `changing_state_of_group.py` (linje 397-521) har en fallback-kæde med 3 faser der hver laver et PUT-kald efterfulgt af et GET-kald for at "verificere" resultatet — i alt op til **6 API-kald** per bruger i worst case:

1. **Phase A:** PUT `<EntryRemaining>1</EntryRemaining>` → sleep 0.4s → GET verify
2. **Phase B:** Identisk PUT → sleep 0.4s → GET verify
3. **Phase C:** PUT **UDEN** `<EntryRemaining>` → sleep 0.4s → GET verify

Verify skal fejle 3 gange i træk (pga. netværk/timeouts) før Phase C nås. Det er usandsynligt for én bruger, men over mange brugere og mange nætter kan det ske. Når Phase C trigges, PUT'es brugerdata **uden EntryRemaining**, hvilket potentielt fjerner feltet. Og Phase C's verify kan aldrig lykkes (den checker om tekst == "1" for et fjernet element), så resultatet er altid `"persist_failed"`.

Hele verify-logikken er unødvendig. Hvis PUT returnerer 200, har serveren accepteret dataen. Der er ingen grund til at lave et ekstra GET-kald for at bekræfte.

**Sådan skal du fixe det:**

1. **Fjern `_verify()`**, alle phases, og `time.sleep(0.4)` fra `set_entry_remaining()`
2. **Reducer funktionen til:** Byg XML med `<EntryRemaining>1</EntryRemaining>`, PUT det, tjek HTTP-statuskode. 200/202/204 = success. Behold fallback'en der prøver uden XML-deklaration ved 400. Færdig.
3. **Tilføj samlet verifikation til sidst i `main()`:** Efter alle brugere er opdateret, hent alle gruppemedlemmer med ét GET-kald (brug `build_members_csv.py`-logikken) og tjek at `EntryRemaining == 1` for dem alle. Log afvigelser.

Samme unødvendige verify-mønster ses i `add_user_to_group()` (linje 249-257) — et "re-check membership" GET-kald efter succesfuld PUT. Fjern det også.

### Problem B: Unødige API-kald skal fjernes

Alle steder i koden hvor der laves et GET-kald bare for at bekræfte et succesfuldt PUT (200-svar) bør fjernes. Udover det der er nævnt i Problem A, gælder det også `add_user_to_group()` linje 249-257 og det dobbelte kald til `_get_user_groups()` i linje 174-196 (samme endpoint kaldes to gange med forskellig parsing — den ene er nok).

### Problem C: Engangsarmbånd i andre grupper bør give advarsel

`add_user_to_group()` henter allerede brugerens eksisterende grupper (linje 173-176) men logger intet hvis brugeren er i andre grupper end den forventede. Engangsarmbånd bør **ikke** være i andre grupper — det tyder på fejl eller misbrug.

**Sådan:** Når `current_groups` indeholder andre grupper end `GROUP_ID`, skriv en advarsel via `SyncLog`, f.eks.: `log.log("ADVARSEL", card, f"Armbånd er i {len(other_groups)} andre grupper: {other_groups}")`

### Note D: Tomt `<EntryRemaining/>`-element ved edge case

I `add_user_to_group()` og `remove_user_from_group()` kan der i teorien sendes et tomt `<EntryRemaining/>` element, hvis serverens GET-svar hverken har `nil="true"` eller en talværdi. Det er usandsynligt, men som defensiv kodning kan du tilføje en fallback (sæt `"1"` som default).

### Problem E: Error-filer kan overskrive hinanden + manglende miljønavn

De to kørsler (indgang/udgang) uploader til GCS bucket med minut-opløsning i timestamp. Hvis de rammer samme minut, overskriver de hinanden. Desuden er der intet i filnavnene der indikerer miljø.

**Sådan:** `ENV_NAME` env var'en eksisterer allerede (bruges af `SyncLog`). Brug den til at prepende filnavne: `f"{ENV_NAME}_update_errors.json"` osv. Gør det i `changing_state_of_group.py` (error-filer) og i `main.py` (upload-listen og `os.chdir`-logikken).

### Note F: Oprydning i `main.py`

Forældet kommentar på linje 81 og `all_users.csv` i upload-listen (linje 96) — skippes bare. Ingen funktionel påvirkning, men ryd op.

---

## Kodekvalitet og oprydning

Disse er lavere prioritet, men gør koden nemmere at vedligeholde. Tag dem når du har tid.

### Inkonsistent `EntryRemaining`-fortolkning (virker, men er rodet)

Tre scripts fortolker `EntryRemaining` med `i:nil="true"` forskelligt:

| Script | `nil="true"` fortolkes som |
|--------|---------------------------|
| `find_users.py` | `"1"` |
| `build_members_csv.py` | `"nil"` |
| `changing_state_of_group.py` | Bevarer nil-attributten |

Det virker tilfældigt — fordi diff-logikken checker `entry != "1"`, og `"nil" != "1"`. Men tre scripts bør ikke have tre forskellige fortolkninger af samme felt. Lav en fælles hjælpefunktion i `utils/`.

### Duplikeret kode mellem scripts

Følgende funktioner er copy-pasted mellem `member_rasmus_diff.py` og `create_missing_users.py`:
- `load_cache()` / `save_cache()`
- `parse_users_from_xml()`
- `lookup_userid_by_card()`
- `_find_first_text()` / `_local()`

**Fix:** Flyt til `utils/` modulet og importer derfra.

### `_lname` / `lname` er defineret 4+ gange

Funktionen der stripper XML-namespace fra tag-navne er defineret som:
- `_lname()` på modulniveau i `changing_state_of_group.py` (linje 107)
- Lokal closure `lname()` inde i `add_user_to_group()`, `remove_user_from_group()` og `set_entry_remaining()`
- `_localname()` i `utils/xml_utils.py` (gør det samme!)

**Fix:** Brug `_localname` fra `xml_utils.py` overalt. Fjern alle lokale kopier.

### Død kode og misplacerede imports

- `_write_debug_xml()` i `changing_state_of_group.py` (linje 278) er defineret men aldrig kaldt. Slet den.
- `import time` i `set_entry_remaining()` (linje 407) hører til i toppen af filen.

### Enkeltbogstav-variabler

`r`, `g`, `p`, `p2`, `gg`, `ud` bruges som variabelnavne for HTTP-responses og XML-elementer i `changing_state_of_group.py`. Det gør koden svær at læse. Brug beskrivende navne: `user_response`, `put_response`, `userdata_element` osv.

### Emoji i kode

`create_missing_users.py` bruger emojis i kommentarer og print-statements (✅, ❌, 🔎 osv.). Fjern dem for konsistens.

### `.env` og datafiler i git

- `.env` (med credentials) er i repo'et trods `.gitignore`
- CSV- og JSON-filer er committet trods ignore-regler

**Fix:** `git rm --cached .env all_users.csv group_members.csv rasmus-liste.csv *.json`

### `find_users.py` er ubrugt i produktion

`main.py` kalder ikke `find_users.py`. Kun `run_sync` (lokal udvikling) bruger det. Kan fjernes eller markeres som deprecated.

### `run_sync` er ude af sync med `main.py`

`run_sync` antages kun brugt til lokal udvikling. Det kalder stadig `find_users.py` og sender forkerte argumenter til `create_missing_users.py`. Opdater det eller marker det som deprecated.

---

## Prioriteret opgaveliste

| # | Opgave | Prioritet | Kompleksitet | Reference |
|---|--------|-----------|-------------|-----------|
| 1 | Redesign `set_entry_remaining()`: fjern verify, ét PUT per bruger, samlet verifikation til sidst | Kritisk | Medium | Problem A |
| 2 | Fjern unødige verify-GET-kald i `add_user_to_group()` | Høj | Lav | Problem B |
| 3 | Tilføj `ENV_NAME` til error-filnavne og upload-stier | Høj | Lav | Problem E |
| 4 | Log advarsel når engangsarmbånd er i andre grupper | Høj | Lav | Problem C |
| 5 | Saml duplikeret kode i `utils/` (inkl. `_lname`/`_localname`) | Lav | Lav | Kodekvalitet |
| 6 | Saml EntryRemaining-fortolkning i fælles hjælpefunktion | Lav | Lav | Kodekvalitet |
| 7 | Fjern død kode (`_write_debug_xml`, `import time` i funktion) | Lav | Lav | Kodekvalitet |
| 8 | Omdøb enkeltbogstav-variabler (`r`, `g`, `p`, `ud` osv.) | Lav | Lav | Kodekvalitet |
| 9 | Fjern emojis fra kodekommentarer og print-statements | Lav | Lav | Kodekvalitet |
| 10 | Fjern `.env` og datafiler fra git | Lav | Lav | Kodekvalitet |
| 11 | Opdater eller deprecer `run_sync` | Lav | Lav | Kodekvalitet |

**Allerede done:**
- ~~Implementer Google Drive logging~~ → `utils/sync_log.py`
