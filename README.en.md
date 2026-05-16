<div align="center">

# SeerMat

**A MATLAB `.mat` / `.fig` quick preview plugin for [Seer](https://1218.io/).**

[中文](README.md) · [繁體中文](README.zh-TW.md) · [English](README.en.md)

![release](https://img.shields.io/badge/release-v1.1.0-blue)
![platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Seer](https://img.shields.io/badge/Seer-4.1.3%2B-0b78d0)
![Python](https://img.shields.io/badge/Python-3.x-3776ab)
![formats](https://img.shields.io/badge/formats-.mat%20%7C%20.fig-22a06b)
![license](https://img.shields.io/badge/license-GPL--3.0-green)

</div>

SeerMat lets you preview MATLAB files directly from File Explorer without opening MATLAB. It focuses on the information you usually need first: variable structure, struct fields, table sizes, table column names, and rendered `.fig` images.

## Screenshots

MAT-file workspace preview:

![MAT-file preview](assets/mat-preview.png)

FIG image preview:

![FIG preview](assets/fig-preview.png)

## Features

- Preview MATLAB `.mat` and `.fig` files in [Seer](https://1218.io/).
- Supports classic MATLAB v6/v7 MAT-files through `scipy`.
- Supports MATLAB v7.3 HDF5 MAT-files through `h5py`.
- Restores MATLAB semantics where possible instead of exposing raw HDF5 reference values.
- Shows `struct` values as a field tree.
- Shows v7.3 `table` values with real row and column counts, expandable as column rows.
- Shows lightweight previews for simple numeric / char / string variables.
- Renders `.fig` files to an image when MATLAB is available.
- Caches rendered `.fig` images so repeated previews of unchanged files are much faster.
- Follows the system light/dark theme.

## Requirements

- Windows
- [Seer](https://1218.io/) `4.1.3` or newer
- Python 3 available on `PATH`
- Python packages:

```powershell
pip install numpy scipy h5py
```

Optional:

- MATLAB available on `PATH`; required only for rendered `.fig` image previews.

## Installation

1. Download or clone this repository.
2. Install Python dependencies:

```powershell
pip install numpy scipy h5py
```

3. Add this project's `plugin.json` in Seer's plugin manager.
4. Restart Seer.
5. Select a `.mat` or `.fig` file in File Explorer and press the Seer preview hotkey.

## Cache Notes

After updating the plugin, restart Seer. If the same `.mat` or `.fig` file still shows an old layout, clear Seer's temporary HTML cache:

```powershell
Get-ChildItem "$env:TEMP\Seer" -Filter *.html -ErrorAction SilentlyContinue | Remove-Item -Force
```

Rendered `.fig` PNG files are cached under:

```text
%TEMP%\SeerMat\fig-cache
```

If the `.fig` file size or modified time changes, SeerMat renders it again automatically.

## Limitations

- SeerMat is designed for quick preview, not full MAT-file conversion.
- Very large or deeply nested objects are summarized to avoid freezing Seer.
- MATLAB opaque objects, custom classes, and old table internals may only show partial metadata.
- `.fig` image rendering requires MATLAB; if MATLAB is unavailable, SeerMat falls back to the stored graphics-object structure.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

## Acknowledgements

Thanks to [Seer](https://1218.io/) for providing the lightweight quick preview platform that makes this MATLAB file preview plugin possible.
