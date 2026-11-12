"""Phase 8.4: BDGD -> pandapower converter (synthetic export, no real data)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pytest

import _bdgd_fixtures as fx
from gridsense_sim.bdgd import BdgdSchema, BuildOptions, build_lv_network, list_transformers, read_tables, save_network
from gridsense_sim.bdgd.builder import BdgdBuildError
from gridsense_sim.bdgd.cli import main as bdgd_main
from gridsense_sim.bdgd.reader import BdgdReadError, inspect, to_number
from gridsense_sim.hosting_capacity import find_hosting_capacity_deterministic
from gridsense_sim.networks import NETWORKS, register_network_file, unregister_network

OPT = BuildOptions(mv_kv=13.8, lv_kv=0.38, load_factor=0.5, vk_percent=4.5)


@pytest.fixture()
def src(tmp_path) -> Path:
    return fx.write(tmp_path / "bdgd")


def test_reader_handles_separators_decimal_commas_and_string_ids(src) -> None:
    t = read_tables(src)
    assert t["UCBT_tab"]["COD_ID"].tolist()[0] == "U1"
    assert to_number(t["SEGCON"]["R1"]).tolist() == [0.60, 1.20]          # "0,60" and "1.20"
    assert to_number(t["UCBT_tab"]["ENE_01"]).iloc[0] == 900.0


def test_reader_reports_missing_layers_and_columns(tmp_path) -> None:
    d = fx.write(tmp_path / "bdgd")
    (d / "SEGCON.csv").unlink()
    with pytest.raises(BdgdReadError, match="SEGCON not found"):
        read_tables(d)
    rows = {r["layer"]: r for r in inspect(d)}
    assert not rows["SEGCON"]["found"] and rows["SSDBT"]["found"] and not rows["SSDBT"]["missing"]


def test_schema_override_for_older_releases(tmp_path) -> None:
    d = tmp_path / "old"
    d.mkdir()
    for name, df in fx.tables().items():
        df = df.rename(columns={"UNI_TR_MT": "UNI_TR_D"})
        df.to_csv(d / f"{'UNTRD' if name == 'UNTRMT' else name}.csv", sep=";", index=False)
    with pytest.raises(BdgdReadError):
        read_tables(d)
    schema = BdgdSchema(transformers="UNTRD", transformer_link="UNI_TR_D", equipment_link="UNI_TR_D")
    net, _ = build_lv_network(read_tables(d, schema), "T1", OPT, schema)
    assert len(net.load) == 5


def test_list_transformers_summarises_each_feeder(src) -> None:
    df = list_transformers(read_tables(src)).set_index("transformer_id")
    assert df.loc["T1", "consumers"] == 7 and df.loc["T2", "consumers"] == 1
    assert df.loc["T1", "lv_segments"] == 6 and df.loc["T1", "generators"] == 1
    assert df.loc["T1", "annual_mwh"] == pytest.approx((900 + 300 + 250 + 400 + 200 + 20 + 15) * 12 / 1000)


def test_build_topology_loads_and_report(src) -> None:
    net, rep = build_lv_network(read_tables(src), "T1", OPT)
    c = rep.counts
    assert c["loads"] == 5 and c["lines"] == 5                      # 4 segments + 1 service drop
    assert c["self_loop_branches_dropped"] == 1
    assert c["consumers_not_in_network"] == 2                       # U6 on node Q9, U7 on the island
    assert c["consumers_on_disconnected_segments"] == 1
    assert c["disconnected_nodes_removed"] == 2                     # the island X1 - X2
    assert (c["single_phase_loads"], c["two_phase_loads"], c["three_phase_loads"]) == (3, 1, 1)
    assert c["existing_generators"] == 1 and c["existing_pv_mw"] == pytest.approx(0.003)
    # peak = annual energy / 8760 / load factor; U3 has no curve -> option 0.5
    u3 = 250 * 12 / 8760 / 0.5 / 1000
    assert np.isclose(net.load["p_mw"], u3).any()
    # U1 uses the typical curve's load factor (not 0.5)
    lf = float(np.mean(shape := 0.35 + 0.65 * np.exp(-0.5 * ((np.arange(96) - 78) / 8.0) ** 2)) / shape.max())
    assert np.isclose(net.load["p_mw"], 900 * 12 / 8760 / lf / 1000, rtol=1e-3).any()
    assert c["load_factor_from_typical_curve"] == 3
    assert net.load["q_mvar"].iloc[0] == pytest.approx(net.load["p_mw"].iloc[0] * np.tan(np.arccos(0.92)))
    # anonymous by default
    assert net.bus["name"].isna().sum() == len(net.bus) - 1 and rep.transformer_id != "T1"
    assert any("balanced" in a for a in rep.assumptions) and any("single- or two-phase" in w for w in rep.warnings)
    pp.runpp(net)
    assert 0.9 < rep.nominal_state["vm_min_pu"] <= 1.0


def test_transformer_impedance_sources(src, tmp_path) -> None:
    net, rep = build_lv_network(read_tables(src), "T1", OPT)
    assert net.trafo["vkr_percent"].iloc[0] == pytest.approx(1.5)
    assert net.trafo["vk_percent"].iloc[0] == pytest.approx(np.hypot(1.5, 3.2))
    d = fx.write(tmp_path / "noeq", with_equipment=False)
    net, rep = build_lv_network(read_tables(d), "T1", OPT)
    assert net.trafo["vkr_percent"].iloc[0] == pytest.approx((1400 - 300) / 75000 * 100)   # copper losses
    assert net.trafo["vk_percent"].iloc[0] == 4.5
    with pytest.raises(BdgdBuildError, match="vk_percent"):
        build_lv_network(read_tables(d), "T1", BuildOptions(mv_kv=13.8, load_factor=0.5))


def test_no_silent_defaults(src, tmp_path) -> None:
    with pytest.raises(BdgdBuildError, match="load_factor"):
        build_lv_network(read_tables(src), "T1", BuildOptions(mv_kv=13.8, vk_percent=4.5))
    with pytest.raises(BdgdBuildError, match="not found"):
        build_lv_network(read_tables(src), "nope", OPT)
    t = read_tables(src)
    t["SSDBT"].loc[t["SSDBT"]["COD_ID"] == "S3", "TIP_CND"] = "ZZ"
    with pytest.raises(BdgdBuildError, match="conductor"):
        build_lv_network(t, "T1", OPT)
    net, rep = build_lv_network(t, "T1", BuildOptions(mv_kv=13.8, load_factor=0.5, vk_percent=4.5,
                                                    default_conductor=(1.0, 0.1, 100.0)))
    assert any("default conductor" in a for a in rep.assumptions)


def test_too_much_unattached_load_is_an_error(src) -> None:
    t = read_tables(src)
    t["UCBT_tab"].loc[t["UCBT_tab"]["COD_ID"] == "U1", "PAC"] = "Q1"     # the largest consumer, off-network
    with pytest.raises(BdgdBuildError, match="not in the LV network"):
        build_lv_network(t, "T1", OPT)


def test_save_register_and_run_a_study(src, tmp_path) -> None:
    net, rep = build_lv_network(read_tables(src), "T1", OPT)
    out = tmp_path / "net" / "br_feeder.json"
    meta = json.loads(save_network(net, rep, out, source_label="synthetic test export").read_text(encoding="utf-8"))
    assert meta["default_framework"] == "prodist_m8_bt" and meta["voltage_levels_kv"] == [0.38]
    assert "synthetic test export" in meta["citation"] and meta["build_report"]["counts"]["loads"] == 5
    try:
        info = register_network_file("br_feeder_test", out)
        assert info.source.startswith("file:") and info.country == "Brazil"
        from gridsense_sim.networks import make_network
        r = find_hosting_capacity_deterministic(make_network("br_feeder_test"), "br_feeder_test")
        assert r.bounded and r.lambda_max > 0
        assert r.conditions.framework == "prodist_m8_bt"
        assert len(make_network("br_feeder_test", existing_pv="keep").sgen) == 1
    finally:
        unregister_network("br_feeder_test")
    assert "br_feeder_test" not in NETWORKS


def test_cli_inspect_list_build(src, tmp_path, capsys) -> None:
    assert bdgd_main(["inspect", str(src)]) == 0
    assert bdgd_main(["list", str(src), "--min-consumers", "1"]) == 0
    assert "T1" in capsys.readouterr().out
    out = tmp_path / "x.json"
    args = ["build", str(src), "--transformer", "T1", "--mv-kv", "13.8", "--vk-percent", "4.5",
            "--source-label", "synthetic", "--out", str(out)]
    assert bdgd_main(args) == 2                                    # no load factor: refused
    assert "load_factor" in capsys.readouterr().err
    assert bdgd_main([*args, "--load-factor", "0.5"]) == 0
    assert out.is_file() and Path(str(out) + ".meta.json").is_file()


def test_parquet_export_is_read_too(tmp_path) -> None:
    pytest.importorskip("pyarrow")
    d = fx.write(tmp_path / "pq", fmt="parquet")
    net, _ = build_lv_network(read_tables(d), "T1", OPT)
    assert len(net.load) == 5


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_study_runner_accepts_a_network_file(src, tmp_path) -> None:
    pytest.importorskip("pyarrow")
    import run_hosting_capacity_study as study

    net, rep = build_lv_network(read_tables(src), "T1", OPT)
    out = tmp_path / "br.json"
    save_network(net, rep, out, source_label="synthetic")
    try:
        study.main(["--network", "br_runner_test", "--network-file", str(out), "--methods", "deterministic",
                    "--output-dir", str(tmp_path / "hc")])
        import pyarrow.parquet as pq
        row = pq.read_table(next((tmp_path / "hc").rglob("*.parquet"))).to_pylist()[0]
        payload = json.loads(row["raw_value"])
        assert payload["status"] == "ok" and payload["conditions"]["framework"] == "prodist_m8_bt"
        assert payload["params"]["network_source"].startswith("file:")
        assert payload["params"]["network_country"] == "Brazil"
    finally:
        unregister_network("br_runner_test")
        study.SUPPORTED_NETWORKS.pop("br_runner_test", None)
