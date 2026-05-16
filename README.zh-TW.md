<div align="center">

# SeerMat

**用於 [Seer](https://1218.io/) 的 MATLAB `.mat` / `.fig` 快速預覽外掛。**

[中文](README.md) · [繁體中文](README.zh-TW.md) · [English](README.en.md)

![release](https://img.shields.io/badge/release-v1.1.0-blue)
![platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Seer](https://img.shields.io/badge/Seer-4.1.3%2B-0b78d0)
![Python](https://img.shields.io/badge/Python-3.x-3776ab)
![formats](https://img.shields.io/badge/formats-.mat%20%7C%20.fig-22a06b)
![license](https://img.shields.io/badge/license-GPL--3.0-green)

</div>

SeerMat 可以在檔案總管中直接預覽 MATLAB 檔案，不必開啟 MATLAB。它著重呈現使用者真正關心的內容：變數結構、struct 欄位、table 行列資訊、table 欄位名稱，以及 `.fig` 圖像預覽。

## 預覽效果

MAT 檔案變數結構：

![MAT-file preview](assets/mat-preview.png)

FIG 圖像預覽：

![FIG preview](assets/fig-preview.png)

## 功能特色

- 支援在 [Seer](https://1218.io/) 中預覽 MATLAB `.mat` 和 `.fig` 檔案。
- 支援 MATLAB v6/v7 MAT-file，基於 `scipy` 讀取。
- 支援 MATLAB v7.3 HDF5 MAT-file，基於 `h5py` 讀取。
- 盡量還原 MATLAB 語意，而不是直接顯示 HDF5 內部引用編號。
- `struct` 顯示為欄位樹。
- v7.3 `table` 顯示真實行列數，並可展開為欄位列表。
- 普通 numeric / char / string 變數可顯示輕量資料預覽。
- `.fig` 檔案在 MATLAB 可用時渲染為圖片預覽。
- `.fig` 渲染結果會快取，重複預覽同一個未修改檔案會更快。
- 支援系統淺色 / 深色主題。

## 依賴

- Windows
- [Seer](https://1218.io/) `4.1.3` 或更新版本
- Python 3，需要在 `PATH` 中可用
- Python 套件：

```powershell
pip install numpy scipy h5py
```

可選：

- MATLAB，需要在 `PATH` 中可用；只有渲染 `.fig` 圖片預覽時需要。

## 安裝

1. 下載或 clone 本倉庫。
2. 安裝 Python 依賴：

```powershell
pip install numpy scipy h5py
```

3. 在 Seer 外掛管理中新增本專案的 `plugin.json`。
4. 重新啟動 Seer。
5. 在檔案總管中選取 `.mat` 或 `.fig` 檔案，按下 Seer 預覽快捷鍵。

## 快取說明

更新外掛後，請先重新啟動 Seer。如果同一個 `.mat` 或 `.fig` 檔案仍顯示舊介面，可以清理 Seer 的暫存 HTML：

```powershell
Get-ChildItem "$env:TEMP\Seer" -Filter *.html -ErrorAction SilentlyContinue | Remove-Item -Force
```

`.fig` 渲染出的 PNG 圖片會快取在：

```text
%TEMP%\SeerMat\fig-cache
```

如果 `.fig` 檔案大小或修改時間改變，外掛會自動重新渲染。

## 限制

- 外掛面向快速預覽，不是完整 MAT-file 轉換器。
- 非常大或層級很深的物件會被摘要化，避免 Seer 卡住。
- MATLAB opaque 物件、自訂類別和舊式 table 內部結構可能只能顯示部分中繼資料。
- `.fig` 圖片渲染需要 MATLAB；如果 MATLAB 不可用，會回退到圖形物件結構預覽。

## 授權

本專案採用 [GNU General Public License v3.0](LICENSE) 授權。

## 致謝

感謝 [Seer](https://1218.io/) 提供輕量、快速的檔案預覽平台，讓這個 MATLAB 檔案預覽外掛成為可能。
