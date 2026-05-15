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
MAX_TABLE_PREVIEW_ROWS = 20
MAX_TABLE_PREVIEW_COLS = 40
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


def _is_opaque_struct(arr) -> bool:
    """scipy returns MATLAB opaque objects (table, datetime, …) as void
    arrays with dtype.names == ('s0','s1','s2','s3')."""
    return getattr(arr, "dtype", None) is not None and arr.dtype.names == ("s0", "s1", "s2", "s3")


def _opaque_class_from_scipy(arr) -> str:
    try:
        s2 = arr.flatten()[0]["s2"]
        if hasattr(s2, "tobytes"):
            return s2.tobytes().decode("ascii", "replace").rstrip("\x00") or "opaque"
        if isinstance(s2, (bytes, bytearray)):
            return s2.decode("ascii", "replace").rstrip("\x00") or "opaque"
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
            summary=f"(MATLAB {cls}, not expanded)",
        )
    return VarRecord(
        name=name,
        matlab_class=scipy_class_hint(v),
        shape=shape_str(getattr(v, "shape", ())),
        dtype=str(getattr(v, "dtype", type(v).__name__)),
        nbytes=int(getattr(v, "nbytes", 0)),
        summary=scipy_summary(v),
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
    import numpy as np
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
                summary=f"{td.n_rows} rows × {td.n_cols} cols"
                        + (" (uncertain)" if td.uncertain else ""),
                table_data=td,
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
    try:
        if matlab_class == "char":
            # MATLAB char in v7.3: uint16 array, UTF-16LE codepoints
            if dset.dtype == np.uint16:
                data = dset[...]
                s = data.tobytes().decode("utf-16-le", "replace").replace("\x00", "")
                summary = f'"{s[:80]}{"…" if len(s) > 80 else ""}"'
            else:
                summary = "(char, non-uint16 encoding)"
        elif matlab_class == "logical":
            summary = "logical"
        elif matlab_class == "cell":
            summary = f"{int(dset.size)} cell(s)"
        elif is_sparse:
            summary = "sparse"
        elif dset.dtype.kind in ("i", "u", "f", "c"):
            if nbytes > LARGE_SUMMARY_BYTES:
                summary = f"(>{human_bytes(LARGE_SUMMARY_BYTES)}, range skipped)"
            else:
                data = dset[...]
                if dset.dtype.kind == "c":
                    summary = f"complex, |z| ~ [{np.abs(data).min():.4g}, {np.abs(data).max():.4g}]"
                else:
                    finite = data[np.isfinite(data)] if dset.dtype.kind == "f" else data
                    if finite.size == 0:
                        summary = "all non-finite"
                    else:
                        summary = f"range [{finite.min():.6g}, {finite.max():.6g}]"
    except Exception as e:
        summary = f"<summary error: {e}>"

    return VarRecord(
        name=name,
        matlab_class=matlab_class,
        shape=shape_str(shape_mat),
        dtype=dtype,
        nbytes=nbytes,
        summary=summary,
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

    rows = "".join(
        f"<tr><td class='n'>{html.escape(r.name)}</td>"
        f"<td>{html.escape(r.matlab_class)}</td>"
        f"<td>{html.escape(r.shape)}</td>"
        f"<td>{html.escape(r.dtype)}</td>"
        f"<td class='r'>{human_bytes(r.nbytes)}</td>"
        f"<td class='s'>{html.escape(r.summary)}</td></tr>"
        for r in records
    )
    table_html = (
        "<table><thead><tr>"
        "<th>name</th><th>class</th><th>shape</th><th>dtype</th>"
        "<th class='r'>bytes</th><th>summary</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
        if records else "<p class='muted'>(no user variables)</p>"
    )

    notice = (
        f"<div class='warn'><b>Could not parse:</b><pre>{html.escape(error)}</pre></div>"
        if error else ""
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(file_name)}</title>
<style>
  body {{ font-family: Consolas, "Segoe UI", monospace; background:#1e1e1e; color:#e6e6e6; padding:20px; }}
  h1 {{ margin:0 0 4px 0; color:#9cdcfe; font-size:18px; }}
  .meta {{ color:#888; font-size:12px; margin-bottom:16px; }}
  .meta b {{ color:#bbb; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ padding:6px 10px; border-bottom:1px solid #333; text-align:left; vertical-align:top; }}
  thead th {{ position:sticky; top:0; background:#252526; color:#9cdcfe; border-bottom:1px solid #444; }}
  tbody tr:nth-child(even) {{ background:#252526; }}
  td.n {{ color:#dcdcaa; }}
  td.r, th.r {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.s {{ color:#b5cea8; }}
  .warn {{ background:#3a2a1f; color:#ffb86b; padding:10px 12px; border-radius:6px; margin:12px 0; font-size:13px; }}
  .warn pre {{ margin:6px 0 0 0; white-space:pre-wrap; color:#e6e6e6; }}
  .muted {{ color:#888; }}
</style></head>
<body>
  <h1>{html.escape(file_name)}</h1>
  <div class="meta">
    <b>size</b> {human_bytes(file_size)} &nbsp;·&nbsp;
    <b>mat</b> {html.escape(kind)} &nbsp;·&nbsp;
    <b>load</b> {load_secs*1000:.0f} ms &nbsp;·&nbsp;
    <b>vars</b> {len(records)} &nbsp;·&nbsp;
    <b>generated</b> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    {('<br><b>header</b> ' + html.escape(header)) if header else ''}
  </div>
  {notice}
  {table_html}
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
