"""Batched power flow for time-series studies (Phase 8).

* :mod:`.model` -- extracts a compact linear-algebra model from a solved
  pandapower network (no PyTorch needed);
* :mod:`.solver` -- solves many operating points at once on CPU or GPU
  with PyTorch (optional dependency: ``pip install -e "simulator[gpu]"``).

pandapower stays the reference: the model is built from pandapower's own
admittance data and is checked against a pandapower solution when it is
created (:func:`.model.build_model`).
"""

from .model import BatchedModel, UnsupportedNetworkError, build_model

__all__ = ["BatchedModel", "UnsupportedNetworkError", "build_model"]
