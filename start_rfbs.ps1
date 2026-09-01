param([switch]$Test)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDirectory = Get-ChildItem -LiteralPath $projectRoot -Directory |
    Where-Object { $_.Name -like "RFBS*" -and (Test-Path -LiteralPath (Join-Path $_.FullName "main.py")) } |
    Select-Object -First 1

if (-not $appDirectory) {
    throw "Cannot find the RFBS application directory under: $projectRoot"
}

$bundledPythonRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python"
$bundledPython = Join-Path $bundledPythonRoot "python.exe"
$pythonCandidates = @()
$projectPython = Join-Path $projectRoot ".python\python.exe"
if (Test-Path -LiteralPath $projectPython) {
    $pythonCandidates += $projectPython
}
if (Test-Path -LiteralPath $bundledPython) {
    $pythonCandidates += $bundledPython
}
foreach ($commandName in @("python", "py")) {
    $candidate = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) {
        $pythonCandidates += $candidate.Source
    }
}

$pythonPath = ""
foreach ($candidatePath in ($pythonCandidates | Select-Object -Unique)) {
    if ($candidatePath -eq $bundledPython) {
        $projectTclRoot = Join-Path $projectRoot ".tcl"
        $env:TCL_LIBRARY = Join-Path $projectTclRoot "tcl8.6"
        $env:TK_LIBRARY = Join-Path $projectTclRoot "tk8.6"
    } else {
        Remove-Item Env:TCL_LIBRARY -ErrorAction SilentlyContinue
        Remove-Item Env:TK_LIBRARY -ErrorAction SilentlyContinue
    }
    try {
        & $candidatePath -B -c "import tkinter as tk; r=tk.Tk(); r.withdraw(); r.destroy()" *> $null
        $candidateUsable = $LASTEXITCODE -eq 0
    } catch {
        $candidateUsable = $false
    }
    if ($candidateUsable) {
        $pythonPath = $candidatePath
        break
    }
}
if (-not $pythonPath) {
    throw "Python 3.10+ with Tkinter was not found. Install Python from python.org and include Tcl/Tk support."
}

$pythonTag = (& $pythonPath -B -c "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')").Trim()
$runtimeDependencies = Join-Path $appDirectory.FullName (".runtime-deps-" + $pythonTag)
New-Item -ItemType Directory -Path $runtimeDependencies -Force | Out-Null
$env:PYTHONPATH = if ($env:PYTHONPATH) {
    $runtimeDependencies + [IO.Path]::PathSeparator + $env:PYTHONPATH
} else {
    $runtimeDependencies
}

& $pythonPath -B -c "import aiohttp, alibabacloud_oss_v2, openpyxl, pandas, PIL, playwright, requests" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "First launch: installing Python dependencies..." -ForegroundColor Cyan
    & $pythonPath -m pip install --target $runtimeDependencies -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Check the network and run this launcher again."
    }
}

if (Test-Path -LiteralPath $runtimeDependencies) {
    $env:PATH = $runtimeDependencies + [IO.Path]::PathSeparator + $env:PATH
}

if ($Test) {
    & $pythonPath -B -m unittest discover -s (Join-Path $appDirectory.FullName "tests") -v
} else {
    & $pythonPath (Join-Path $appDirectory.FullName "main.py")
}

if ($LASTEXITCODE -ne 0) {
    Read-Host "Program exited with an error. Press Enter to close"
    exit $LASTEXITCODE
}
