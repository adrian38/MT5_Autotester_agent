param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildRoot = Join-Path $Root "build_installer"
$PyInstallerOut = Join-Path $BuildRoot "pyinstaller"
$StageDir = Join-Path $BuildRoot "stage"
$PackageDir = Join-Path $BuildRoot "package"
$OutputDir = Join-Path $Root "dist_installer"
$PayloadZip = Join-Path $PackageDir "MT5AutotesterPayload.zip"
$SetupExe = Join-Path $OutputDir "MT5AutotesterSetup.exe"
$PortableZip = Join-Path $OutputDir "MT5AutotesterPortable.zip"

function Reset-Dir([string]$Path) {
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
}

function Invoke-Native([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "El comando nativo fallo con codigo de salida $LASTEXITCODE."
    }
}

Reset-Dir $BuildRoot
New-Item -ItemType Directory -Path $PyInstallerOut, $StageDir, $PackageDir, $OutputDir -Force | Out-Null

$IconPath = Join-Path $Root "assets\app_icon.ico"
$IconPng = Join-Path $Root "assets\app_icon.png"

Push-Location $Root
try {
    $pyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--distpath",
        $PyInstallerOut,
        "--workpath",
        (Join-Path $BuildRoot "work"),
        "--specpath",
        (Join-Path $BuildRoot "specs")
    )
    if (Test-Path $IconPath) {
        $pyInstallerArgs += @("--icon", $IconPath)
    }

    $addIconData = @()
    if (Test-Path $IconPath) {
        $addIconData += @("--add-data", "$IconPath;.")
    }
    if (Test-Path $IconPng) {
        $addIconData += @("--add-data", "$IconPng;.")
    }

    Invoke-Native { python -m PyInstaller @pyInstallerArgs @addIconData --windowed --name MT5Autotester app_ui.py }
    Invoke-Native { python -m PyInstaller @pyInstallerArgs --console --name compile_mq5 compile_mq5.py }
    Invoke-Native { python -m PyInstaller @pyInstallerArgs --console --name run_tests run_tests.py }
    Invoke-Native { python -m PyInstaller @pyInstallerArgs --console --name compile_and_backtest compile_and_backtest.py }
}
finally {
    Pop-Location
}

Copy-Item -LiteralPath (Join-Path $PyInstallerOut "MT5Autotester.exe") -Destination $StageDir
Copy-Item -LiteralPath (Join-Path $PyInstallerOut "compile_mq5.exe") -Destination $StageDir
Copy-Item -LiteralPath (Join-Path $PyInstallerOut "run_tests.exe") -Destination $StageDir
Copy-Item -LiteralPath (Join-Path $PyInstallerOut "compile_and_backtest.exe") -Destination $StageDir

$contentFiles = @(
    "README.md",
    ".env.example",
    ".env",
    "tester_template.ini",
    "compile_root.txt",
    "experts_root.txt",
    "experts_list.txt"
)
foreach ($file in $contentFiles) {
    $source = Join-Path $Root $file
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $StageDir -Force
    }
}

if (Test-Path $IconPath) {
    Copy-Item -LiteralPath $IconPath -Destination $StageDir -Force
}
if (Test-Path $IconPng) {
    Copy-Item -LiteralPath $IconPng -Destination $StageDir -Force
}

$ScriptsSource = Join-Path $Root "scripts"
if (Test-Path $ScriptsSource) {
    $ScriptsDest = Join-Path $StageDir "scripts"
    New-Item -ItemType Directory -Path $ScriptsDest -Force | Out-Null
    Get-ChildItem -LiteralPath $ScriptsSource -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $ScriptsDest -Force
    }
}

New-Item -ItemType Directory -Path (Join-Path $StageDir "configs"), (Join-Path $StageDir "logs"), (Join-Path $StageDir "reports") | Out-Null
Set-Content -Path (Join-Path $StageDir "VERSION.txt") -Value $Version -Encoding UTF8

$uninstallScript = @'
$ErrorActionPreference = "Stop"
$AppName = "MT5 Autotester"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"
$StartMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) $AppName
Remove-Item -LiteralPath $DesktopShortcut -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StartMenuDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MT5 Autotester" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $InstallDir -Recurse -Force
'@
Set-Content -Path (Join-Path $StageDir "uninstall.ps1") -Value $uninstallScript -Encoding UTF8

Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $PayloadZip -Force
Copy-Item -LiteralPath $PayloadZip -Destination $PortableZip -Force

Push-Location $Root
try {
    $setupArgs = @(
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "MT5AutotesterSetup",
        "--distpath", $OutputDir,
        "--workpath", (Join-Path $BuildRoot "installer_work"),
        "--specpath", (Join-Path $BuildRoot "specs"),
        "--add-data", "$PayloadZip;."
    )
    if (Test-Path $IconPath) {
        $setupArgs += @("--icon", $IconPath, "--add-data", "$IconPath;.")
    }
    if (Test-Path $IconPng) {
        $setupArgs += @("--add-data", "$IconPng;.")
    }
    Invoke-Native { python -m PyInstaller @setupArgs (Join-Path $Root "tools\installer_app.py") }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Instalador generado: $SetupExe"
Write-Host "ZIP portable generado: $PortableZip"
