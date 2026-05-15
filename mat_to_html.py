"""mat-preview: read MATLAB .mat (v6/v7 via scipy, v7.3/HDF5 via h5py) and
render a variable summary as a single HTML page."""

from __future__ import annotations

import html
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
SCIPY_META_KEYS = {"__header__", "__version__", "__globals__", "__function_workspace__"}
H5_SKIP_KEYS = {"#refs#", "#subsystem#"}

LARGE_SUMMARY_BYTES = 64 * 1024 * 1024
MAX_STRUCT_DEPTH = 4
MAX_RECORDS = 200
MAX_TABLE_PREVIEW_ROWS = 5
MAX_TABLE_PREVIEW_COLS = 40
MAX_RENDER_PREVIEW_COLS = 6
KNOWN_MATLAB_CLASSES = {
    "double", "single",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
    "logical", "char", "cell", "struct", "function_handle",
}


@dataclass
class ColumnPreview:
    name: str
    dtype: str
    n_rows: int
    n_nan: int
    vmin: Optional[float]
    vmax: Optional[float]
    first: list


@dataclass
class TableData:
    n_rows: int
    n_cols: int
    columns: list  # list[ColumnPreview]
    uncertain: bool


@dataclass
class VarRecord:
    name: str
    matlab_class: str
    shape: str
    dtype: str
    nbytes: int
    summary: str
    table_data: Optional[TableData] = None
    advanced: str = ""
    preview_html: str = ""


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n} B"


def detect_mat_kind(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(128)
    if head.startswith(HDF5_MAGIC):
        return "v7.3"
    if b"MATLAB 7.3 MAT-file" in head:
        return "v7.3"
    if head[:6] == b"MATLAB" and b"5.0 MAT-file" in head:
        return "v6/v7"
    return "unknown"


def shape_str(shape) -> str:
    if not shape:
        return "scalar"
    return "x".join(str(d) for d in shape)


# ---------------------------------------------------------------------------
# scipy branch (v6/v7)
# ---------------------------------------------------------------------------

def load_scipy(path: str):
    from scipy.io import loadmat
    return loadmat(path, struct_as_record=True, squeeze_me=False, mat_dtype=False)


def scipy_class_hint(arr) -> str:
    import numpy as np
    if isinstance(arr, np.ndarray):
        if arr.dtype == np.bool_: return "logical"
        if arr.dtype.kind in ("U", "S"): return "char"
        if arr.dtype.kind == "O": return "cell"
        if arr.dtype.names: return "struct"
        return str(arr.dtype)
    return type(arr).__name__


def scipy_summary(arr) -> str:
    import numpy as np
    try:
        if not isinstance(arr, np.ndarray):
            return repr(arr)[:80]
        if arr.dtype.names:
            fields = list(arr.dtype.names)
            tail = f", … (+{len(fields) - 8})" if len(fields) > 8 else ""
            return "fields: " + ", ".join(fields[:8]) + tail
        if arr.dtype.kind in ("U", "S"):
            s = arr.item() if arr.size == 1 else " ".join(map(str, arr.flatten()[:4]))
            return f'"{s[:80]}{"…" if len(s) > 80 else ""}"'
        if arr.dtype.kind == "O":
            return f"{arr.size} cell(s)"
        if arr.dtype.kind == "c" and arr.size:
            return f"complex, |z| ~ [{np.abs(arr).min():.4g}, {np.abs(arr).max():.4g}]"
        if arr.dtype.kind in ("i", "u", "f") and arr.size:
            finite = arr[np.isfinite(arr)] if arr.dtype.kind == "f" else arr
            if finite.size == 0:
                return "all non-finite"
            return f"range [{finite.min():.6g}, {finite.max():.6g}]"
        return ""
    except Exception as e:
        return f"<summary error: {e}>"


def _numeric_stats_from_array(arr, max_values: int = 20) -> tuple[str, list]:
    import numpy as np
    flat = arr.flatten()
    if flat.size == 0:
        return "empty", []
    kind = arr.dtype.kind
    values = [_format_scalar(x, kind) for x in flat[:max_values]]
    if kind == "c":
        mag = np.abs(flat)
        finite = mag[np.isfinite(mag)]
        if finite.size == 0:
            return f"all non-finite, NaN {mag.size}", values
        return (
            f"|z| min {finite.min():.6g}, max {finite.max():.6g}, "
            f"mean {finite.mean():.6g}, NaN {int(mag.size - finite.size)}",
            values,
        )
    if kind == "f":
        finite = flat[np.isfinite(flat)]
        if finite.size == 0:
            return f"all non-finite, NaN {flat.size}", values
        return (
            f"min {finite.min():.6g}, max {finite.max():.6g}, "
            f"mean {finite.mean():.6g}, NaN {int(flat.size - finite.size)}",
            values,
        )
    return (
        f"min {flat.min():.6g}, max {flat.max():.6g}, "
        f"mean {flat.mean():.6g}, NaN 0",
        values,
    )


def _render_values_preview(values: list) -> str:
    if not values:
        return ""
    rows = "".join(
        f"<tr><td class='r'>{idx}</td><td>{html.escape(str(value))}</td></tr>"
        for idx, value in enumerate(values, 1)
    )
    return (
        "<table class='mini values'><thead><tr><th class='r'>#</th><th>value</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _numeric_preview_html(shape: str, dtype: str, stats: str, values: list) -> str:
    return (
        f"<div class='submeta'>{html.escape(dtype)} | {html.escape(shape)} | "
        f"{html.escape(stats)}</div>"
        + _render_values_preview(values)
    )


def _is_opaque_struct(arr) -> bool:
    """scipy returns MATLAB opaque objects (table, datetime, …) as void
    arrays with internal fields like ('s0','s1','s2','arr')."""
    names = getattr(getattr(arr, "dtype", None), "names", None)
    return bool(names and len(names) >= 4 and names[0:3] == ("s0", "s1", "s2"))


def _opaque_class_from_scipy(arr) -> str:
    try:
        s2 = arr.flatten()[0]["s2"]
        if isinstance(s2, (bytes, bytearray)):
            return s2.decode("ascii", "replace").rstrip("\x00") or "opaque"
        if hasattr(s2, "tobytes"):
            return s2.tobytes().decode("ascii", "replace").rstrip("\x00") or "opaque"
        return str(s2)[:32] or "opaque"
    except Exception:
        return "opaque"


def _scipy_leaf_record(name: str, v) -> VarRecord:
    import numpy as np
    if isinstance(v, np.ndarray) and _is_opaque_struct(v):
        cls = _opaque_class_from_scipy(v)
        return VarRecord(
            name=name, matlab_class=cls, shape="(opaque)",
            dtype="-", nbytes=int(v.nbytes),
            summary=f"MATLAB {cls} object",
        )
    shape = shape_str(getattr(v, "shape", ()))
    dtype = str(getattr(v, "dtype", type(v).__name__))
    summary = scipy_summary(v)
    preview_html = ""
    if isinstance(v, np.ndarray) and v.dtype.kind in ("i", "u", "f", "c"):
        stats, values = _numeric_stats_from_array(v)
        summary = stats
        preview_html = _numeric_preview_html(shape, dtype, stats, values)
    elif isinstance(v, np.ndarray) and v.dtype.kind in ("U", "S"):
        text = str(v.item() if v.size == 1 else " ".join(map(str, v.flatten()[:20])))
        preview_html = f"<pre>{html.escape(text[:1000])}</pre>"

    return VarRecord(
        name=name,
        matlab_class=scipy_class_hint(v),
        shape=shape,
        dtype=dtype,
        nbytes=int(getattr(v, "nbytes", 0)),
        summary=summary,
        preview_html=preview_html,
    )


def _scipy_walk(name: str, v, recs: list, depth: int) -> None:
    import numpy as np
    if len(recs) >= MAX_RECORDS:
        return
    if (isinstance(v, np.ndarray) and v.dtype.names
            and not _is_opaque_struct(v)
            and v.size == 1
            and depth < MAX_STRUCT_DEPTH):
        fields = v.dtype.names
        recs.append(VarRecord(
            name=name, matlab_class="struct",
            shape=f"{len(fields)} field(s)",
            dtype="-", nbytes=int(v.nbytes),
            summary="expanded below",
        ))
        scalar = v.reshape(())[()] if v.ndim else v[()]
        for fname in fields:
            child = scalar[fname]
            # scipy wraps scalar fields in 1-element arrays; unwrap once.
            if isinstance(child, np.ndarray) and child.size == 1 and child.dtype.names:
                pass
            _scipy_walk(f"{name}.{fname}", child, recs, depth + 1)
    else:
        recs.append(_scipy_leaf_record(name, v))


def records_from_scipy(mat: dict) -> list[VarRecord]:
    recs: list[VarRecord] = []
    for name in sorted(k for k in mat.keys() if k not in SCIPY_META_KEYS):
        _scipy_walk(name, mat[name], recs, 0)
    return recs


# ---------------------------------------------------------------------------
# h5py branch (v7.3) — MCOS table decoding
# ---------------------------------------------------------------------------

def _attr_str(item, key) -> str:
    if key not in item.attrs:
        return ""
    v = item.attrs[key]
    if hasattr(v, "tobytes"):
        return v.tobytes().decode("ascii", "replace").rstrip("\x00")
    if isinstance(v, (bytes, bytearray)):
        return v.decode("ascii", "replace").rstrip("\x00")
    return str(v)


def _decode_chars(dset) -> str:
    """MATLAB v7.3 chars are uint16 UTF-16LE."""
    import numpy as np
    data = dset[...]
    if dset.dtype == np.uint16:
        return data.tobytes().decode("utf-16-le", "replace").replace("\x00", "")
    return data.tobytes().decode("utf-8", "replace").replace("\x00", "")


def _preview_ref_value(h5file, ref):
    import h5py
    try:
        target = h5file[ref]
    except Exception:
        return ""
    matlab_class = _attr_str(target, "MATLAB_class")
    if isinstance(target, h5py.Dataset):
        if matlab_class == "char":
            return _decode_chars(target)
        if target.size == 1 and target.dtype.kind in ("i", "u", "f"):
            value = target[...].flat[0]
            if target.dtype.kind == "f":
                return float(value)
            return int(value)
        if target.dtype.kind in ("i", "u", "f") and target.size <= MAX_TABLE_PREVIEW_ROWS:
            return ", ".join(str(x) for x in target[...].flatten())
        return f"<{matlab_class or str(target.dtype)} {shape_str(tuple(reversed(target.shape)))}>"
    return f"<{matlab_class or 'group'}>"


def _find_mcos_refs(h5file):
    """Return the 1D array of refs from /#subsystem#/MCOS, or None."""
    import h5py
    if "#subsystem#" not in h5file:
        return None
    sub = h5file["#subsystem#"]
    for key in sub.keys():
        item = sub[key]
        if isinstance(item, h5py.Dataset) and item.dtype == h5py.ref_dtype:
            return item[...].flatten()
    return None


def _scan_mcos_table_blocks(h5file, mcos_refs):
    """Identify MATLAB table-pattern blocks in MCOS.

    Pattern (6 mandatory consecutive entries, optional meta group at +6):
        i+0: cell(C,1) of object refs  -> column data
        i+1: float64 (1,1) == 2.0      -> marker
        i+2: float64 (1,1) == n_rows
        i+3: uint64 (2,) == [0,0]      -> placeholder
        i+4: float64 (1,1) == n_cols
        i+5: cell(C,1) of object refs  -> column names
    """
    import h5py, numpy as np
    blocks = []
    i = 0
    n = len(mcos_refs)
    while i + 5 < n:
        try:
            data_cell = h5file[mcos_refs[i]]
            marker    = h5file[mcos_refs[i + 1]]
            n_rows_d  = h5file[mcos_refs[i + 2]]
            n_cols_d  = h5file[mcos_refs[i + 4]]
            names_cell = h5file[mcos_refs[i + 5]]
        except Exception:
            i += 1
            continue
        if not (isinstance(data_cell, h5py.Dataset) and data_cell.dtype == h5py.ref_dtype):
            i += 1
            continue
        if not (isinstance(marker, h5py.Dataset) and marker.size == 1
                and marker.dtype.kind == "f" and float(marker[...].flat[0]) == 2.0):
            i += 1
            continue
        if not (isinstance(n_rows_d, h5py.Dataset) and n_rows_d.size == 1
                and n_rows_d.dtype.kind == "f"):
            i += 1
            continue
        if not (isinstance(n_cols_d, h5py.Dataset) and n_cols_d.size == 1
                and n_cols_d.dtype.kind == "f"):
            i += 1
            continue
        if not (isinstance(names_cell, h5py.Dataset) and names_cell.dtype == h5py.ref_dtype):
            i += 1
            continue
        n_rows = int(n_rows_d[...].flat[0])
        n_cols = int(n_cols_d[...].flat[0])
        if data_cell.size != n_cols or names_cell.size != n_cols or n_cols == 0:
            i += 1
            continue
        blocks.append({
            "data_idx": i,
            "names_idx": i + 5,
            "n_rows": n_rows,
            "n_cols": n_cols,
        })
        i += 7  # skip block + optional meta
    return blocks


def _read_table_block(h5file, mcos_refs, block) -> TableData:
    import h5py, numpy as np
    name_refs = h5file[mcos_refs[block["names_idx"]]][...].flatten()
    data_refs = h5file[mcos_refs[block["data_idx"]]][...].flatten()

    columns: list = []
    for i in range(block["n_cols"]):
        if i >= MAX_TABLE_PREVIEW_COLS:
            break
        try:
            col_dset = h5file[data_refs[i]]
            col_name = _decode_chars(h5file[name_refs[i]])
        except Exception as e:
            columns.append(ColumnPreview(
                name=f"col_{i}", dtype="?", n_rows=block["n_rows"],
                n_nan=0, vmin=None, vmax=None, first=[f"<error: {e}>"],
            ))
            continue

        dtype_str = str(col_dset.dtype)
        n_rows = block["n_rows"]
        n_nan = 0
        vmin = vmax = None
        first: list = []
        nbytes = int(col_dset.size) * col_dset.dtype.itemsize

        try:
            if nbytes > LARGE_SUMMARY_BYTES:
                first = ["-"]
            elif col_dset.dtype == np.uint16 and _attr_str(col_dset, "MATLAB_class") == "char":
                s = _decode_chars(col_dset)
                first = [s[:80] + ("…" if len(s) > 80 else "")]
                dtype_str = "char"
            elif col_dset.dtype.kind in ("i", "u", "f", "c"):
                arr = col_dset[...].flatten()
                if col_dset.dtype.kind == "f":
                    finite = arr[np.isfinite(arr)]
                    n_nan = int(arr.size - finite.size)
                    if finite.size:
                        vmin = float(finite.min())
                        vmax = float(finite.max())
                elif col_dset.dtype.kind == "c":
                    mag = np.abs(arr)
                    vmin, vmax = float(mag.min()), float(mag.max())
                else:
                    vmin, vmax = float(arr.min()), float(arr.max())
                first = [
                    (float(x) if np.isfinite(x) else "NaN")
                    if col_dset.dtype.kind == "f" else
                    (complex(x).__repr__() if col_dset.dtype.kind == "c" else int(x))
                    for x in arr[:MAX_TABLE_PREVIEW_ROWS]
                ]
            elif col_dset.dtype == h5py.ref_dtype:
                refs = col_dset[...].flatten()
                values = [_preview_ref_value(h5file, ref) for ref in refs[:MAX_TABLE_PREVIEW_ROWS]]
                first = values
                dtype_str = "cellstr" if all(isinstance(v, str) for v in values) else "cell"
                n_nan = sum(1 for v in values if v in ("", None))
            else:
                first = [f"<{dtype_str}>"]
        except Exception as e:
            first = [f"<read error: {e}>"]

        columns.append(ColumnPreview(
            name=col_name, dtype=dtype_str, n_rows=n_rows,
            n_nan=n_nan, vmin=vmin, vmax=vmax, first=first,
        ))

    return TableData(
        n_rows=block["n_rows"],
        n_cols=block["n_cols"],
        columns=columns,
        uncertain=False,
    )


def _format_scalar(x, dtype_kind: str):
    import numpy as np
    if dtype_kind == "f":
        return f"{float(x):.6g}" if np.isfinite(x) else "NaN"
    if dtype_kind == "c":
        return complex(x).__repr__()
    if dtype_kind in ("i", "u"):
        return int(x)
    return str(x)


def _numeric_summary_and_preview(dset, max_values: int = 20) -> tuple[str, list]:
    import numpy as np
    nbytes = int(dset.size) * dset.dtype.itemsize
    if nbytes > LARGE_SUMMARY_BYTES:
        return f"(>{human_bytes(LARGE_SUMMARY_BYTES)}, stats skipped)", []

    data = dset[...]
    flat = data.flatten()
    if flat.size == 0:
        return "empty", []

    kind = dset.dtype.kind
    preview = [_format_scalar(x, kind) for x in flat[:max_values]]

    if kind == "c":
        mag = np.abs(flat)
        finite = mag[np.isfinite(mag)]
        if finite.size == 0:
            return "all non-finite", preview
        summary = (
            f"|z| min {finite.min():.6g}, max {finite.max():.6g}, "
            f"mean {finite.mean():.6g}, NaN {int(mag.size - finite.size)}"
        )
        return summary, preview

    if kind == "f":
        finite = flat[np.isfinite(flat)]
        n_nan = int(flat.size - finite.size)
        if finite.size == 0:
            return f"all non-finite, NaN {n_nan}", preview
        summary = (
            f"min {finite.min():.6g}, max {finite.max():.6g}, "
            f"mean {finite.mean():.6g}, NaN {n_nan}"
        )
        return summary, preview

    summary = (
        f"min {flat.min():.6g}, max {flat.max():.6g}, "
        f"mean {flat.mean():.6g}, NaN 0"
    )
    return summary, preview


class _McosState:
    """Per-file MCOS resolution state. Two passes: collect k values, then decode."""

    def __init__(self, h5file):
        self.h5file = h5file
        self.mcos_refs = _find_mcos_refs(h5file)
        self.blocks = (
            _scan_mcos_table_blocks(h5file, self.mcos_refs)
            if self.mcos_refs is not None else []
        )
        self.k_to_rank: dict = {}  # populated after collect phase
        self._cache: dict = {}

    def collect_k(self, dset) -> Optional[int]:
        try:
            data = dset[...].flatten()
            if data.size < 5:
                return None
            return int(data[4])
        except Exception:
            return None

    def finalize_ranking(self, k_values: set):
        # Map sorted unique k → rank among detected table blocks
        ks = sorted(k_values)
        for rank, k in enumerate(ks):
            self.k_to_rank[k] = rank

    def decode(self, dset) -> Optional[TableData]:
        if not self.blocks:
            return None
        k = self.collect_k(dset)
        if k is None or k not in self.k_to_rank:
            return None
        rank = self.k_to_rank[k]
        if rank >= len(self.blocks):
            return None
        if k in self._cache:
            return self._cache[k]
        try:
            td = _read_table_block(self.h5file, self.mcos_refs, self.blocks[rank])
        except Exception:
            td = None
        self._cache[k] = td
        return td


def _h5_dataset_record(name: str, dset, mcos: Optional[_McosState] = None) -> VarRecord:
    import numpy as np
    matlab_class = _attr_str(dset, "MATLAB_class") or str(dset.dtype)
    nbytes = int(dset.size) * dset.dtype.itemsize

    # Try semantic decoding for MATLAB tables via MCOS subsystem.
    if matlab_class == "table" and mcos is not None:
        td = mcos.decode(dset)
        if td is not None:
            return VarRecord(
                name=name, matlab_class="table",
                shape=f"{td.n_rows}x{td.n_cols}",
                dtype="mixed", nbytes=nbytes,
                summary=", ".join(c.name for c in td.columns[:6])
                        + (", ..." if td.n_cols > 6 else "")
                        + (" (uncertain)" if td.uncertain else ""),
                table_data=td,
            )
        return VarRecord(
            name=name, matlab_class="table",
            shape="(unresolved)", dtype="-", nbytes=nbytes,
            summary="MATLAB table metadata found, but column data could not be decoded",
            advanced=f"HDF5 dtype={dset.dtype}, shape={tuple(dset.shape)}",
        )

    # Non-primitive MATLAB classes: opaque metadata header, not user data.
    if matlab_class not in KNOWN_MATLAB_CLASSES:
        return VarRecord(
            name=name, matlab_class=matlab_class, shape="(opaque)",
            dtype="-", nbytes=nbytes,
            summary=f"(MATLAB {matlab_class}, not expanded)",
        )

    is_sparse = "MATLAB_sparse" in dset.attrs
    shape_h5 = tuple(dset.shape)
    shape_mat = tuple(reversed(shape_h5)) if len(shape_h5) > 1 else shape_h5
    dtype = str(dset.dtype)

    summary = ""
    preview_html = ""
    try:
        if matlab_class == "char":
            # MATLAB char in v7.3: uint16 array, UTF-16LE codepoints
            if dset.dtype == np.uint16:
                s = _decode_chars(dset)
                summary = f'"{s[:80]}{"…" if len(s) > 80 else ""}"'
                preview_html = f"<pre>{html.escape(s[:1000])}</pre>"
            else:
                summary = "(char, non-uint16 encoding)"
        elif matlab_class == "logical":
            summary = "logical"
        elif matlab_class == "cell":
            summary = f"{int(dset.size)} cell(s)"
        elif is_sparse:
            summary = "sparse"
        elif dset.dtype.kind in ("i", "u", "f", "c"):
            summary, values = _numeric_summary_and_preview(dset)
            preview_html = _numeric_preview_html(shape_str(shape_mat), dtype, summary, values)
    except Exception as e:
        summary = f"<summary error: {e}>"

    return VarRecord(
        name=name,
        matlab_class=matlab_class,
        shape=shape_str(shape_mat),
        dtype=dtype,
        nbytes=nbytes,
        summary=summary,
        advanced=f"HDF5 dtype={dset.dtype}, shape={tuple(dset.shape)}",
        preview_html=preview_html,
    )


def _h5_group_record(name: str, grp, expandable: bool) -> VarRecord:
    matlab_class = _attr_str(grp, "MATLAB_class") or "struct"
    members = [k for k in grp.keys() if k not in H5_SKIP_KEYS]
    tail = f", … (+{len(members) - 8})" if len(members) > 8 else ""
    field_list = ", ".join(members[:8]) + tail if members else "(empty)"
    summary = "expanded below" if expandable and members else f"fields: {field_list}"
    return VarRecord(
        name=name,
        matlab_class=matlab_class,
        shape=f"{len(members)} field(s)",
        dtype="-",
        nbytes=0,
        summary=summary,
    )


def _h5_walk(name: str, item, recs: list, depth: int, mcos: Optional[_McosState]) -> None:
    if len(recs) >= MAX_RECORDS:
        return
    if hasattr(item, "shape"):
        recs.append(_h5_dataset_record(name, item, mcos))
        return
    matlab_class = _attr_str(item, "MATLAB_class") or "struct"
    members = [k for k in item.keys() if k not in H5_SKIP_KEYS]
    expandable = (
        matlab_class == "struct"
        and depth < MAX_STRUCT_DEPTH
        and 0 < len(members) <= 64
    )
    recs.append(_h5_group_record(name, item, expandable))
    if expandable:
        for child_name in members:
            _h5_walk(f"{name}.{child_name}", item[child_name], recs, depth + 1, mcos)


def _collect_table_k_values(h5file, mcos: _McosState) -> set:
    """First-pass walk: enumerate every dataset with MATLAB_class='table' and
    record its k discriminator. Needed to rank-map k → block before decode."""
    import h5py
    ks: set = set()

    def visit(name, obj):
        if not isinstance(obj, h5py.Dataset):
            return
        if _attr_str(obj, "MATLAB_class") == "table":
            k = mcos.collect_k(obj)
            if k is not None:
                ks.add(k)

    h5file.visititems(visit)
    return ks


def records_from_h5py(path: str) -> tuple[list[VarRecord], str]:
    import h5py
    header = ""
    recs: list[VarRecord] = []
    with h5py.File(path, "r") as f:
        try:
            with open(path, "rb") as raw:
                ub = raw.read(128)
                if ub.startswith(b"MATLAB"):
                    header = ub.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
        except Exception:
            pass

        mcos = _McosState(f)
        if mcos.blocks:
            mcos.finalize_ranking(_collect_table_k_values(f, mcos))

        for name in sorted(f.keys()):
            if name in H5_SKIP_KEYS:
                continue
            try:
                _h5_walk(name, f[name], recs, 0, mcos)
            except Exception as e:
                recs.append(VarRecord(name, "?", "?", "?", 0, f"<error: {e}>"))
    return recs, header


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_html(input_file: str, kind: str, header: str, records: list[VarRecord],
                error: Optional[str], load_secs: float) -> str:
    file_name = os.path.basename(input_file)
    file_size = os.path.getsize(input_file) if os.path.exists(input_file) else 0
    root_count = sum(1 for r in records if "." not in r.name)

    def fmt_value(value) -> str:
        import math
        if isinstance(value, float):
            if math.isnan(value):
                return "NaN"
            if math.isinf(value):
                return "Inf" if value > 0 else "-Inf"
            return f"{value:.6g}"
        if isinstance(value, complex):
            return f"{value.real:.6g}{value.imag:+.6g}i"
        return str(value)

    def fmt_num(value: Optional[float]) -> str:
        return "" if value is None else f"{value:.6g}"

    def semantic_type(r: VarRecord) -> str:
        return "table" if r.table_data is not None else r.matlab_class

    def semantic_size(r: VarRecord) -> str:
        if r.table_data is not None:
            return f"{r.table_data.n_rows} x {r.table_data.n_cols}"
        if r.shape == "(opaque)":
            return ""
        if r.matlab_class == "struct":
            return r.shape.replace(" field(s)", " fields")
        return r.shape

    def matlab_value(r: VarRecord) -> str:
        if r.table_data is not None:
            return f"{r.table_data.n_rows} x {r.table_data.n_cols} table"
        if r.shape == "(opaque)" or r.matlab_class == "struct":
            return ""
        if r.matlab_class in ("char", "string"):
            return r.summary
        return f"{semantic_size(r)} {semantic_type(r)}".strip()

    def item_preview(r: VarRecord) -> str:
        if r.table_data is not None:
            return render_table_preview(r.table_data)
        return r.preview_html

    def render_table_preview(td: TableData) -> str:
        visible_cols = td.columns[:MAX_RENDER_PREVIEW_COLS]
        col_head = "".join(f"<th>{html.escape(c.name)}</th>" for c in visible_cols)
        row_count = min(MAX_TABLE_PREVIEW_ROWS, max((len(c.first) for c in visible_cols), default=0))
        body_rows = []
        for row_idx in range(row_count):
            cells = []
            for col in visible_cols:
                value = col.first[row_idx] if row_idx < len(col.first) else ""
                is_numeric = isinstance(value, (int, float, complex))
                text_value = fmt_value(value)
                cls_name = "num" if is_numeric else "text-cell"
                if text_value in ("NaN", "Inf", "-Inf", ""):
                    cls_name += " muted-value"
                cls = f" class='{cls_name}'"
                cells.append(f"<td{cls}>{html.escape(text_value)}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

        col_rows = "".join(
            f"<tr><td>{html.escape(c.name)}</td>"
            f"<td>{html.escape(c.dtype)}</td>"
            f"<td class='num'>{c.n_rows}</td>"
            f"<td class='num'>{c.n_nan}</td>"
            f"<td class='num'>{html.escape(fmt_num(c.vmin))}</td>"
            f"<td class='num'>{html.escape(fmt_num(c.vmax))}</td></tr>"
            for c in td.columns
        )
        note = f"<div class='table-note'>Showing first {row_count} rows"
        if td.n_cols > len(visible_cols):
            note += f" and first {len(visible_cols)} of {td.n_cols} columns"
        note += ".</div>"
        columns_text = ", ".join(c.name for c in td.columns)

        return (
            "<div class='preview-block'>"
            f"<div class='columns-line'>{html.escape(columns_text)}</div>"
            "<h3>Data Preview</h3>"
            f"{note}"
            "<div class='table-scroll'><table class='data-table'><thead><tr>" + col_head + "</tr></thead>"
            "<tbody>" + "".join(body_rows) + "</tbody></table></div>"
            "<details class='stats-detail'><summary>Show column statistics</summary>"
            "<div class='table-scroll'><table class='data-table columns'><thead><tr>"
            "<th>Column</th><th>Type</th><th class='num'>Rows</th>"
            "<th class='num'>NaN</th><th class='num'>Min</th><th class='num'>Max</th></tr></thead>"
            f"<tbody>{col_rows}</tbody></table></div></details>"
            "</div>"
        )

    def row_summary(r: VarRecord) -> str:
        if r.table_data is not None:
            names = ", ".join(c.name for c in r.table_data.columns[:6])
            return names + ("..." if r.table_data.n_cols > 6 else "")
        if r.matlab_class == "struct":
            return "expanded below"
        if r.shape == "(opaque)":
            return "metadata only"
        return r.summary

    root_rows = []
    workspace_rows = []
    preview_cards = []
    for idx, r in enumerate(records):
        depth = r.name.count(".")
        if depth > 1:
            continue
        field_name = r.name.split(".")[-1]
        preview = item_preview(r)
        has_preview = bool(preview)
        target = f"item-{idx}" if has_preview else ""
        name_html = (
            f"<a href='#{target}'>{html.escape(field_name)}</a>" if has_preview else html.escape(field_name)
        )
        row_html = (
            f"<tr><td class='field'>{name_html}</td>"
            f"<td>{html.escape(semantic_type(r))}</td>"
            f"<td>{html.escape(semantic_size(r))}</td>"
            f"<td class='summary-text'>{html.escape(row_summary(r))}</td></tr>"
        )
        if depth == 0:
            root_rows.append(row_html)
        else:
            workspace_rows.append(row_html)
        if has_preview:
            if r.table_data is not None:
                subtitle = f"table · {r.table_data.n_rows} rows x {r.table_data.n_cols} columns"
            else:
                subtitle = f"{semantic_type(r)} · {semantic_size(r)}"
            preview_cards.append(
                f"<details id='{target}' class='item-card'>"
                f"<summary><span><strong>{html.escape(r.name)}</strong>"
                f"<small>{html.escape(subtitle)}</small></span></summary>"
                f"{preview}</details>"
            )

    root_html = (
        "<table class='root-table'><thead><tr>"
        "<th>Name</th><th>Class</th><th>Size</th><th>Summary</th>"
        "</tr></thead><tbody>" + "".join(root_rows) + "</tbody></table>"
        if root_rows else "<p class='muted'>(no root variables)</p>"
    )
    workspace_html = (
        "<table class='workspace-table'><thead><tr>"
        "<th>Name</th><th>Class</th><th>Size</th><th>Summary</th>"
        "</tr></thead><tbody>" + "".join(workspace_rows) + "</tbody></table>"
        if workspace_rows else "<p class='muted'>(no fields)</p>"
    )
    cards_html = "".join(preview_cards) if preview_cards else (
        "<div class='empty-card'>No expandable value preview is available for this file.</div>"
    )

    notice = (
        f"<div class='warn'><b>Could not parse:</b><pre>{html.escape(error)}</pre></div>"
        if error else ""
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    class_counts = {}
    for r in records:
        if "." in r.name:
            class_counts[semantic_type(r)] = class_counts.get(semantic_type(r), 0) + 1
    class_badges = "".join(
        f"<span class='badge'>{html.escape(k)} <b>{v}</b></span>"
        for k, v in sorted(class_counts.items())
    )
    kind_label = "MATLAB v7.3" if kind == "v7.3" else ("MATLAB v6/v7" if kind == "v6/v7" else "MAT-file")
    hdf5_badge = "<span class='badge'>HDF5</span>" if kind == "v7.3" else ""
    table_count = sum(1 for r in records if "." in r.name and semantic_type(r) == "table")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(file_name)}</title>
<style>
  :root {{ --bg:#f6f8fa; --card:#fff; --head:#f3f5f7; --line:#e1e7ef; --line2:#edf1f5; --text:#1f2937; --muted:#6b7280; --accent:#0f5f86; --soft:#f9fbfd; --stripe:#fbfcfd; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#111; --card:#181818; --head:#202020; --line:#303030; --line2:#292929; --text:#f2f5f8; --muted:#a6adb6; --accent:#8fd3ff; --soft:#151515; --stripe:#161616; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:"Segoe UI", Arial, sans-serif; font-size:14px; }}
  .page {{ max-width:1280px; margin:0 auto; padding:16px 18px 24px; }}
  .hero {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
  h1 {{ margin:0 0 7px; font-size:22px; line-height:1.2; font-weight:650; letter-spacing:0; }}
  .meta {{ color:var(--muted); display:flex; flex-wrap:wrap; gap:8px 14px; font-size:13px; }}
  .meta span:not(:last-child)::after {{ content:"·"; margin-left:14px; color:#aab3bd; }}
  .badges {{ display:flex; flex-wrap:wrap; gap:7px; margin-top:10px; }}
  .badge {{ display:inline-flex; align-items:center; gap:5px; border:1px solid var(--line); background:var(--soft); color:var(--text); border-radius:999px; padding:3px 8px; font-size:12px; }}
  .section {{ margin-top:12px; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
  .section-title {{ padding:10px 14px; border-bottom:1px solid var(--line); font-weight:650; color:var(--text); }}
  table {{ border-collapse:separate; border-spacing:0; width:100%; }}
  th, td {{ border-bottom:1px solid var(--line2); padding:8px 12px; text-align:left; vertical-align:middle; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  th {{ position:sticky; top:0; background:var(--head); color:var(--text); font-weight:650; z-index:1; }}
  tbody tr:nth-child(even) td {{ background:var(--stripe); }}
  .root-table th:nth-child(1), .root-table td:nth-child(1),
  .workspace-table th:nth-child(1), .workspace-table td:nth-child(1) {{ width:34%; }}
  .root-table th:nth-child(2), .root-table td:nth-child(2),
  .workspace-table th:nth-child(2), .workspace-table td:nth-child(2) {{ width:13%; }}
  .root-table th:nth-child(3), .root-table td:nth-child(3),
  .workspace-table th:nth-child(3), .workspace-table td:nth-child(3) {{ width:16%; }}
  .root-table th:nth-child(4), .root-table td:nth-child(4),
  .workspace-table th:nth-child(4), .workspace-table td:nth-child(4) {{ width:37%; }}
  .field, .value, .num, code {{ font-family:Consolas, "Cascadia Mono", monospace; }}
  .field a {{ color:var(--accent); text-decoration:none; }}
  .field a:hover {{ text-decoration:underline; }}
  .value {{ color:var(--accent); font-style:italic; }}
  .summary-text {{ color:var(--muted); }}
  .details-section {{ margin-top:12px; }}
  .cards {{ display:grid; gap:8px; }}
  .item-card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
  .item-card > summary {{ cursor:pointer; list-style:none; padding:10px 14px; background:var(--card); border-bottom:1px solid transparent; }}
  .item-card > summary::-webkit-details-marker {{ display:none; }}
  .item-card > summary::before {{ content:"▸"; display:inline-block; width:18px; color:var(--muted); }}
  .item-card[open] > summary {{ border-bottom-color:var(--line); }}
  .item-card[open] > summary::before {{ content:"▾"; }}
  .item-card strong {{ font-family:Consolas, "Cascadia Mono", monospace; font-size:15px; }}
  .item-card small {{ display:block; color:var(--muted); margin:4px 0 0 18px; font-size:12px; }}
  .preview-block {{ padding:12px 14px 14px; }}
  .columns-line {{ background:var(--soft); border:1px solid var(--line2); border-radius:6px; color:var(--muted); padding:8px 10px; font-family:Consolas, "Cascadia Mono", monospace; white-space:nowrap; overflow:auto; }}
  h3 {{ margin:12px 0 7px; font-size:13px; color:var(--text); font-weight:650; }}
  .table-scroll {{ max-width:100%; overflow:auto; border:1px solid var(--line2); border-radius:6px; }}
  .data-table {{ min-width:620px; font-size:13px; }}
  .data-table th, .data-table td {{ padding:6px 9px; height:30px; }}
  .data-table th {{ background:var(--head); }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .text-cell {{ text-align:left; }}
  .muted-value {{ color:#9aa4b2; }}
  .table-note {{ color:var(--muted); font-size:12px; margin:-2px 0 7px; }}
  .stats-detail {{ margin-top:10px; }}
  .stats-detail > summary {{ cursor:pointer; color:var(--accent); font-size:13px; margin-bottom:8px; }}
  .empty-card {{ background:var(--card); border:1px dashed #cfd8e3; border-radius:8px; color:var(--muted); padding:16px; }}
  pre {{ margin:0; white-space:pre-wrap; font-family:Consolas, "Cascadia Mono", monospace; }}
  .warn {{ margin-top:16px; padding:10px 12px; background:#fff8e6; border:1px solid #edd38a; border-radius:8px; }}
  .muted {{ color:var(--muted); margin:0; padding:14px 16px; }}
</style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <h1>{html.escape(file_name)}</h1>
      <div class="meta">
        <span>{kind_label}</span>
        <span>{human_bytes(file_size)}</span>
        <span>{root_count} root variable{'s' if root_count != 1 else ''}</span>
        <span>{table_count} table{'s' if table_count != 1 else ''}</span>
        <span>load {load_secs * 1000:.0f} ms</span>
        <span>generated {generated}</span>
      </div>
      <div class="badges">
        <span class="badge">{html.escape(kind_label)}</span>
        {hdf5_badge}
        {class_badges}
      </div>
    </header>
    {notice}
    <section class="section">
      <div class="section-title">Root Variable</div>
      {root_html}
    </section>
    <section class="section">
      <div class="section-title">Workspace</div>
      {workspace_html}
    </section>
    <section class="details-section">
      <div class="cards">
        {cards_html}
      </div>
    </section>
  </main>
</body></html>
"""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 3:
        print("usage: mat_to_html.py <input.mat> <output.html>", file=sys.stderr)
        return 2

    input_file = sys.argv[1]
    output_html = sys.argv[2]

    error: Optional[str] = None
    records: list[VarRecord] = []
    header = ""
    kind = "unknown"
    t0 = time.perf_counter()

    try:
        if not os.path.exists(input_file):
            raise FileNotFoundError(input_file)
        kind = detect_mat_kind(input_file)
        if kind == "v7.3":
            records, header = records_from_h5py(input_file)
        else:
            mat = load_scipy(input_file)
            h = mat.get("__header__")
            if isinstance(h, (bytes, bytearray)):
                header = h.decode("utf-8", "replace").strip()
            records = records_from_scipy(mat)
    except ImportError as e:
        error = f"missing dependency: {e}\nrun: pip install scipy numpy h5py"
    except NotImplementedError:
        # scipy raises this for v7.3; retry through h5py
        try:
            kind = "v7.3"
            records, header = records_from_h5py(input_file)
        except Exception:
            error = traceback.format_exc()
    except Exception:
        error = traceback.format_exc()

    page = render_html(input_file, kind, header, records, error, time.perf_counter() - t0)
    with open(output_html, "w", encoding="utf-8-sig") as f:
        f.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
