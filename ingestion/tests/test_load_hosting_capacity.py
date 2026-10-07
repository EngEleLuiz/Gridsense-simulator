"""Unit tests for the hosting-capacity loader (review finding R13).

psycopg2 is replaced by a fake connection that records what the loader
sends, so these tests need no database. The real database path is
covered by the CI e2e job.
"""

from __future__ import annotations

import csv
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq

import load_hosting_capacity_to_timescale as loader

SCHEMA = pa.schema(
    [(c, pa.string()) for c in ("network", "method", "run_id", "run_timestamp", "raw_value")]
)


class _Cursor:
    def __init__(self, log: dict) -> None:
        self.log = log
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str) -> None:
        self.log["sql"].append(sql)
        if "INSERT INTO bronze.hosting_capacity_results" in sql:
            self.rowcount = len(self.log["copied"][-1])

    def copy_expert(self, sql: str, buf: io.StringIO) -> None:
        self.log["copied"].append(list(csv.reader(io.StringIO(buf.getvalue()))))


class _Conn:
    def __init__(self) -> None:
        self.log = {"sql": [], "copied": [], "commits": 0, "closed": False}
        self.autocommit = True

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    def commit(self) -> None:
        self.log["commits"] += 1

    def close(self) -> None:
        self.log["closed"] = True


def _write(tmp_path, n_rows: int) -> None:
    part = tmp_path / "network=cigre_lv" / "method=deterministic"
    part.mkdir(parents=True)
    rows = [
        {
            "network": "cigre_lv",
            "method": "deterministic",
            "run_id": f"r{i}",
            "run_timestamp": "2026-10-06T00:00:00+00:00",
            "raw_value": json.dumps({"schema_version": 2, "note": 'quote " and, comma'}),
        }
        for i in range(n_rows)
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), part / "part-1.parquet")


def test_load_copies_every_row_and_upserts(tmp_path, monkeypatch) -> None:
    _write(tmp_path, 3)
    conn = _Conn()
    monkeypatch.setattr(loader.psycopg2, "connect", lambda url: conn)

    assert loader.load(tmp_path, "postgresql://x/y") == 3
    assert any("CREATE TABLE IF NOT EXISTS bronze.hosting_capacity_results" in s for s in conn.log["sql"])
    assert any("ON CONFLICT (network, method, run_id) DO NOTHING" in s for s in conn.log["sql"])
    copied = conn.log["copied"][0]
    assert [r[2] for r in copied] == ["r0", "r1", "r2"]
    assert json.loads(copied[0][4])["note"] == 'quote " and, comma'  # CSV quoting round-trips
    assert conn.log["closed"]


def test_load_batches_by_batch_size(tmp_path, monkeypatch) -> None:
    _write(tmp_path, 5)
    conn = _Conn()
    monkeypatch.setattr(loader.psycopg2, "connect", lambda url: conn)
    assert loader.load(tmp_path, "postgresql://x/y", batch_size=2) == 5
    assert [len(b) for b in conn.log["copied"]] == [2, 2, 1]


def test_empty_directory_loads_nothing_but_still_creates_the_table(tmp_path, monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(loader.psycopg2, "connect", lambda url: conn)
    assert loader.load(tmp_path, "postgresql://x/y") == 0
    assert conn.log["copied"] == []
    assert any("CREATE TABLE" in s for s in conn.log["sql"])


def test_db_url_default_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("GRIDSENSE_DB_URL", "postgresql://env/db")
    seen = {}
    monkeypatch.setattr(loader, "load", lambda d, url, batch_size: seen.setdefault("url", url) and 0)
    monkeypatch.setattr("sys.argv", ["prog"])
    loader.main()
    assert seen["url"] == "postgresql://env/db"
