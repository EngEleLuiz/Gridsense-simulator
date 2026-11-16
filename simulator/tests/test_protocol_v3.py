"""Protocol v3: the sensitivity addendum to protocol v2 (one assumption varied per run)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_sensitivity_protocol as builder  # noqa: E402
import run_hosting_capacity_study as study  # noqa: E402
import run_protocol  # noqa: E402
from gridsense_sim.networks import NETWORKS  # noqa: E402

V2 = ROOT / "protocol" / "protocol-v2.json"
V3 = ROOT / "protocol" / "protocol-v3.json"

FACTOR_FLAGS = {
    "pv_power_factor": ["--pv-power-factor"],
    "source_vm_pu": ["--source-vm-pu"],
    "block_average_min": ["--qsts-block-average"],
    "adoption_fraction": ["--mc-adoption-min", "--mc-adoption-max"],
    "n_scenarios": [],                       # changes the value of an argument the base run already has
}


def _v3() -> dict:
    return json.loads(V3.read_text(encoding="utf-8"))


def test_v3_is_the_committed_output_of_the_builder() -> None:
    raw = V2.read_bytes()
    built = builder.build(json.loads(raw), hashlib.sha256(raw).hexdigest())
    assert built == _v3(), "re-run scripts/build_sensitivity_protocol.py"
    assert built["base_protocol"] == {"id": "protocol-v2", "sha256": hashlib.sha256(raw).hexdigest(),
                                      "output_dir": "data/protocol-v2"}
    assert built["output_dir"] == "data/protocol-v3"                 # base results are never overwritten


def test_every_v3_run_passes_the_runner_validation() -> None:
    protocol, _ = run_protocol.load_protocol(V3)
    names = [r["name"] for r in protocol["runs"]]
    assert len(names) == len(set(names)) == 70
    for r in protocol["runs"]:
        args = [*r["args"], "--output-dir", "x", "--run-label", f"{protocol['id']}/{r['name']}"]
        if any(a.endswith(".parquet") for a in args):                # file existence is checked at run time
            assert r["requires"] == [a for a in args if a.endswith(".parquet")]
            continue
        study.parse_args(args)


def test_each_run_changes_one_factor_of_an_existing_base_run() -> None:
    v2, v3 = json.loads(V2.read_text(encoding="utf-8")), _v3()
    base_runs = {r["name"]: r for r in v2["runs"]}
    own = {r["name"]: r for r in v3["runs"]}
    assert not set(base_runs) & set(own)                             # no base run is repeated
    for r in v3["runs"]:
        flags = FACTOR_FLAGS[r["factor"]["name"]]
        ref = r["baseline"]
        if ref is None:                                              # the reference of a self-contained series
            assert r["factor"]["value"] == r["factor"]["base_value"]
            continue
        pool = base_runs if ref["protocol"] == "protocol-v2" else own
        base = pool[ref["run"]]
        assert ref["method"] in builder_methods(base["args"])
        if ref["protocol"] != "protocol-v2":
            continue                                                 # same args but for the factor's value
        stripped, skip = [], 0
        for a in r["args"]:
            if skip:
                skip -= 1
            elif a in flags:
                skip = 1
            else:
                stripped.append(a)
        if r["factor"]["name"] == "n_scenarios":
            i = stripped.index("--mc-n-scenarios") + 1
            assert stripped[i] == "200"
            stripped[i] = base["args"][base["args"].index("--mc-n-scenarios") + 1]
        assert stripped == base["args"], r["name"]


def builder_methods(args: list[str]) -> list[str]:
    if "--methods" not in args:
        return ["deterministic", "stochastic", "qsts"]
    i, out = args.index("--methods") + 1, []
    while i < len(args) and not args[i].startswith("--"):
        out.append(args[i])
        i += 1
    return out


def test_groups_and_levels_match_the_design() -> None:
    v3 = _v3()
    count: dict[str, int] = {}
    for r in v3["runs"]:
        count[r["group"]] = count.get(r["group"], 0) + 1
    assert count == {"G_power_factor": 17, "H_source_voltage": 34, "I_resolution": 7, "J_adoption": 12}
    london = [r for r in v3["runs"] if r["name"].startswith("I-london")]
    assert sorted(r["factor"]["value"] for r in london) == [10, 15, 30, 60]
    assert sum(r["baseline"] is None for r in london) == 1
    assert v3["fixed_parameters"]["london_reference_block_minutes"] == 10
    assert all(0.8 <= pf < 1.0 for pf in v3["fixed_parameters"]["power_factors"])


def test_v3_uses_only_the_built_in_international_panel() -> None:
    for r in _v3()["runs"]:
        assert "--network-file" not in r["args"]
        info = NETWORKS[r["args"][r["args"].index("--network") + 1]]
        assert info.source == "builtin" and info.country != "Brazil"


def test_builder_cli_writes_a_file_and_leaves_the_base_alone(tmp_path, capsys) -> None:
    before = V2.read_bytes()
    out = tmp_path / "p.json"
    assert builder.main(["--id", "protocol-test", "--out", str(out)]) == 0
    assert "70 run(s)" in capsys.readouterr().out
    built = json.loads(out.read_text(encoding="utf-8"))
    assert built["id"] == "protocol-test" and built["output_dir"] == "data/protocol-test"
    assert V2.read_bytes() == before
