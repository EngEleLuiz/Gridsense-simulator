"""A tiny synthetic BDGD export (no real data) for the converter tests.

Feeder T1: L0 - L1 - L2 - L3 with a lateral L1 - L4 and a service drop
L3 - R1. Deliberate defects: a self-loop, an island (X1 - X2) and one
consumer on a node that is not in the network.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

MONTHS = [f"ENE_{m:02d}" for m in range(1, 13)]
QUARTERS = [f"POT_{q:02d}" for q in range(1, 97)]


def _consumer(cod: str, node: str, phases: str, kwh: float, curve: str, link: str = "T1") -> dict:
    row = {"COD_ID": cod, "PAC": node, "FAS_CON": phases, "UNI_TR_MT": link, "TIP_CC": curve}
    row.update({m: f"{kwh:.1f}".replace(".", ",") for m in MONTHS})   # decimal comma, as in exports
    return row


def tables(with_equipment: bool = True, with_curves: bool = True) -> dict[str, pd.DataFrame]:
    out = {
        "UNTRMT": pd.DataFrame([
            {"COD_ID": "T1", "PAC_1": "M1", "PAC_2": "L0", "POT_NOM": "75", "PER_FER": "300", "PER_TOT": "1400"},
            {"COD_ID": "T2", "PAC_1": "M2", "PAC_2": "K0", "POT_NOM": "45", "PER_FER": "200", "PER_TOT": "900"},
        ]),
        "SEGCON": pd.DataFrame([
            {"COD_ID": "C1", "R1": "0,60", "X1": "0,10", "CNOM": "150"},
            {"COD_ID": "C2", "R1": "1.20", "X1": "0.11", "CNOM": "90"},
        ]),
        "SSDBT": pd.DataFrame([
            {"COD_ID": "S1", "PAC_1": "L0", "PAC_2": "L1", "TIP_CND": "C1", "COMP": "60", "UNI_TR_MT": "T1", "FAS_CON": "ABCN"},
            {"COD_ID": "S2", "PAC_1": "L1", "PAC_2": "L2", "TIP_CND": "C1", "COMP": "80", "UNI_TR_MT": "T1", "FAS_CON": "ABCN"},
            {"COD_ID": "S3", "PAC_1": "L2", "PAC_2": "L3", "TIP_CND": "C2", "COMP": "70", "UNI_TR_MT": "T1", "FAS_CON": "ABCN"},
            {"COD_ID": "S4", "PAC_1": "L1", "PAC_2": "L4", "TIP_CND": "C2", "COMP": "50", "UNI_TR_MT": "T1", "FAS_CON": "ABN"},
            {"COD_ID": "S5", "PAC_1": "L2", "PAC_2": "L2", "TIP_CND": "C1", "COMP": "5", "UNI_TR_MT": "T1", "FAS_CON": "ABCN"},
            {"COD_ID": "S6", "PAC_1": "X1", "PAC_2": "X2", "TIP_CND": "C1", "COMP": "30", "UNI_TR_MT": "T1", "FAS_CON": "ABCN"},
            {"COD_ID": "S7", "PAC_1": "K0", "PAC_2": "K1", "TIP_CND": "C1", "COMP": "40", "UNI_TR_MT": "T2", "FAS_CON": "ABCN"},
        ]),
        "RAMLIG": pd.DataFrame([
            {"COD_ID": "R1", "PAC_1": "L3", "PAC_2": "R1", "TIP_CND": "C2", "COMP": "15", "UNI_TR_MT": "T1", "FAS_CON": "AN"},
        ]),
        "UCBT_tab": pd.DataFrame([
            _consumer("U1", "L1", "ABCN", 900, "RES1"),
            _consumer("U2", "L2", "AN", 300, "RES1"),
            _consumer("U3", "L3", "BN", 250, ""),
            _consumer("U4", "L4", "ABN", 400, "RES1"),
            _consumer("U5", "R1", "CN", 200, ""),
            _consumer("U6", "Q9", "AN", 20, ""),       # node not in the network
            _consumer("U7", "X2", "AN", 15, ""),       # on the island
            _consumer("U8", "K1", "ABCN", 500, "RES1", link="T2"),
        ]),
        "UGBT_tab": pd.DataFrame([
            {"COD_ID": "G1", "PAC": "L2", "UNI_TR_MT": "T1", "POT_INST": "3,0"},
        ]),
    }
    if with_equipment:
        out["EQTRMT"] = pd.DataFrame([{"COD_ID": "E1", "UNI_TR_MT": "T1", "R": "1,5", "XHL": "3,2"}])
    if with_curves:
        shape = 0.35 + 0.65 * np.exp(-0.5 * ((np.arange(96) - 78) / 8.0) ** 2)    # evening peak, LF ~ 0.47
        rows = []
        for day in ("DU", "SA", "DO"):
            row = {"COD_ID": "RES1", "TIP_DIA": day}
            row.update({q: f"{v:.4f}" for q, v in zip(QUARTERS, shape)})
            rows.append(row)
        out["CRVCRG"] = pd.DataFrame(rows)
    return out


def write(directory: Path, fmt: str = "csv", **kw) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, df in tables(**kw).items():
        if fmt == "csv":
            df.to_csv(directory / f"{name}.csv", sep=";", index=False)
        else:
            df.to_parquet(directory / f"{name}.parquet", index=False)
    return directory
