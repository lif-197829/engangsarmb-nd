cat > README.md << 'EOF'
# LIF / Lystrup Svømning — Engangsarmbaand Sync

Daily sync pipeline for ACCT users vs. Rasmus list (by Card):
- Export all ACCT users and current group members
- Compare to Rasmus list
- Produce JSON/CSV diffs
- Add missing users to the group, delete extras, flag zero EntryRemaining
- Save a timestamped summary report

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests pandas certifi
cp .env.example .env   # fill in credentials (do not commit .env)
