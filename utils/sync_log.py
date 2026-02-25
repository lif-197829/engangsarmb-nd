# utils/sync_log.py
import os
import datetime


class SyncLog:
    """
    Logger der skriver sync-handlinger til et Google Sheet.
    Hver maaned faar sit eget ark (fane) i regnearket.

    Brug:
        log = SyncLog()
        log.log("ADD", "12345", "Tilfojet til gruppe")
        log.log("UPDATE", "12345", "EntryRemaining sat til 1")
        log.log("DELETE", "67890", "Fjernet fra gruppe")
        log.flush()
    """

    HEADERS = ["Tidspunkt", "Handling", "Armbaand", "Miljoe", "Detalje"]

    def __init__(self, env_name=None, sheet_id=None):
        self.env_name = env_name or os.getenv("ENV_NAME", "ukendt")
        self.sheet_id = sheet_id or os.getenv("LOG_SHEET_ID", "")
        self._entries = []
        self._service = None

    def log(self, action, card, detail=""):
        self._entries.append({
            "tidspunkt": datetime.datetime.now().isoformat(timespec="seconds"),
            "handling": action,
            "armbaand": card,
            "miljoe": self.env_name,
            "detalje": detail,
        })

    def flush(self):
        if not self._entries:
            return

        if not self.sheet_id:
            print(f"SyncLog: LOG_SHEET_ID mangler — {len(self._entries)} entries printet til stdout:")
            for e in self._entries:
                print(f"  {e['tidspunkt']}  {e['handling']:<8} {e['armbaand']}  [{e['miljoe']}]  {e['detalje']}")
            self._entries.clear()
            return

        try:
            self._flush_to_sheet()
        except Exception as e:
            print(f"SyncLog: Fejl ved skrivning til Google Sheet: {e}")
            print(f"SyncLog: {len(self._entries)} entries tabt — printer til stdout som fallback:")
            for entry in self._entries:
                print(f"  {entry['tidspunkt']}  {entry['handling']:<8} {entry['armbaand']}  [{entry['miljoe']}]  {entry['detalje']}")
        finally:
            self._entries.clear()

    def _flush_to_sheet(self):
        service = self._get_service()

        # Grupper entries efter maaned — hver maaned faar sit eget faneblad i regnearket
        by_month = {}
        for entry in self._entries:
            month = entry["tidspunkt"][:7]
            by_month.setdefault(month, []).append(entry)

        existing_tabs = self._get_existing_tabs(service)

        for month, entries in by_month.items():
            if month not in existing_tabs:
                self._create_tab(service, month)

            rows = [
                [e["tidspunkt"], e["handling"], e["armbaand"], e["miljoe"], e["detalje"]]
                for e in entries
            ]
            service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{month}!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()

        print(f"SyncLog: {len(self._entries)} entries skrevet til Google Sheet")

    def _get_service(self):
        # Lazy import: undgaar afhaengighed af google-biblioteker naar LOG_SHEET_ID ikke er sat
        if self._service is None:
            import google.auth
            from googleapiclient.discovery import build

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def _get_existing_tabs(self, service):
        spreadsheet = service.spreadsheets().get(
            spreadsheetId=self.sheet_id
        ).execute()
        return {s["properties"]["title"] for s in spreadsheet["sheets"]}

    def _create_tab(self, service, tab_name):
        service.spreadsheets().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        # Tilfoej header-raekke
        service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{tab_name}!A1",
            valueInputOption="RAW",
            body={"values": [self.HEADERS]},
        ).execute()
