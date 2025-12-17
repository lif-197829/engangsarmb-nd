# rasmus-liste-til_csv.py
import io
import os
import pandas as pd
import requests
import certifi

FILE_ID   = os.getenv("RASMUS_SHEET_FILE_ID", "1pT6J-H4mCcCi7_kQeoUvpoepayKcndbx")
SHEET_GID = os.getenv("RASMUS_SHEET_GID", "")  # tom = første ark
qs = "export?format=csv" + (f"&gid={SHEET_GID}" if SHEET_GID else "")
url = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/{qs}"

def main():
    r = requests.get(url, timeout=30, verify=certifi.where())
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.to_csv("rasmus-liste.csv", index=False, encoding="utf-8")
    print("Downloaded og gemt som rasmus-liste.csv")

if __name__ == "__main__":
    main()
