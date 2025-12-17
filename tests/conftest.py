# tests/conftest.py
import os
import pytest

ACCT_BASE = "https://test.acct.dk/rest/current"
GROUP_ID  = "e9d39db7-b38f-43db-bfe1-d9a3a8f4b177"

@pytest.fixture(autouse=True)
def acct_env(monkeypatch):
    monkeypatch.setenv("ACCT_BASE", ACCT_BASE)
    monkeypatch.setenv("ACCT_USER", "user")
    monkeypatch.setenv("ACCT_PASS", "pass")
    monkeypatch.setenv("GROUP_ID", GROUP_ID)
    # default strategi så tests ikke sletter brugere
    monkeypatch.setenv("DELETE_STRATEGY", "group_only")
    yield

def xml_user(card: str, name: str, entry: str | None):
    # entry: "nil" -> xsi:nil="true", "0" -> <EntryRemaining>0</EntryRemaining>, None -> udelades
    ns = 'http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary'
    xsi = 'http://www.w3.org/2001/XMLSchema-instance'
    parts = [
        f'<User xmlns="{ns}" xmlns:i="{xsi}">',
        f'  <Card>{card}</Card>',
        f'  <Name>{name}</Name>',
    ]
    if entry == "nil":
        parts.append('  <EntryRemaining i:nil="true" />')
    elif entry == "0":
        parts.append('  <EntryRemaining>0</EntryRemaining>')
    parts.append('</User>')
    return "\n".join(parts)

def xml_groups_array(ids):
    # /users/{guid}/groups kan returnere ArrayOfstring
    arr = 'http://schemas.microsoft.com/2003/10/Serialization/Arrays'
    s = [f'<ArrayOfstring xmlns="{arr}">']
    for gid in ids:
        s.append(f'  <string>{gid}</string>')
    s.append('</ArrayOfstring>')
    return "\n".join(s)

def xml_groupcollection(base, gids):
    # “re-check” svar efter add/remove
    ns = 'http://schemas.datacontract.org/2004/07/AcctPublicRestCommunicationLibrary'
    rows = [f'<GroupCollection xmlns="{ns}" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">']
    for gid in gids:
        rows += [
            '  <Group>',
            f'    <GroupID>{base}/groups/{gid}</GroupID>',
            f'    <Groups>{base}/groups/{gid}/groups</Groups>',
            '  </Group>',
        ]
    rows.append('</GroupCollection>')
    return "\n".join(rows)