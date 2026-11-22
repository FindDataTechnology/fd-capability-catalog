"""CLI tests: the runnable entry the library lacked.

Exercises build → list → resolve through main() so the plane is provably
callable, not just importable. Uses the graph source (offline: reads text-core
verticals files) so no network/DB is needed.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from fd_capability_catalog.cli import main

VERT_DIR = pathlib.Path(__file__).resolve().parents[2] / "fd-text-core" / "fd_text_core" / "kg_lane" / "verticals"


def _skip_if_no_verticals():
    if not VERT_DIR.is_dir():
        pytest.skip("fd-text-core verticals not present")


def test_cli_build_then_list_then_resolve(tmp_path, capsys):
    _skip_if_no_verticals()
    db = str(tmp_path / "cat.db")

    rc = main(["--db", db, "build", "--source", "graph", "--verticals-dir", str(VERT_DIR)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] >= 2 and out["by_kind"].get("graph") == out["written"]

    rc = main(["--db", db, "list"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {"graph:legal_v2", "graph:gov_v3"} <= {r["id"] for r in rows}

    rc = main(["--db", db, "resolve", "--agent",
               '[{"server":"fd-text-core","tools":["kg_search_facts"]}]'])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert {"graph:legal_v2", "graph:gov_v3"} <= set(res["effective"])


def test_cli_resolve_no_grant_is_empty(tmp_path, capsys):
    _skip_if_no_verticals()
    db = str(tmp_path / "cat.db")
    main(["--db", db, "build", "--source", "graph", "--verticals-dir", str(VERT_DIR)])
    capsys.readouterr()
    rc = main(["--db", db, "resolve", "--agent",
               '[{"server":"fd-open-data-mcp","tools":["read"]}]'])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["effective"] == []


def test_cli_list_governor_narrows(tmp_path, capsys):
    _skip_if_no_verticals()
    db = str(tmp_path / "cat.db")
    main(["--db", db, "build", "--source", "graph", "--verticals-dir", str(VERT_DIR)])
    capsys.readouterr()
    # governor without the kg tools sees nothing
    rc = main(["--db", db, "list", "--governor", '[{"server":"fd-open-data-mcp","tools":["read"]}]'])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == []