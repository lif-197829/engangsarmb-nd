#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_sync.sh                # normal run (no user creation)
#   ./run_sync.sh --create-missing  # also creates missing users before applying adds/deletes

CREATE_MISSING=false
if [[ "${1:-}" == "--create-missing" ]]; then
  CREATE_MISSING=true
fi

# ----- 0) env -----
if [ -f ".env" ]; then
  set -a; source .env; set +a
else
  echo "⚠️  .env not found — using environment variables from shell"
fi

# sanity: jq needed for counting JSON items (brew install jq)
if ! command -v jq >/dev/null 2>&1; then
  echo "❌ jq is required (macOS: brew install jq)."
  exit 1
fi

echo "=== LIF / Lystrup Svømning — Daily Sync & Report ==="

# ----- 1) Download Rasmus list (→ rasmus-liste.csv) -----
echo "1) Henter rasmus-liste.csv …"
python3 rasmus-liste-til_csv.py

# ----- 2) Export all ACCT users (→ all_users.csv) -----
echo "2) Henter all_users.csv …"
python3 find_users.py

# ----- 3) Export current group members (→ group_members.csv) -----
echo "3) Henter group_members.csv …"
python3 build_members_csv.py

# ----- 4) Compute diffs (→ to_add.json / to_delete.json / to_update.json / missing_cards.json) -----
echo "4) Danner diff-filer …"
python3 member_rasmus_diff.py

# Show counts
ADD_COUNT=$(jq '.to_add | length' to_add.json 2>/dev/null || echo 0)
DEL_COUNT=$(jq '.to_delete | length' to_delete.json 2>/dev/null || echo 0)
UPD_COUNT=$(jq '.to_update | length' to_update.json 2>/dev/null || echo 0)
MISS_COUNT=$( [ -f missing_cards.json ] && jq 'length' missing_cards.json || echo 0 )

echo "---- Før evt. oprettelse ----"
echo "To ADD:    ${ADD_COUNT}"
echo "To DELETE: ${DEL_COUNT}"
echo "To UPDATE: ${UPD_COUNT}"
echo "Missing in system (from Rasmus): ${MISS_COUNT}"

# ----- 5) (Optional) Create missing users, then refresh exports & diffs -----
if $CREATE_MISSING; then
  if [ -f "missing_cards.json" ] && [ "$(jq 'length' missing_cards.json)" -gt 0 ]; then
    echo "5) Opretter manglende brugere (fra rasmus-liste.csv som ikke er i all_users.csv) …"
    # You can adjust --name-col/--pid-col if your CSV has different columns
    python3 create_missing_users.py rasmus-liste.csv all_users.csv --card-col "Card" --name-col "Name"

    echo "   ↻ Refresher all_users.csv / group_members.csv / diffs efter oprettelser …"
    python3 find_users.py
    python3 build_members_csv.py
    python3 member_rasmus_diff.py

    # Recompute counts
    ADD_COUNT=$(jq '.to_add | length' to_add.json 2>/dev/null || echo 0)
    DEL_COUNT=$(jq '.to_delete | length' to_delete.json 2>/dev/null || echo 0)
    UPD_COUNT=$(jq '.to_update | length' to_update.json 2>/dev/null || echo 0)
    MISS_COUNT=$( [ -f missing_cards.json ] && jq 'length' missing_cards.json || echo 0 )

    echo "---- Efter oprettelse ----"
    echo "To ADD:    ${ADD_COUNT}"
    echo "To DELETE: ${DEL_COUNT}"
    echo "To UPDATE: ${UPD_COUNT}"
    echo "Missing in system (from Rasmus): ${MISS_COUNT}"
  else
    echo "5) Ingen manglende kort at oprette (missing_cards.json er tom eller findes ikke)."
  fi
fi

# ----- 6) Apply changes (add/remove users) -----
echo "6) Anvender ændringer (ADD/DELETE) …"
python3 changing_state_of_group.py to_add.json to_delete.json

# ----- 7) Write summary report -----
ts=$(date +'%Y-%m-%d_%H-%M-%S')
report_dir="reports"
mkdir -p "$report_dir"

add_count=$(jq '.to_add | length' to_add.json 2>/dev/null || echo 0)
del_count=$(jq '.to_delete | length' to_delete.json 2>/dev/null || echo 0)
upd_count=$(jq '.to_update | length' to_update.json 2>/dev/null || echo 0)
miss_count=$( [ -f missing_cards.json ] && jq 'length' missing_cards.json || echo 0 )

report_csv="$report_dir/report_$ts.csv"
{
  echo "timestamp,adds,deleted,updates,missing_cards"
  echo "$ts,$add_count,$del_count,$upd_count,$miss_count"
} > "$report_csv"

echo "✅ Rapport gemt: $report_csv"
