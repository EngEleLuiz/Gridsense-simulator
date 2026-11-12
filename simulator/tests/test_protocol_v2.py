"""Phase 8: protocol v2 is protocol v1 on the batched solver, on international networks only."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import derive_protocol
import run_hosting_capacity_study as study
import run_protocol
from gridsense_sim.networks import NETWORKS

V1 = ROOT / "protocol" / "protocol-v1.json"
V2 = ROOT / "protocol" / "protocol-v2.json"


def _strip(args: list[str]) -> list[str]:
    out, skip = [], 0
    for a in args:
        if skip:
            skip -= 1
        elif a in ("--qsts-backend", "--device"):
            skip = 1
        else:
            out.append(a)
    return out


def test_v2_is_the_committed_derivation_of_v1() -> None:
    raw = V1.read_bytes()
    derived = derive_protocol.derive(json.loads(raw), hashlib.sha256(raw).hexdigest(), "protocol-v2", "auto")
    assert derived == json.loads(V2.read_text(encoding="utf-8")), "re-run scripts/derive_protocol.py"
    assert derived["derived_from"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert derived["output_dir"] == "data/protocol-v2"          # v1 results are never overwritten


def test_v2_asks_the_same_questions_as_v1() -> None:
    v1, v2 = json.loads(V1.read_text(encoding="utf-8")), json.loads(V2.read_text(encoding="utf-8"))
    assert [r["name"] for r in v1["runs"]] == [r["name"] for r in v2["runs"]]
    assert v1["fixed_parameters"] == v2["fixed_parameters"]
    for a, b in zip(v1["runs"], v2["runs"]):
        assert a["args"] == _strip(b["args"])
        duration = derive_protocol.uses_duration_qsts(a["args"])
        assert ("--qsts-backend" in b["args"]) == duration
        assert b["backend"] == ("torch" if duration else "pandapower")
    assert sum(r["backend"] == "torch" for r in v2["runs"]) == 19


def test_every_v2_run_passes_the_runner_validation() -> None:
    protocol, _ = run_protocol.load_protocol(V2)
    for r in protocol["runs"]:
        args = [*r["args"], "--output-dir", "x", "--run-label", f"{protocol['id']}/{r['name']}"]
        if any(a.endswith(".parquet") for a in args):   # file existence is checked at run time
            continue
        study.parse_args(args)


def test_frozen_protocols_use_only_the_built_in_international_panel() -> None:
    """The paper's protocols must not depend on local (e.g. Brazilian BDGD) networks."""
    for path in (V1, V2):
        for r in json.loads(path.read_text(encoding="utf-8"))["runs"]:
            args = r["args"]
            assert "--network-file" not in args, f"{path.name}: {r['name']} uses a local network file"
            key = args[args.index("--network") + 1]
            info = NETWORKS[key]
            assert info.source == "builtin" and info.country != "Brazil"


def test_methods_parser_and_duration_detection() -> None:
    assert derive_protocol.uses_duration_qsts(["--network", "x", "--qsts-criterion", "en50160_weekly"])
    assert not derive_protocol.uses_duration_qsts(["--network", "x", "--methods", "qsts"])
    assert not derive_protocol.uses_duration_qsts(
        ["--methods", "deterministic", "--qsts-criterion", "prodist_m8_drp_drc"])
    assert not derive_protocol.uses_duration_qsts(["--qsts-criterion", "zero_tolerance"])


def test_derive_cli_writes_a_new_file_and_leaves_the_source_alone(tmp_path) -> None:
    before = V1.read_bytes()
    out = tmp_path / "p.json"
    assert derive_protocol.main(["--source", str(V1), "--id", "protocol-test", "--out", str(out),
                                 "--device", "cpu"]) == 0
    derived = json.loads(out.read_text(encoding="utf-8"))
    assert derived["id"] == "protocol-test" and V1.read_bytes() == before
    torch_runs = [r for r in derived["runs"] if r["backend"] == "torch"]
    assert all(r["args"][-2:] == ["--device", "cpu"] for r in torch_runs)


def _write(directory: Path, rows: list[tuple[str, str, dict, str]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([
        {"network": "n", "method": method, "run_id": f"id{i}", "run_timestamp": stamp,
         "raw_value": json.dumps({"status": "ok", "params": {"run_label": label}, **payload})}
        for i, (label, method, payload, stamp) in enumerate(rows)])
    pq.write_table(table, directory / "part.parquet")


def test_compare_protocols(tmp_path, capsys) -> None:
    import compare_protocols

    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, [
        ("protocol-v1/D-x", "qsts", {"lambda_max": 2.000, "bounded": True}, "2026-01-01"),
        ("protocol-v1/D-y", "qsts", {"lambda_max": 3.000, "bounded": True}, "2026-01-01"),
        ("protocol-v1/D-z", "qsts", {"lambda_max": 8.0, "bounded": False}, "2026-01-01"),
        ("protocol-v1/A-x", "deterministic", {"lambda_max": 1.5, "bounded": True}, "2026-01-01"),
        ("protocol-v1/only-a", "qsts", {"lambda_max": 1.0, "bounded": True}, "2026-01-01"),
    ])
    _write(b, [
        ("protocol-v2/D-x", "qsts", {"lambda_max": 1.0, "bounded": True, "backend": "torch"}, "2026-01-01"),   # stale
        ("protocol-v2/D-x", "qsts", {"lambda_max": 2.004, "bounded": True, "backend": "torch"}, "2026-02-01"),
        ("protocol-v2/D-y", "qsts", {"lambda_max": 3.000, "bounded": True, "backend": "torch"}, "2026-01-01"),
        ("protocol-v2/D-z", "qsts", {"lambda_max": 16.0, "bounded": False, "backend": "torch"}, "2026-01-01"),
        ("protocol-v2/A-x", "deterministic", {"lambda_max": 1.5, "bounded": True}, "2026-01-01"),
    ])
    assert compare_protocols.main([str(a), str(b)]) == 0          # latest row used; censored caps not compared
    out = capsys.readouterr().out
    assert "4 pair(s) compared, 0 outside" in out and "only in a: only-a" in out
    assert compare_protocols.main([str(a), str(b), "--tolerance", "0.001"]) == 1
    assert "DIFFERENT" in capsys.readouterr().out
    assert compare_protocols.main([str(a), str(b), "--only-backend-changes"]) == 0
    assert "3 pair(s) compared" in capsys.readouterr().out
