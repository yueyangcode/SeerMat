[CmdletBinding()]
param(
    [Alias('i')][Parameter(Mandatory = $true)][string] $InputFile,
    [Alias('o')][Parameter(Mandatory = $true)][string] $OutputFile
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding           = [System.Text.UTF8Encoding]::new($false)

$PluginDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PyScript  = Join-Path $PluginDir "mat_to_html.py"

function Write-ErrorHtml {
    param([string]$Message)
    $escaped = $Message -replace '&','&amp;' -replace '<','&lt;' -replace '>','&gt;'
    $html = @"
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>mat-preview error</title>
<style>body{font-family:Consolas,monospace;background:#1e1e1e;color:#e6e6e6;padding:24px}
h1{color:#ff7676;margin-top:0}pre{background:#2a2a2a;padding:12px;border-radius:6px;white-space:pre-wrap}</style>
</head><body><h1>mat-preview failed</h1><pre>$escaped</pre></body></html>
"@
    $outDir = Split-Path $OutputFile -Parent
    if ($outDir -and -not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir | Out-Null
    }
    Set-Content -LiteralPath $OutputFile -Value $html -Encoding UTF8
}

function Find-PythonExe {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { return @{ Exe = $p.Source; PreArgs = @() } }
    $p = Get-Command py -ErrorAction SilentlyContinue
    if ($p) { return @{ Exe = $p.Source; PreArgs = @("-3") } }
    return $null
}

function Quote-Arg([string]$s) {
    if ($s -eq "") { return '""' }
    if ($s -notmatch '[\s"]') { return $s }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $bs = 0
    foreach ($ch in $s.ToCharArray()) {
        if ($ch -eq '\') { $bs++; continue }
        if ($ch -eq '"') {
            [void]$sb.Append('\' * (2 * $bs + 1)); [void]$sb.Append('"')
        } else {
            [void]$sb.Append('\' * $bs); [void]$sb.Append($ch)
        }
        $bs = 0
    }
    [void]$sb.Append('\' * (2 * $bs))
    [void]$sb.Append('"')
    return $sb.ToString()
}

try {
    $py = Find-PythonExe
    if (-not $py) {
        Write-ErrorHtml "Python not found on PATH. Install Python 3 or add it to PATH."
        exit 0
    }

    $rest = @()
    $rest += $py.PreArgs
    $rest += $PyScript
    $rest += $InputFile
    $rest += $OutputFile

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $py.Exe
    $psi.Arguments              = ($rest | ForEach-Object { Quote-Arg $_ }) -join ' '
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardError  = $true
    $psi.RedirectStandardOutput = $true
    $psi.CreateNoWindow         = $true
    $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8

    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    $code = $proc.ExitCode

    if ($code -ne 0) {
        Write-ErrorHtml "Python exit code $code`n`nstderr:`n$stderr`n`nstdout:`n$stdout`n`ncmdline:`n$($psi.FileName) $($psi.Arguments)"
    }
    exit 0
}
catch {
    Write-ErrorHtml ($_ | Out-String)
    exit 0
}
