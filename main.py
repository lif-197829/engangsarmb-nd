import os
import sys
import time
import json
import datetime
import logging
from pathlib import Path
from google.cloud import storage

# --- IMPORTER DINE EKSISTERENDE SCRIPTS ---
import rasmus_liste_til_csv
import build_members_csv
import member_rasmus_diff
import create_missing_users
import changing_state_of_group

# --- KONFIGURATION ---
BUCKET_NAME = os.getenv("BUCKET_NAME")

# Udled env-navn fra ACCT_BASE URL (fx "test.acct.dk" -> "test.acct.dk")
ACCT_BASE = os.getenv("ACCT_BASE", "")
ENV_NAME = ACCT_BASE.replace("https://", "").replace("http://", "").split("/")[0] or "ukendt"

# Logging konfigureres paa modul-niveau saa alle trin bruger samme format.
# ENV_NAME indbages ved import-tid — det er acceptabelt da ACCT_BASE er statisk per Cloud Run revision.
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [%(levelname)s] [{ENV_NAME}] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sync")


def _format_elapsed(start: float) -> str:
    """Returnerer forloeben tid siden start som laesbar streng, fx '1.4s'."""
    return f"{time.time() - start:.1f}s"


def _file_summary(filnavn: str) -> str:
    """Laes en JSON-fil og returner et kort resume af indholdet."""
    sti = Path(filnavn)
    if not sti.exists():
        return f"{filnavn} ikke fundet"
    try:
        data = json.loads(sti.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            parts = [f"{k}={len(v)}" for k, v in data.items() if isinstance(v, list)]
            return ", ".join(parts) if parts else f"{len(data)} noegler"
        if isinstance(data, list):
            return f"{len(data)} elementer"
    except Exception:
        logger.warning(f"Kunne ikke parse {filnavn} som JSON")
    return "ulaeselig"


def upload_files_to_bucket(file_list: list[str]) -> int:
    """Uploader eksisterende filer fra arbejdsmappen til Google Cloud Storage.
    Returnerer antal filer der faktisk blev uploaded."""
    if not BUCKET_NAME:
        logger.warning("Upload springes over: BUCKET_NAME env var mangler")
        return 0

    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")

    logger.info(f"Uploader artefakter til gs://{BUCKET_NAME}/logs/{timestamp}/")

    uploaded = 0
    for filename in file_list:
        if os.path.exists(filename):
            blob = bucket.blob(f"logs/{timestamp}/{filename}")
            blob.upload_from_filename(filename)
            uploaded += 1
            logger.info(f"  Uploaded {filename}")
        else:
            logger.debug(f"  {filename} findes ikke, springes over")
    return uploaded


def run_script_with_args(script_module, args):
    """Kalder script_module.main() med simuleret sys.argv.
    Argparse-scripts kalder sys.exit(0) ved succes — det fanges herunder."""
    original_argv = sys.argv
    try:
        sys.argv = ["script_name"] + args
        script_module.main()
    except SystemExit as e:
        # argparse kalder sys.exit(0) ved succes — kun != 0 er uventet
        if e.code != 0:
            logger.warning(f"{script_module.__name__} exit code {e.code}")
    except Exception as e:
        logger.error(f"Fejl i {script_module.__name__}: {e}")
        raise
    finally:
        sys.argv = original_argv


def entry_point(request):
    """Cloud Run entry point — kaldt af Google Scheduler"""
    pipeline_start = time.time()
    try:
        logger.info("=" * 60)
        logger.info(f"Pipeline startet — env={ENV_NAME}")
        logger.info("=" * 60)

        # 0) Skift til /tmp (Cloud Run kan kun skrive her)
        os.chdir("/tmp")
        logger.info(f"Working directory: {os.getcwd()}")

        # --- TRIN 1: Hent Rasmus-listen (Google Sheet -> rasmus-liste.csv) ---
        logger.info("TRIN 1/6: Henter rasmus-liste fra Google Sheet")
        trin_start = time.time()
        rasmus_liste_til_csv.main()
        logger.info(f"TRIN 1/6 faerdig ({_format_elapsed(trin_start)})")

        # --- TRIN 2: Hent gruppemedlemmer fra ACCT (-> group_members.csv) ---
        logger.info("TRIN 2/6: Henter gruppemedlemmer fra ACCT API")
        trin_start = time.time()
        build_members_csv.main()
        logger.info(f"TRIN 2/6 faerdig ({_format_elapsed(trin_start)})")

        # --- TRIN 3: Beregn diff (-> to_add/to_delete/to_update/missing_cards) ---
        logger.info("TRIN 3/6: Beregner diff mellem Rasmus-liste og gruppemedlemmer")
        trin_start = time.time()
        member_rasmus_diff.main()
        logger.info(f"TRIN 3/6 faerdig ({_format_elapsed(trin_start)})")

        # Log diff-resultater
        for json_fil in ["to_add.json", "to_delete.json", "to_update.json", "missing_cards.json"]:
            logger.info(f"  {json_fil}: {_file_summary(json_fil)}")

        # --- TRIN 4: Opret manglende brugere ---
        logger.info("TRIN 4/6: Opretter manglende brugere")
        trin_start = time.time()
        run_script_with_args(create_missing_users, [
            "rasmus-liste.csv",
            "--card-col", "Card"
        ])
        logger.info(f"TRIN 4/6 faerdig ({_format_elapsed(trin_start)})")

        # --- TRIN 5: Udfoer aendringer (ADD/DELETE/UPDATE) ---
        logger.info("TRIN 5/6: Udfoerer aendringer i ACCT (add/delete/update)")
        trin_start = time.time()
        changing_state_of_group.main()
        logger.info(f"TRIN 5/6 faerdig ({_format_elapsed(trin_start)})")

        # --- TRIN 6: Upload artefakter til GCS ---
        logger.info("TRIN 6/6: Uploader artefakter til Cloud Storage")
        trin_start = time.time()
        files_to_save = [
            "rasmus-liste.csv",
            "group_members.csv",
            "to_add.json",
            "to_delete.json",
            "to_update.json",
            "add_errors.json",
            "delete_errors.json",
            "create_user_errors.json",
            "update_errors.json",
            "missing_cards.json",
            "verify_errors.json",
        ]
        uploaded = upload_files_to_bucket(files_to_save)
        logger.info(f"TRIN 6/6 faerdig — {uploaded} filer uploaded ({_format_elapsed(trin_start)})")

        total = _format_elapsed(pipeline_start)
        logger.info("=" * 60)
        logger.info(f"Pipeline faerdig — samlet tid: {total}")
        logger.info("=" * 60)

        return "Sync Success", 200

    except Exception as e:
        total = _format_elapsed(pipeline_start)
        logger.error(f"KRITISK FEJL efter {total}: {e}", exc_info=True)
        return f"Error: {str(e)}", 500
