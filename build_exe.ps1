param(
    [switch]$SkipTests,
    [switch]$SkipPackagedSmokeTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolRoot = Join-Path $ProjectRoot "RFBS上品工具"
$BuildRoot = Join-Path $ProjectRoot "build\pyinstaller"
$DistRoot = Join-Path $ProjectRoot "发布"
$ReleaseRoot = Join-Path $DistRoot "Ozon_RFBS上品工具"
$ArchivePath = Join-Path $DistRoot "Ozon_RFBS上品工具-Windows-x64.zip"
$PreservedDataRoot = Join-Path $ProjectRoot "build\preserved-program-data"
$SpecPath = Join-Path $ToolRoot "Ozon_RFBS上品工具.spec"
$RuntimeDependencies = Join-Path $ToolRoot ".runtime-deps"

$PythonPath = ""
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
        $PythonPath = $candidate.Source
        break
    }
}

$BundledPythonRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python"
if (-not $PythonPath -and (Test-Path -LiteralPath (Join-Path $BundledPythonRoot "python.exe"))) {
    $PythonPath = Join-Path $BundledPythonRoot "python.exe"
    $env:TCL_LIBRARY = Join-Path $BundledPythonRoot "tcl\tcl8.6"
    $env:TK_LIBRARY = Join-Path $BundledPythonRoot "tcl\tk8.6"
}
if (-not $PythonPath) {
    throw "Python was not found. Install Python 3.10 or newer first."
}
$SystemSitePackages = (& $PythonPath -B -c "import site; print(site.getsitepackages()[-1])" | Select-Object -Last 1).Trim()
if (Test-Path -LiteralPath $RuntimeDependencies) {
    # Prefer the interpreter's own packaging module. The vendored dependency
    # directory can contain a newer packaging build that is incompatible with
    # the local Python regex engine, while PyInstaller itself still comes from
    # the vendored directory.
    $DependencyPaths = @($SystemSitePackages, $RuntimeDependencies) | Where-Object { $_ }
    if ($env:PYTHONPATH) {
        $DependencyPaths += $env:PYTHONPATH
    }
    $env:PYTHONPATH = $DependencyPaths -join [IO.Path]::PathSeparator
}

if (-not $SkipTests) {
    & $PythonPath -B -m unittest discover -s (Join-Path $ToolRoot "tests")
    if ($LASTEXITCODE -ne 0) {
        throw "测试未通过，已停止打包。"
    }
}

# PyInstaller replaces the release directory. Preserve the user's local
# configuration, task checkpoints and ledger across rebuilds.
if (Test-Path -LiteralPath $PreservedDataRoot) {
    Remove-Item -LiteralPath $PreservedDataRoot -Recurse -Force
}
$ExistingDataRoot = Join-Path $ReleaseRoot "程序数据"
if (Test-Path -LiteralPath $ExistingDataRoot) {
    New-Item -ItemType Directory -Force -Path $PreservedDataRoot | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $ExistingDataRoot -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $PreservedDataRoot -Recurse -Force
    }
}

& $PythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $BuildRoot `
    $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败。"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ReleaseRoot "程序数据") | Out-Null
if (Test-Path -LiteralPath $PreservedDataRoot) {
    foreach ($item in Get-ChildItem -LiteralPath $PreservedDataRoot -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $ReleaseRoot "程序数据") -Recurse -Force
    }
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot "给使用者的说明.txt") -Destination $ReleaseRoot -Force

$ExecutablePath = Join-Path $ReleaseRoot "Ozon_RFBS上品工具.exe"
if (-not $SkipPackagedSmokeTest) {
    & $ExecutablePath --packaging-smoke-test
    if ($LASTEXITCODE -ne 0) {
        throw "打包后的程序自检失败。"
    }
}

$ArchiveCreated = $false
for ($ArchiveAttempt = 1; $ArchiveAttempt -le 5; $ArchiveAttempt++) {
    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }
    try {
        Compress-Archive -LiteralPath $ReleaseRoot -DestinationPath $ArchivePath -CompressionLevel Optimal
        $ArchiveCreated = $true
        break
    } catch {
        if ($ArchiveAttempt -ge 5) {
            throw
        }
        Write-Host "ZIP source is temporarily locked; retrying in 3 seconds ($ArchiveAttempt/5)..."
        Start-Sleep -Seconds 3
    }
}
if (-not $ArchiveCreated) {
    throw "ZIP archive was not created."
}

$Executable = Get-Item -LiteralPath $ExecutablePath
$Archive = Get-Item -LiteralPath $ArchivePath
$ExecutableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExecutablePath).Hash
$ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash

Write-Host "EXE: $($Executable.FullName)"
Write-Host "EXE size: $($Executable.Length)"
Write-Host "EXE SHA256: $ExecutableHash"
Write-Host "ZIP: $($Archive.FullName)"
Write-Host "ZIP size: $($Archive.Length)"
Write-Host "ZIP SHA256: $ArchiveHash"
