# tests/test_member_rasmus_diff.py
import os
import csv
import json
import importlib
from pathlib import Path

def _write_csv(path: Path, rows: list[list[str]]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)

def test_diff_outputs(tmp_path, monkeypatch):
    # Files the script forventer i cwd
    cwd = tmp_path
    os.chdir(cwd)

    # group_members.csv – Card,Name,UserID,EntryRemaining
    _write_csv(cwd/"group_members.csv", [
        ["Card","Name","UserID","EntryRemaining"],
        ["A","A","uid_A","1"],
        ["B","B","uid_B","0"],   # skal ende i to_update (0)
        ["C","C","uid_C","1"],
    ])

    # all_users.csv – Card,Name,UserID,EntryRemaining (map Card->UserID)
    _write_csv(cwd/"all_users.csv", [
        ["Card","Name","UserID","EntryRemaining"],
        ["A","A","uid_A","1"],
        ["B","B","uid_B","1"],
        ["C","C","uid_C","1"],
        ["D","D","uid_D","1"],
    ])

    # rasmus-liste.csv – Card
    _write_csv(cwd/"rasmus-liste.csv", [
        ["Card"],
        ["A"],  # i gruppe (behold)
        ["B"],  # i gruppe (behold) men entry=0 -> to_update
        ["D"],  # ikke i gruppe -> to_add
    ])

    import member_rasmus_diff as mod
    importlib.reload(mod)

    mod.main()

    add = json.loads((cwd/"to_add.json").read_text(encoding="utf-8"))
    delete = json.loads((cwd/"to_delete.json").read_text(encoding="utf-8"))
    update = json.loads((cwd/"to_update.json").read_text(encoding="utf-8"))

    # to_add: D
    assert add["to_add"] == ["uid_D"]
    # to_delete: C (var i gruppe, men ikke i rasmus)
    assert delete["to_delete"] == ["uid_C"]
    # to_update: B (i gruppe og EntryRemaining == "0")
    assert update["to_update"] == ["uid_B"]
