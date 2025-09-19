#get_group-users.py
import requests
from requests.auth import HTTPBasicAuth

# ACCT API config
import os
ACCT_BASE = os.getenv("ACCT_BASE", "https://test.acct.dk/rest/current")
ACCT_USER = os.getenv("ACCT_USER", "")
ACCT_PASS = os.getenv("ACCT_PASS", "")
GROUP_ID  = os.getenv("GROUP_ID", "")

GROUP_ID = "e9d39db7-b38f-43db-bfe1-d9a3a8f4b177"  # din gruppe

def main():
    url = f"{ACCT_BASE}/groups/{GROUP_ID}/users"
    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(ACCT_USER, ACCT_PASS),
            headers={"Accept": "application/xml"},
            timeout=15
        )
        response.raise_for_status()
        print("✅ Response:")
        print(response.text)
    except requests.exceptions.RequestException as err:
        print("❌ Request failed:")
        print(err)

if __name__ == "__main__":
    main()
