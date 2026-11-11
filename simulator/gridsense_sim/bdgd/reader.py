"""Read BDGD layers into pandas DataFrames (attributes only, never geometry).

Accepted inputs:

* a directory with one ``<LAYER>.csv`` or ``<LAYER>.parquet`` per layer
  (export them from the geodatabase with QGIS or ogr2ogr); CSV separators
  ``;`` and ``,`` and decimal commas are handled;
* a geodatabase / GeoPackage (``.gdb``, ``.gpkg``) when ``pyogrio`` is
  installed -- geometry is not read.

Identifiers are read as strings: BDGD codes are not numbers, and reading them
as numbers silently drops leading zeros.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import BdgdSchema


class BdgdReadError(ValueError):
    """A required layer or column is missing."""


def _read_csv(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        head = fh.readline()
    sep = ";" if head.count(";") > head.count(",") else ","
    return pd.read_csv(path, sep=sep, dtype=str, encoding="utf-8-sig", encoding_errors="replace",
                       keep_default_na=False)


def _find(directory: Path, layer: str) -> Path | None:
    for candidate in directory.iterdir():
        if candidate.stem.lower() == layer.lower() and candidate.suffix.lower() in (".csv", ".parquet"):
            return candidate
    return None


def read_layer(source: Path, layer: str) -> pd.DataFrame | None:
    """One layer as strings, or ``None`` when the source does not have it."""
    if source.is_dir() and source.suffix.lower() != ".gdb":
        path = _find(source, layer)
        if path is None:
            return None
        df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else _read_csv(path)
    else:
        try:
            import pyogrio
        except ImportError as exc:
            raise BdgdReadError(
                f"Reading {source.suffix} needs pyogrio (pip install pyogrio); or export the layers "
                "to CSV/Parquet and pass the directory.") from exc
        layers = {name.lower(): name for name, _ in pyogrio.list_layers(source)}
        if layer.lower() not in layers:
            return None
        df = pyogrio.read_dataframe(source, layer=layers[layer.lower()], read_geometry=False)
    df = df.drop(columns=[c for c in df.columns if c.lower() in ("geometry", "shape", "geom")], errors="ignore")
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.astype(str).apply(lambda col: col.str.strip())


def read_tables(source: str | Path, schema: BdgdSchema | None = None) -> dict[str, pd.DataFrame]:
    """Read every layer the converter uses. Required layers and columns are checked."""
    schema = schema or BdgdSchema()
    source = Path(source)
    if not source.exists():
        raise BdgdReadError(f"BDGD source {source} does not exist.")
    tables: dict[str, pd.DataFrame] = {}
    problems: list[str] = []
    for layer, columns in schema.required_columns().items():
        df = read_layer(source, layer)
        if df is None:
            problems.append(f"layer {layer} not found")
            continue
        missing = [c for c in columns if c.upper() not in df.columns]
        if missing:
            problems.append(f"layer {layer}: missing column(s) {missing}")
        tables[layer] = df
    for layer in schema.optional_layers():
        df = read_layer(source, layer)
        if df is not None:
            tables[layer] = df
    if problems:
        raise BdgdReadError(
            "BDGD source does not match the expected schema: " + "; ".join(problems)
            + ". Check the names with 'gridsense-bdgd inspect' and override them with --schema.")
    return tables


def inspect(source: str | Path, schema: BdgdSchema | None = None) -> list[dict]:
    """Per layer: found or not, rows, and which expected columns are missing."""
    schema = schema or BdgdSchema()
    source = Path(source)
    out = []
    required = schema.required_columns()
    for layer in [*required, *schema.optional_layers()]:
        df = read_layer(source, layer)
        row = {"layer": layer, "required": layer in required, "found": df is not None, "rows": 0, "missing": []}
        if df is not None:
            row["rows"] = len(df)
            row["missing"] = [c for c in required.get(layer, []) if c.upper() not in df.columns]
            row["columns"] = list(df.columns)
        out.append(row)
    return out


def to_number(series: pd.Series) -> pd.Series:
    """Numbers written with a decimal point or a decimal comma; blanks become NaN."""
    text = series.astype(str).str.strip()
    comma = text.str.contains(",", regex=False) & ~text.str.contains(".", regex=False)
    text = text.where(~comma, text.str.replace(",", ".", regex=False))
    return pd.to_numeric(text, errors="coerce")


__all__ = ["BdgdReadError", "inspect", "read_layer", "read_tables", "to_number"]
