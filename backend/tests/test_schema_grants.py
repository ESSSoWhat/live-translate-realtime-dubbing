"""Static guard: SECURITY DEFINER functions in the schema must not stay callable by anon."""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "supabase_schema.sql"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _definer_functions(sql: str) -> set[str]:
    names = set()
    for m in re.finditer(
        r"CREATE OR REPLACE FUNCTION\s+(?:public\.)?(\w+)\s*\((.*?)\$\$",
        sql,
        re.DOTALL | re.IGNORECASE,
    ):
        if "SECURITY DEFINER" in m.group(2).upper():
            names.add(m.group(1))
    return names


def _assert_locked_down(sql: str, label: str) -> None:
    for fn in _definer_functions(sql):
        assert re.search(
            rf"REVOKE ALL ON FUNCTION\s+public\.{fn}\s*\([^)]*\)\s+FROM PUBLIC",
            sql,
            re.IGNORECASE,
        ), f"{label}: {fn} is SECURITY DEFINER but EXECUTE is not revoked from PUBLIC"
        assert re.search(
            rf"ON FUNCTION\s+public\.{fn}\s*\([^)]*\)\s+FROM %I", sql
        ) or re.search(
            rf"ON FUNCTION\s+public\.{fn}\s*\([^)]*\)\s+FROM PUBLIC,\s*anon,\s*authenticated",
            sql,
            re.IGNORECASE,
        ), f"{label}: {fn} is not revoked from anon/authenticated"


def test_schema_definer_functions_revoked() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert _definer_functions(sql), "expected SECURITY DEFINER functions in schema"
    _assert_locked_down(sql, SCHEMA.name)


def test_migration_definer_functions_revoked() -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        _assert_locked_down(path.read_text(encoding="utf-8"), path.name)
