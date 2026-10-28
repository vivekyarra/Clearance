<#
Submission proof from real runs (wrapper for scripts/release_proof.py).
  powershell -ExecutionPolicy Bypass -File scripts\release_proof.ps1 [--sites ref_cafe,ref_storeroom,ref_corridor]
      [--require-provider token_factory] [--fresh-usage] [--skip-checks] [--force] [--tavily-mode live|offline]
Python: $env:PYTHON, else $env:CLEARANCE_VENV\Scripts\python.exe, else C:\Users\<you>\.venvs\pg\Scripts\python.exe.
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @())
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = $env:PYTHON
if (-not $Py -and $env:CLEARANCE_VENV) { $Py = Join-Path $env:CLEARANCE_VENV "Scripts\python.exe" }
if (-not $Py) {
    $default = Join-Path $env:USERPROFILE ".venvs\pg\Scripts\python.exe"
    $Py = if (Test-Path $default) { $default } else { "python" }
}
$env:PYTHONPATH = $Root
& $Py (Join-Path $Root "scripts\release_proof.py") @Rest
exit $LASTEXITCODE
