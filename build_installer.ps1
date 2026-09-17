param(
    [switch]$SkipExeBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildScript = Join-Path $ProjectRoot "build_exe.ps1"
$InstallerScript = Join-Path $ProjectRoot "installer.iss"
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path -LiteralPath $Iscc)) {
    $Iscc = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path -LiteralPath $Iscc)) {
    throw "Inno Setup 6 was not found."
}

if (-not $SkipExeBuild) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "The application build failed."
    }
}

& $Iscc $InstallerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed."
}

$installer = Get-ChildItem -LiteralPath $ProjectRoot -Filter "Ozon_RFBS*.exe" -File -Recurse |
    Where-Object { $_.DirectoryName -notlike "*Ozon_RFBS*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $installer) {
    throw "The installer output was not found."
}
if ($installer.Length -ge 1GB) {
    throw "Installer exceeds 1 GB: $([math]::Round($installer.Length / 1MB, 2)) MB"
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer.FullName).Hash
Write-Host "INSTALLER: $($installer.FullName)"
Write-Host "SIZE_MB: $([math]::Round($installer.Length / 1MB, 2))"
Write-Host "SHA256: $hash"
