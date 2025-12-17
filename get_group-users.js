#get_group-users.py
import requests
from requests.auth import HTTPBasicAuth

# ACCT API config
import os
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")
GROUP_ID  = os.getenv("GROUP_ID", "")

def main():
    url = f"{ACCT_BASE}/groups/{GROUP_ID}/users"
    try:
        r = requests.get(
            url,
            auth=HTTPBasicAuth(ACCT_USER, ACCT_PASS),
            headers={"Accept": "application/xml"},
            timeout=15
        )
        r.raise_for_status()
        print("Response:")
        print(r.text)
    except requests.exceptions.HTTPError as e:
        print(f"Request fejlede ({r.status_code})")
        print((r.text or "")[:500])
    except requests.exceptions.RequestException as e:
        print("Request fejlede:")
        print(e)

if __name__ == "__main__":
    main()

