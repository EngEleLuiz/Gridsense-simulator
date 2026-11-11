"""Batched fixed-point power flow on CPU or GPU with PyTorch (Phase 8.2).

One call solves ``B`` operating points of the same network at once::

    V_I  <-  w_I + conj(S_I / V_I) @ Z_II.T        (repeat until max |dV| < tol)

and returns voltage magnitudes and branch loadings for all of them. See
:mod:`.model` for the formulation and for what the model covers.

Convergence
-----------
The fixed point converges linearly; on low-voltage feeders it needs about
8-30 iterations up to voltages far beyond any limit. A sample that does not
reach ``tol`` within ``max_iter`` iterations, or whose voltage becomes
non-finite, is returned as ``converged = False`` -- the same meaning as
``LoadflowNotConverged`` in pandapower (the callers treat it as a violation).
The point where the two solvers stop converging is not identical, because
their iterations differ; it lies well above the voltage limits either way.

Precision
---------
``float64`` (default) reproduces pandapower to ~1e-9 pu. ``float32`` is
faster on consumer GPUs but only accurate to ~1e-5 pu, which is larger than
the event margin used by the studies (1e-6 pu): use it for exploration, not
for reported results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import BatchedModel

try:  # optional dependency
    import torch
except ImportError:  # pragma: no cover - exercised only without the extra
    torch = None

INSTALL_HINT = ('The batched solver needs PyTorch: pip install -e "simulator[gpu]" '
                "(see docs/ACCELERATION.md for the CUDA build).")


def torch_available() -> bool:
    return torch is not None


def resolve_device(device: str = "auto") -> str:
    """``auto`` -> ``cuda`` when a GPU is usable, else ``cpu``."""
    if torch is None:
        raise ImportError(INSTALL_HINT)
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("device='cuda' requested but PyTorch reports no usable GPU "
                           "(torch.cuda.is_available() is False). Install a CUDA build of PyTorch "
                           "or use --device cpu.")
    return device


def device_info(device: str) -> dict:
    """What ran the study, for provenance."""
    info = {"torch": torch.__version__, "device": device}
    if device.startswith("cuda"):
        info["gpu"] = torch.cuda.get_device_name(torch.device(device))
        info["cuda"] = torch.version.cuda
    else:
        info["threads"] = torch.get_num_threads()
    return info


@dataclass
class BatchResult:
    """NumPy results of one batch (``B`` operating points)."""

    converged: np.ndarray        # (B,) bool
    vm: np.ndarray               # (B, n_scope) pu; NaN for isolated scope buses
    line: np.ndarray             # (B, n_line) loading percent (0 for out-of-service lines)
    trafo: np.ndarray            # (B, n_trafo) loading percent
    iterations: int


class BatchedSolver:
    """Holds a :class:`BatchedModel` on a device and solves batches against it."""

    def __init__(self, model: BatchedModel, device: str = "auto", dtype: str = "float64",
                 max_iter: int = 100, tol: float | None = None, memory_mb: int = 1024) -> None:
        if torch is None:
            raise ImportError(INSTALL_HINT)
        if dtype not in ("float64", "float32"):
            raise ValueError("dtype must be 'float64' or 'float32'.")
        self.model = model
        self.device = resolve_device(device)
        self.dtype = dtype
        self.real = torch.float64 if dtype == "float64" else torch.float32
        self.cplx = torch.complex128 if dtype == "float64" else torch.complex64
        self.tol = tol if tol is not None else (1e-10 if dtype == "float64" else 1e-5)
        self.max_iter = max_iter
        self.power_flows = 0
        c = lambda a: torch.as_tensor(np.ascontiguousarray(a), dtype=self.cplx, device=self.device)  # noqa: E731
        r = lambda a: torch.as_tensor(np.ascontiguousarray(a), dtype=self.real, device=self.device)  # noqa: E731
        m = model
        self.w_inj, self.z_inj_t = c(m.w_inj), c(m.z_inj.T)
        self.w_scope, self.z_scope_t = c(m.w_scope), c(m.z_scope.T)
        self.line_const = c(m.line_const)
        self.line_map_t = c(np.transpose(m.line_map, (0, 2, 1)))
        self.trafo_const = c(m.trafo_const)
        self.trafo_map_t = c(np.transpose(m.trafo_map, (0, 2, 1)))
        self.line_scale, self.trafo_scale = r(m.line_scale), r(m.trafo_scale)
        self.s_fixed = c(m.s_fixed)
        ok = m.load_pos >= 0
        self.load_ok = torch.as_tensor(np.flatnonzero(ok), device=self.device)
        self.load_pos = torch.as_tensor(m.load_pos[ok], device=self.device)
        self.load_s = c((m.load_p[ok] + 1j * m.load_q[ok]) / m.base_mva)
        agg = np.zeros(m.n_inj, dtype=complex)
        np.add.at(agg, m.load_pos[ok], (m.load_p[ok] + 1j * m.load_q[ok]) / m.base_mva)
        self.load_agg = c(agg)
        self.pv_base = c(m.pv_base / m.base_mva)
        self.isolated = torch.as_tensor(m.scope_isolated, device=self.device)
        # bytes per sample: iterates on the injection buses plus every output row
        n_out = m.n_scope + 4 * (m.line_scale.size + m.trafo_scale.shape[1])
        per_sample = (16 if dtype == "float64" else 8) * (6 * m.n_inj + 2 * n_out + 16)
        self.chunk = max(1, int(memory_mb * 1024 * 1024 / per_sample))

    # -- public -----------------------------------------------------------
    def solve(self, load_mult: np.ndarray, pv_mult: np.ndarray, lam: np.ndarray | float) -> BatchResult:
        """Solve ``B`` operating points.

        Args:
            load_mult: ``(B,)`` (same multiplier for every load) or ``(B, n_loads)``.
            pv_mult: ``(B,)`` PV output per unit of rating.
            lam: Scalar or ``(B,)`` PV penetration.
        """
        load_mult = np.asarray(load_mult, dtype=float)
        pv = np.asarray(pv_mult, dtype=float) * np.asarray(lam, dtype=float)
        b = int(pv.size)
        if load_mult.shape[0] != b:
            raise ValueError("load_mult and pv_mult must have the same number of operating points.")
        m = self.model
        conv = np.empty(b, dtype=bool)
        vm = np.empty((b, m.n_scope))
        line = np.empty((b, m.line_scale.size))
        trafo = np.empty((b, m.trafo_scale.shape[1]))
        iterations = 0
        for start in range(0, b, self.chunk):
            sl = slice(start, min(b, start + self.chunk))
            c, v, ln, tr, it = self._solve_chunk(load_mult[sl], pv[sl])
            conv[sl], vm[sl], line[sl], trafo[sl] = c, v, ln, tr
            iterations = max(iterations, it)
        self.power_flows += b
        return BatchResult(conv, vm, line, trafo, iterations)

    # -- internals --------------------------------------------------------
    def _injections(self, load_mult: np.ndarray, pv: np.ndarray):
        lm = torch.as_tensor(load_mult, dtype=self.real, device=self.device)
        pvt = torch.as_tensor(pv, dtype=self.real, device=self.device)
        s = self.s_fixed.unsqueeze(0) + pvt.unsqueeze(1) * self.pv_base.unsqueeze(0)
        if lm.ndim == 1:
            return s - lm.unsqueeze(1) * self.load_agg.unsqueeze(0)
        demand = lm.index_select(1, self.load_ok) * self.load_s.unsqueeze(0)
        return s.index_add(1, self.load_pos, -demand)

    @torch.no_grad() if torch is not None else (lambda f: f)
    def _solve_chunk(self, load_mult: np.ndarray, pv: np.ndarray):
        s = self._injections(load_mult, pv)
        v = self.w_inj.unsqueeze(0).expand_as(s).clone()
        delta = torch.full((s.shape[0],), float("inf"), dtype=self.real, device=self.device)
        it = 0
        for it in range(1, self.max_iter + 1):
            v_new = self.w_inj + torch.conj(s / v) @ self.z_inj_t
            delta = (v_new - v).abs().amax(dim=1)
            v = v_new
            if not bool((delta >= self.tol).any()):   # NaN compares False: a diverged sample does not block
                break
        j = torch.conj(s / v)
        converged = (delta < self.tol) & torch.isfinite(delta) & torch.isfinite(j.abs().amax(dim=1))
        j = torch.where(converged.unsqueeze(1), j, torch.zeros_like(j))
        vm = (self.w_scope + j @ self.z_scope_t).abs()
        vm = vm.masked_fill(self.isolated.unsqueeze(0), float("nan"))
        i_line = (self.line_const.unsqueeze(1) + torch.matmul(j, self.line_map_t)).abs().amax(dim=0)
        i_trafo = ((self.trafo_const.unsqueeze(1) + torch.matmul(j, self.trafo_map_t)).abs()
                   * self.trafo_scale.unsqueeze(1)).amax(dim=0)
        return (converged.cpu().numpy(), vm.double().cpu().numpy(),
                (i_line * self.line_scale).double().cpu().numpy(), i_trafo.double().cpu().numpy(), it)


__all__ = ["BatchResult", "BatchedSolver", "INSTALL_HINT", "device_info", "resolve_device", "torch_available"]
