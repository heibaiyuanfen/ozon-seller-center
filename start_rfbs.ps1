param([switch]$Test)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDirectory = Get-ChildItem -LiteralPath $projectRoot -Directory |
    Where-Object { $_.Name -like "RFBS*" -and (Test-Path -LiteralPath (Join-Path $_.FullName "main.py")) } |
    Select-Object -First 1

if (-not $appDirectory) {
    throw "Cannot find the RFBS application directory under: $projectRoot"
}

$pythonPath = ""
foreach ($commandName in @("python", "py")) {
    $candidate = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $candidate) {
        continue
    }
    try {
        & $candidate.Source -B -c "import sys; print(sys.executable)" *> $null
        $candidateUsable = $LASTEXITCODE -eq 0
    } catch {
        $candidateUsable = $false
    }
    if ($candidateUsable) {
        $pythonPath = $candidate.Source
        break
    }
}

$bundledPythonRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python"
if (-not $pythonPath -and (Test-Path -LiteralPath (Join-Path $bundledPythonRoot "python.exe"))) {
    $pythonPath = Join-Path $bundledPythonRoot "python.exe"
    $env:TCL_LIBRARY = Join-Path $bundledPythonRoot "tcl\tcl8.6"
    $env:TK_LIBRARY = Join-Path $bundledPythonRoot "tcl\tk8.6"
}
if (-not $pythonPath) {
    throw "Python was not found. Install Python 3.10 or newer first."
}

$runtimeDependencies = Join-Path $appDirectory.FullName ".runtime-deps"
if (Test-Path -LiteralPath $runtimeDependencies) {
    $env:PYTHONPATH = if ($env:PYTHONPATH) {
        $runtimeDependencies + [IO.Path]::PathSeparator + $env:PYTHONPATH
    } else {
        $runtimeDependencies
    }
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
