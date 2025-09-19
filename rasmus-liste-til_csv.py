import io
import pandas as pd
import requests
import certifi

FILE_ID = "1pT6J-H4mCcCi7_kQeoUvpoepayKcndbx"
# If you need a specific sheet, append &gid=YOUR_GID
url = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=csv"

r = requests.get(url, timeout=30, verify=certifi.where())  # <- trusted CA bundle
r.raise_for_status()

df = pd.read_csv(io.StringIO(r.text))
df.to_csv("rasmus-liste.csv", index=False, encoding="utf-8")

print("✅ Downloaded and saved as rasmus-liste.csv")
