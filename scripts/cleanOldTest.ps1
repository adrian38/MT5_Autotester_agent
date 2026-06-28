Write-Host "========================================="
Write-Host "LIMPIEZA GLOBAL META TRADER TESTER"
Write-Host "========================================="

Write-Host "Cerrando MetaTrader y agentes Tester..."
foreach ($pattern in @("terminal*", "metatester*", "metaeditor*")) {
    Get-Process $pattern -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

$basePath = Join-Path $env:APPDATA "MetaQuotes\Terminal"
$globalTesterPath = Join-Path $env:APPDATA "MetaQuotes\Tester"

if (!(Test-Path $basePath)) {
    Write-Host "No se encontro carpeta MetaQuotes."
    exit
}

$reportPatterns = @("*.htm", "*.html", "*.xml", "*.png", "*.gif", "*.set")

Get-ChildItem $basePath -Directory | ForEach-Object {

    $terminalPath = $_.FullName
    Write-Host ""
    Write-Host "Procesando terminal: $terminalPath"

    $testerPath = Join-Path $terminalPath "tester"
    if (Test-Path $testerPath) {
        Remove-Item $testerPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Carpeta tester eliminada"
    }

    $reportDirs = @(
        $terminalPath,
        (Join-Path $terminalPath "Reports"),
        (Join-Path $terminalPath "MQL5\Files")
    )
    foreach ($reportDir in $reportDirs) {
        if (Test-Path $reportDir) {
            foreach ($pattern in $reportPatterns) {
                Get-ChildItem -Path (Join-Path $reportDir $pattern) -File -ErrorAction SilentlyContinue |
                    Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Write-Host "Reportes eliminados"

    $basesPath = Join-Path $terminalPath "bases"
    if (Test-Path $basesPath) {
        Get-ChildItem $basesPath -Recurse -Include *.fxt,*.tick -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Host "Archivos tester eliminados en bases"
    }

    Get-ChildItem $terminalPath -Recurse -Include *.fxt -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host "Limpieza completada en esta terminal"
}

if (Test-Path $globalTesterPath) {
    Remove-Item $globalTesterPath -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "MetaQuotes\Tester eliminado"
}

Write-Host ""
Write-Host "========================================="
Write-Host "LIMPIEZA COMPLETADA EN TODAS LAS TERMINALES"
Write-Host "========================================="
