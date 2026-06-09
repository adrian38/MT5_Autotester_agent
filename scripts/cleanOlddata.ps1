Write-Host "========================================="
Write-Host "RESET TOTAL MT4 / MT5"
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
$terminalFolders = Get-ChildItem $basePath -Directory

foreach ($folder in $terminalFolders) {

    $terminalPath = $folder.FullName
    Write-Host ""
    Write-Host "Procesando: $terminalPath"

    $testerPath = Join-Path $terminalPath "tester"
    if (Test-Path $testerPath) {
        Remove-Item $testerPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "tester eliminado"
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
    Write-Host "reports eliminados"

    $basesPath = Join-Path $terminalPath "bases"
    if (Test-Path $basesPath) {
        Remove-Item $basesPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "bases eliminado"
    }

    $historyPath = Join-Path $terminalPath "history"
    if (Test-Path $historyPath) {
        Remove-Item $historyPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "history eliminado"
    }

    Get-ChildItem $terminalPath -Recurse -Include *.fxt,*.tick -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host "Terminal limpiada"
}

Write-Host ""
Write-Host "========================================="
Write-Host "LIMPIEZA COMPLETADA"
Write-Host "========================================="
