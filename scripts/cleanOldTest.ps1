Write-Host "========================================="
Write-Host "LIMPIEZA GLOBAL META TRADER TESTER"
Write-Host "========================================="

Write-Host "Cerrando MetaTrader..."
Get-Process terminal* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

$basePath = Join-Path $env:APPDATA "MetaQuotes\Terminal"

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

Write-Host ""
Write-Host "========================================="
Write-Host "LIMPIEZA COMPLETADA EN TODAS LAS TERMINALES"
Write-Host "========================================="
