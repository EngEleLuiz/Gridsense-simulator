"""Phase 8.2: the batched power flow reproduces pandapower."""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pandapower.networks as pn
import pytest

from gridsense_sim.accel import UnsupportedNetworkError, build_model
from gridsense_sim.accel.model import solve_reference
from gridsense_sim.hosting_capacity import limits_for, proportional_to_load, voltage_scope_buses
from gridsense_sim.hosting_capacity.allocation import apply_allocation
from gridsense_sim.networks import make_network

POINTS = [(1.0, 0.0, 0.0), (0.3, 0.9, 1.5), (0.5, 1.0, 3.0), (1.2, 0.2, 0.5)]   # load, pv output, lambda


def _setup(key: str):
    net = make_network(key)
    scope = voltage_scope_buses(net, limits_for(key))
    alloc = proportional_to_load(net)
    return net, scope, alloc, build_model(net, scope, alloc.base_mw_per_bus)


def _pandapower(key, alloc, scope, load, pv, lam):
    net = make_network(key)
    net.load["p_mw"] *= load
    net.load["q_mvar"] *= load
    apply_allocation(net, alloc, lam * pv)
    pp.runpp(net)
    return (net.res_bus.loc[list(scope), "vm_pu"].to_numpy(),
            np.nan_to_num(net.res_line["loading_percent"].to_numpy()),
            np.nan_to_num(net.res_trafo["loading_percent"].to_numpy()))


@pytest.mark.parametrize("key", ["cigre_lv", "kerber_landnetz", "kerber_vorstadt"])
def test_model_reproduces_pandapower_without_torch(key) -> None:
    net, scope, alloc, model = _setup(key)
    assert model.check["max_abs_dv_pu"] < 1e-7
    for load, pv, lam in POINTS:
        vm, line, trafo = solve_reference(model, model.injections(np.array([load]), np.array([pv]), lam)[0])
        ref_vm, ref_line, ref_trafo = _pandapower(key, alloc, scope, load, pv, lam)
        assert np.nanmax(np.abs(vm - ref_vm)) < 1e-7
        assert np.max(np.abs(line - ref_line)) < 1e-3 and np.max(np.abs(trafo - ref_trafo)) < 1e-3


def test_model_does_not_mutate_the_network(cigre) -> None:
    before = cigre.load.copy()
    n_sgen = len(cigre.sgen)
    build_model(cigre, voltage_scope_buses(cigre, limits_for("cigre_lv")), proportional_to_load(cigre).base_mw_per_bus)
    assert cigre.load.equals(before) and len(cigre.sgen) == n_sgen


def test_unsupported_networks_are_refused() -> None:
    net = pn.create_cigre_network_lv()
    scope = voltage_scope_buses(net, limits_for("cigre_lv"))
    base = proportional_to_load(net).base_mw_per_bus
    with_gen = pn.create_cigre_network_lv()
    pp.create_gen(with_gen, int(with_gen.load["bus"].iloc[0]), p_mw=0.01, vm_pu=1.0)
    with pytest.raises(UnsupportedNetworkError, match="generators"):
        build_model(with_gen, scope, base)
    zip_load = pn.create_cigre_network_lv()
    zip_load.load["const_z_p_percent"] = 30.0
    with pytest.raises(UnsupportedNetworkError, match="ZIP"):
        build_model(zip_load, scope, base)
    three_phase = make_network("ieee_european_lv_offpeak")
    with pytest.raises(UnsupportedNetworkError, match="unbalanced"):
        build_model(three_phase, [int(three_phase.bus.index[5])], {int(three_phase.bus.index[5]): 0.01})


def test_existing_generation_and_shunts_are_part_of_the_fixed_injections() -> None:
    net = pn.create_cigre_network_lv()
    bus = int(net.load["bus"].iloc[3])
    pp.create_sgen(net, bus, p_mw=0.02, q_mvar=0.0)
    pp.create_shunt(net, bus, q_mvar=0.005)
    scope = voltage_scope_buses(net, limits_for("cigre_lv"))
    model = build_model(net, scope, proportional_to_load(net).base_mw_per_bus)
    assert model.check["max_abs_dv_pu"] < 1e-7
    vm, _, _ = solve_reference(model, model.injections(np.array([0.4]), np.array([0.0]), 0.0)[0])
    net.load["p_mw"] *= 0.4
    net.load["q_mvar"] *= 0.4
    pp.runpp(net)
    assert np.max(np.abs(vm - net.res_bus.loc[list(scope), "vm_pu"].to_numpy())) < 1e-7


def test_isolated_scope_bus_has_no_voltage() -> None:
    net = pn.create_cigre_network_lv()
    end_bus = int(net.line["to_bus"].iloc[-1])
    net.line.loc[net.line["to_bus"] == end_bus, "in_service"] = False
    scope = voltage_scope_buses(net, limits_for("cigre_lv"))
    model = build_model(net, scope, proportional_to_load(net).base_mw_per_bus)
    vm, _, _ = solve_reference(model, model.injections(np.ones(1), np.zeros(1), 0.0)[0])
    pp.runpp(net)
    ref = net.res_bus.loc[list(scope), "vm_pu"].to_numpy()
    assert model.scope_isolated.any()
    assert np.array_equal(np.isnan(vm), np.isnan(ref))


# ----------------------------------------------------------------- torch part
torch = pytest.importorskip("torch")
from gridsense_sim.accel.solver import BatchedSolver, device_info, resolve_device  # noqa: E402


@pytest.fixture(scope="module")
def cigre_solver():
    net, scope, alloc, model = _setup("cigre_lv")
    return net, scope, alloc, model, BatchedSolver(model, device="cpu")


def test_batched_solver_matches_pandapower(cigre_solver) -> None:
    net, scope, alloc, model, solver = cigre_solver
    load, pv, lam = (np.array(c) for c in zip(*POINTS))
    r = solver.solve(load, pv, lam)
    assert r.converged.all() and r.vm.shape == (len(POINTS), len(scope))
    for i, point in enumerate(POINTS):
        ref_vm, ref_line, ref_trafo = _pandapower("cigre_lv", alloc, scope, *point)
        assert np.max(np.abs(r.vm[i] - ref_vm)) < 1e-7
        assert np.max(np.abs(r.line[i] - ref_line)) < 1e-3 and np.max(np.abs(r.trafo[i] - ref_trafo)) < 1e-3


def test_per_load_multipliers_and_chunking(cigre_solver) -> None:
    net, scope, alloc, model, solver = cigre_solver
    rng = np.random.default_rng(1)
    b = 64
    load, pv, lam = rng.uniform(0.2, 1.1, b), rng.uniform(0, 1, b), rng.uniform(0, 2.5, b)
    whole = solver.solve(load, pv, lam)
    per_load = solver.solve(np.tile(load[:, None], (1, len(net.load))), pv, lam)
    assert np.max(np.abs(per_load.vm - whole.vm)) < 1e-12
    tiny = BatchedSolver(model, device="cpu", memory_mb=1)
    tiny.chunk = 7                                              # force several chunks
    chunked = tiny.solve(load, pv, lam)
    assert np.max(np.abs(chunked.vm - whole.vm)) < 1e-12 and tiny.power_flows == b
    # a per-load matrix that differs between loads changes the answer
    m = np.tile(load[:, None], (1, len(net.load)))
    m[:, 0] *= 3.0
    assert np.max(np.abs(solver.solve(m, pv, lam).vm - whole.vm)) > 1e-6


def test_non_convergence_is_flagged_per_sample(cigre_solver) -> None:
    *_, solver = cigre_solver
    r = solver.solve(np.array([1.0, 400.0, 0.5]), np.zeros(3), 0.0)   # 400x nominal load: no solution
    assert r.converged.tolist() == [True, False, True]
    assert np.isfinite(r.vm[[0, 2]]).all()


def test_float32_is_close_but_not_exact(cigre_solver) -> None:
    net, scope, alloc, model, solver = cigre_solver
    load, pv, lam = (np.array(c) for c in zip(*POINTS))
    r32 = BatchedSolver(model, device="cpu", dtype="float32").solve(load, pv, lam)
    r64 = solver.solve(load, pv, lam)
    assert r32.converged.all()
    assert 0 < np.max(np.abs(r32.vm - r64.vm)) < 1e-4


def test_device_resolution() -> None:
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("cpu", "cuda")
    assert "torch" in device_info("cpu")
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError, match="no usable GPU"):
            resolve_device("cuda")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no CUDA device")
def test_cuda_matches_cpu(cigre_solver) -> None:
    net, scope, alloc, model, solver = cigre_solver
    rng = np.random.default_rng(2)
    load, pv, lam = rng.uniform(0.2, 1.1, 256), rng.uniform(0, 1, 256), rng.uniform(0, 2.5, 256)
    gpu = BatchedSolver(model, device="cuda").solve(load, pv, lam)
    cpu = solver.solve(load, pv, lam)
    assert np.max(np.abs(gpu.vm - cpu.vm)) < 1e-10 and "gpu" in device_info("cuda")
