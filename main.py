import os
import sys
import datetime
import logging
from google.cloud import storage

# --- IMPORTER DINE EKSISTERENDE SCRIPTS ---
# Bemærk: Vi bruger navnet med underscores her
import rasmus_liste_til_csv  
import find_users
import build_members_csv
import member_rasmus_diff
import create_missing_users
import changing_state_of_group

# --- KONFIGURATION ---
BUCKET_NAME = os.getenv("BUCKET_NAME")  # Indstilles i Cloud Function Environment vars

def upload_files_to_bucket(file_list):
    """Uploader filer fra /tmp til Google Cloud Storage for historik"""
    if not BUCKET_NAME:
        print("Skipping upload: BUCKET_NAME env var mangler.")
        return

    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    
    print(f"Uploader logs til: gs://{BUCKET_NAME}/logs/{timestamp}/")

    for filename in file_list:
        if os.path.exists(filename):
            blob = bucket.blob(f"logs/{timestamp}/{filename}")
            blob.upload_from_filename(filename)
            print(f" -> Uploaded {filename}")
        else:
            print(f" -> Fandt ikke {filename}, skipper.")

def run_script_with_args(script_module, args):
    """Snyder sys.argv så scripts med argparse tror de kaldes fra kommandolinjen"""
    original_argv = sys.argv
    try:
        sys.argv = ["script_name"] + args
        script_module.main()
    except SystemExit as e:
        # argparse kalder sys.exit(), vi fanger den så hele processen ikke dør
        if e.code != 0:
            print(f"Advarsel: {script_module.__name__} exit code {e.code}")
    except Exception as e:
        print(f"Fejl i {script_module.__name__}: {e}")
        raise e
    finally:
        sys.argv = original_argv

def entry_point(request):
    """Dette er funktionen Google kalder"""
    try:
        print("Starter synkronisering...")
        
        # 1. SKIFT TIL /tmp - Dette er tricket!
        # Cloud Functions må kun skrive i /tmp. Ved at skifte her,
        # tror alle dine scripts (open('fil.csv', 'w')), at de skriver lokalt.
        os.chdir("/tmp")
        print(f"Working directory changed to: {os.getcwd()}")

        # 2. KØR SCRIPTS I RÆKKEFØLGE
        
        # A. Hent Rasmus listen
        print("\n--- Kører: rasmus_liste_til_csv ---")
        rasmus_liste_til_csv.main()

        # B. Hent alle ACCT brugere
        print("\n--- Kører: find_users ---")
        find_users.main()

        # C. Hent nuværende gruppemedlemmer
        print("\n--- Kører: build_members_csv ---")
        build_members_csv.main()

        # D. Lav diff (sammenlign lister)
        print("\n--- Kører: member_rasmus_diff ---")
        member_rasmus_diff.main()

        # E. Opret manglende brugere
        # create_missing_users bruger argparse, så vi simulerer argumenter:
        # python create_missing_users.py rasmus-liste.csv all_users.csv --card-col Card
        print("\n--- Kører: create_missing_users ---")
        run_script_with_args(create_missing_users, [
            "rasmus-liste.csv", 
            "all_users.csv", 
            "--card-col", "Card"
        ])

        # F. Udfør ændringer (Add/Remove/Update)
        # changing_state_of_group kigger selv efter json-filerne i working dir
        print("\n--- Kører: changing_state_of_group ---")
        changing_state_of_group.main()

        # 3. UPLOAD LOGS TIL BUCKET
        files_to_save = [
            "rasmus-liste.csv",
            "all_users.csv",
            "group_members.csv",
            "to_add.json",
            "to_delete.json",
            "to_update.json",
            "add_errors.json",
            "delete_errors.json",
            "create_user_errors.json",
            "update_errors.json",
            "missing_cards.json"
        ]
        upload_files_to_bucket(files_to_save)

        return "Sync Success", 200

    except Exception as e:
        print(f"KRITISK FEJL: {str(e)}")
        # Vi returnerer fejlen, så Cloud Scheduler kan se det fejlede
        return f"Error: {str(e)}", 500