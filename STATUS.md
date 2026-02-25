# Engangsarmbånd Sync — Status & Overblik

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
- **`tests/`** — 4 testfiler med pytest

---

## Mangler (ikke implementeret endnu)

### 1. Logging til Google Drive

For hver ændring skal der skrives til en logfil i Google Drive. Nuværende logging er kun:
- Lokalt: `logs/` mappe med timestampede filer (via `run_sync`)
- Cloud: Upload til GCS bucket (via `main.py`)

**Skal laves:**
- Integration med Google Drive API (eller Google Sheets API)
- Skriv en linje pr. ændring (add/delete/update) med armbånd-ID, handling, tidsstempel

### 2. `run_sync` er ude af sync med `main.py` (kun udvikling)

`run_sync` antages kun brugt til lokal udvikling. Det kalder stadig `find_users.py` og sender forkerte argumenter til `create_missing_users.py`. Hvis det skal vedligeholdes, bør det opdateres til at matche `main.py`. Ellers kan det markeres som deprecated.

---

## Kundens problem: Armbånd der ikke nulstilles

Kunden rapporterer at nogle armbånd ikke får `EntryRemaining` sat til `1` efter natlig kørsel — på indgang, udgang eller begge. Følgende logiske huller kan forklare dette:

### Problem A: Unødvendig verify-logik kan forårsage aktiv skade (kritisk)

`set_entry_remaining()` (linje 397-521) har en fallback-kæde med 3 faser der hver laver et PUT-kald efterfulgt af et GET-kald for at "verificere" resultatet — i alt op til **6 API-kald** per bruger i worst case:

1. **Phase A:** PUT `<EntryRemaining>1</EntryRemaining>` → sleep 0.4s → GET verify
2. **Phase B:** Identisk PUT → sleep 0.4s → GET verify
3. **Phase C:** PUT **UDEN** `<EntryRemaining>` → sleep 0.4s → GET verify

Verify skal fejle 3 gange i træk (pga. netværk/timeouts) før Phase C nås. Det er usandsynligt for én bruger, men over mange brugere og mange nætter kan det ske. Når Phase C trigges, PUT'es brugerdata **uden EntryRemaining**, hvilket potentielt fjerner feltet. Og Phase C's verify kan aldrig lykkes (den checker om tekst == "1" for et fjernet element), så resultatet er altid `"persist_failed"`.

Det ligner test/debug-kode fra udviklingen, hvor man ikke stolede på serverens svar.

**Anbefalet redesign:**

1. **Ét PUT-kald per bruger** — byg `<EntryRemaining>1</EntryRemaining>`, PUT det, tjek HTTP-statuskode. 200 = success. Færdigt for den bruger.
2. **Samlet verifikation til sidst** — når alle brugere er opdateret, hent alle gruppemedlemmer med ét GET-kald og tjek at `EntryRemaining == 1` for dem alle. Log evt. afvigelser.

Denne tilgang er simplere, bruger færre API-kald, og har ingen risiko for at Phase C ødelægger data. Den samlede verifikation giver også et klart billede af om der faktisk er problemer med serveren.

Samme unødvendige verify-mønster ses i `add_user_to_group()` (linje 249-257) — et "re-check membership" GET-kald efter succesfuld PUT. Bør også fjernes.

### Problem B: Unødige API-kald skal fjernes

Når verify-logikken fjernes (Problem A), forsvinder de redundante kald. Men generelt: alle steder i koden hvor der laves et GET-kald bare for at bekræfte et succesfuldt PUT (200-svar) bør fjernes. Det gælder også `add_user_to_group()` linje 249-257 (re-check membership efter PUT).

### Problem C: Engangsarmbånd i andre grupper bør give advarsel

`add_user_to_group()` henter allerede brugerens eksisterende grupper (linje 173-176) men logger intet hvis brugeren er i andre grupper end den forventede. Engangsarmbånd bør **ikke** være i andre grupper — det tyder på fejl eller misbrug.

**Anbefalet fix:** Når `current_groups` indeholder andre grupper end `GROUP_ID`, skriv en tydelig advarsel i loggen, f.eks.: `"ADVARSEL: Engangsarmbånd {card} er i {len(current_groups)} andre grupper: {groups}. Engangsarmbånd bør kun være i én gruppe."`

### Note D: Tomt `<EntryRemaining/>`-element ved edge case

I `add_user_to_group()` og `remove_user_from_group()` kan der i teorien sendes et tomt `<EntryRemaining/>` element, hvis serverens GET-svar hverken har `nil="true"` eller en talværdi. Det er usandsynligt at serveren returnerer dette, men som defensiv kodning kunne man tilføje en fallback (f.eks. sætte `"1"` som default).

### Problem E: Error-filer kan overskrive hinanden + manglende miljønavn

Scriptet kører som Google Cloud Function, og de to kørsler (indgang/udgang) skriver til `/tmp` og uploader til GCS bucket med minut-opløsning i timestamp (`logs/{timestamp}/`). Hvis de to kørsler rammer samme minut, overskriver de hinandens filer i bucket'en. Desuden er der intet i filnavnene der indikerer hvilket miljø (indgang/udgang) de tilhører.

**Anbefalet fix:** Tilføj en env var (f.eks. `ENV_NAME=indgang` / `ENV_NAME=udgang`) og prepend den til filnavne, f.eks. `indgang_update_errors.json`, `indgang_to_add.json` osv. Det gør det muligt at skelne mellem de to kørsler både i `/tmp`, i GCS bucket, og i logs.

Derudover returnerer `changing_state_of_group.py` altid exit code `0` uanset hvor mange individuelle operationer fejler. Fejl skrives til error-filer, men ingen tjekker dem automatisk.

### Note F: Oprydning i `main.py`

`main.py` har korrekt fjernet kaldet til `find_users.py`, og `create_missing_users.py` kaldes med de rigtige argumenter. Men der er en forældet kommentar (linje 81) og `all_users.csv` står stadig i upload-listen (linje 96) — den skippes bare fordi filen ikke findes. Ren oprydning, ingen funktionel påvirkning.

---

## Kodekvalitet og oprydning

### Inkonsistent `EntryRemaining`-fortolkning (virker, men er rodet)

Tre scripts fortolker `EntryRemaining` med `i:nil="true"` forskelligt:

| Script | `nil="true"` fortolkes som |
|--------|---------------------------|
| `find_users.py` | `"1"` |
| `build_members_csv.py` | `"nil"` |
| `changing_state_of_group.py` | Bevarer nil-attributten |

**Det virker** — fordi diff-logikken checker `entry != "1"`, og `"nil"` er korrekt ikke lig `"1"`. Men det er tilfældigt korrekt snarere end bevidst design. En fælles hjælpefunktion i `utils/` ville gøre intentionen tydelig.

### Duplikeret kode mellem scripts

Følgende funktioner er copy-pasted mellem `member_rasmus_diff.py` og `create_missing_users.py`:
- `load_cache()` / `save_cache()`
- `parse_users_from_xml()`
- `lookup_userid_by_card()`
- `_find_first_text()` / `_local()`

**Fix:** Flyt til `utils/` modulet.

### `.env` og datafiler i git

- `.env` (med credentials) er i repo'et trods `.gitignore`
- CSV- og JSON-filer er committet trods ignore-regler

**Fix:** `git rm --cached .env all_users.csv group_members.csv rasmus-liste.csv *.json`

### `find_users.py` er ubrugt i produktion

`main.py` kalder ikke `find_users.py`. Kun `run_sync` (lokal udvikling) bruger det. Kan evt. fjernes fra repo'et eller markeres som deprecated.

---

## Prioriteret opgaveliste

| # | Opgave | Prioritet | Kompleksitet |
|---|--------|-----------|-------------|
| 1 | Redesign `set_entry_remaining()`: ét PUT per bruger, stol på 200, samlet verifikation til sidst (problem A+B) | Kritisk | Medium |
| 2 | Tilføj `ENV_NAME` til filnavne så indgang/udgang kan skelnes (problem E) | Høj | Lav |
| 3 | Log advarsel når engangsarmbånd er i andre grupper (problem C) | Høj | Lav |
| 4 | Implementer Google Drive logging (mangle #1) | Høj | Medium |
| 5 | Fjern unødige verify-GET-kald i `add_user_to_group()` (problem B) | Medium | Lav |
| 6 | Saml duplikeret kode i `utils/` | Lav | Lav |
| 7 | Saml EntryRemaining-fortolkning i fælles hjælpefunktion | Lav | Lav |
| 8 | Fjern `.env` og datafiler fra git | Lav | Lav |
| 9 | Opdater eller deprecer `run_sync` | Lav | Lav |
