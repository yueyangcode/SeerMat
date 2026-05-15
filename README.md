# SeerMat

SeerMat is a [Seer](https://1218.io/) plugin for previewing MATLAB `.mat` files as an HTML variable summary.

It is useful when you want to quickly inspect a `.mat` file from File Explorer without opening MATLAB: variable names, MATLAB classes, shapes, dtypes, byte sizes, simple numeric ranges, struct fields, and selected table previews are rendered into a compact dark themed page.

## Features

- Preview MATLAB `.mat` files in Seer.
- Supports classic MATLAB v6/v7 MAT-files through `scipy`.
- Supports MATLAB v7.3 HDF5 MAT-files through `h5py`.
- Expands small scalar structs recursively.
- Shows quick summaries for numeric, complex, char, cell, struct, and logical values.
- Attempts to decode MATLAB table-like data in v7.3 files.
- Writes readable error pages when Python or a dependency is missing.

## Requirements

- Windows
- Seer `4.1.3` or newer
- Python 3 available on `PATH`
- Python packages:

```powershell
pip install numpy scipy h5py
```

If `python` is not available, the plugin will also try the Windows `py -3` launcher.

## Installation

1. Download or clone this repository.
2. Make sure Python dependencies are installed:

```powershell
pip install numpy scipy h5py
```

3. Copy the plugin folder into your Seer plugins directory, or install it through Seer's plugin manager if you package it as a Seer plugin archive.
4. Restart Seer.
5. Select a `.mat` file in File Explorer and press the Seer preview hotkey.

## Files

```text
plugin.json     Seer plugin manifest
entry.ps1       PowerShell entry script called by Seer
mat_to_html.py  MAT-file parser and HTML renderer
template/       Reserved for future template assets
```

## How It Works

Seer calls `entry.ps1` with the input `.mat` file and target output path. The script locates Python, runs `mat_to_html.py`, and writes an HTML file for Seer to display.

`mat_to_html.py` detects the MAT-file type:

- v6/v7 files are loaded with `scipy.io.loadmat`.
- v7.3 files are read with `h5py`.

The renderer produces a single self-contained HTML page, so no extra frontend build step is required.

## Development

You can test the converter directly:

```powershell
python .\mat_to_html.py path\to\input.mat preview.html
```

Then open `preview.html` in a browser to inspect the generated preview.

## Limitations

- Very large or deeply nested objects are summarized instead of fully expanded.
- MATLAB opaque objects, custom classes, and some table internals may only show partial metadata.
- The plugin is designed for quick preview, not full MAT-file conversion.

## License

No license has been specified yet. Add one before distributing the plugin publicly.
