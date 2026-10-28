<#
Clearance developer tasks (Makefile equivalent) for Windows PowerShell 5.1+.
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 <task> [args]
Tasks: setup | test | test-web | build-web | api | web | dev | verify-models | docker-build | compose-up | ci
Python: $env:CLEARANCE_VENV (venv folder) or $env:PYTHON, default C:\Users\<you>\.venvs\pg\Scripts\python.exe if present.
Runs in offline mode unless CLEARANCE_LLM_MODE / CLEARANCE_TAVILY_MODE are set.
#>
param(
    [Parameter(Position = 0)][string]$Task = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Resolve-Python {
    if ($env:PYTHON) { return $env:PYTHON }
    if ($env:CLEARANCE_VENV) { return (Join-Path $env:CLEARANCE_VENV "Scripts\python.exe") }
    $default = Join-Path $env:USERPROFILE ".venvs\pg\Scripts\python.exe"
    if (Test-Path $default) { return $default }
    return "python"
}
$Py = Resolve-Python
$env:PYTHONPATH = $Root
if (-not $env:CLEARANCE_LLM_MODE) { $env:CLEARANCE_LLM_MODE = "offline" }
if (-not $env:CLEARANCE_TAVILY_MODE) { $env:CLEARANCE_TAVILY_MODE = "offline" }

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}
function Invoke-Web([string[]]$Arguments) {
    Push-Location (Join-Path $Root "apps\web")
    try { Invoke-Checked "npm" $Arguments } finally { Pop-Location }
}
function Invoke-WebTests {
    Push-Location (Join-Path $Root "apps\web")
    try {
        Invoke-Checked "npx" @("tsc", "--noEmit")
        Invoke-Checked "npm" @("test", "--if-present", "--", "--passWithNoTests")
    } finally { Pop-Location }
}

switch ($Task) {
    "setup" {
        Invoke-Checked $Py (@("-m", "pip", "install", "-r", "requirements.txt"))
        Invoke-Web @("ci", "--no-audit", "--no-fund")
    }
    "test" { Invoke-Checked $Py (@("-m", "pytest", "-q", "tests") + $Rest) }
    "test-web" { Invoke-WebTests }
    "build-web" { Invoke-Web @("run", "build") }
    "api" {
        $port = if ($env:PORT) { $env:PORT } else { "8000" }
        Invoke-Checked $Py (@("-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", $port, "--reload", "--ws-max-size", "131072") + $Rest)
    }
    "web" { Invoke-Web @("run", "dev") }
    "dev" {
        $port = if ($env:PORT) { $env:PORT } else { "8000" }
        $api = Start-Process -FilePath $Py -ArgumentList @("-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", $port, "--reload", "--ws-max-size", "131072") -PassThru -NoNewWindow
        try { Invoke-Web @("run", "dev") } finally { if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force } }
    }
    "verify-models" { Invoke-Checked $Py (@("scripts\verify_token_factory.py") + $Rest) }
    "docker-build" {
        foreach ($t in @("api", "jobs-cpu", "jobs-gpu")) {
            Invoke-Checked "docker" @("build", "--platform", "linux/amd64", "-f", "infra/Dockerfile.$t", "-t", "clearance-${t}:local", ".")
        }
    }
    "compose-up" { Invoke-Checked "docker" (@("compose", "-f", "infra/docker-compose.yml", "up", "--build") + $Rest) }
    "ci" {
        Invoke-Checked $Py @("-m", "pytest", "-q", "tests")
        Invoke-Web @("ci", "--no-audit", "--no-fund")
        Invoke-WebTests
        Invoke-Web @("run", "build")
    }
    default { Get-Content $PSCommandPath -TotalCount 6 | Select-Object -Skip 1 }
}
