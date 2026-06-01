Write-Host "========================================="
Write-Host "RESET TOTAL MT4 / MT5"
Write-Host "========================================="

# Cerrar MetaTrader
Write-Host "Cerrando MetaTrader..."
Get-Process terminal* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

$basePath = Join-Path $env:APPDATA "MetaQuotes\Terminal"

if (!(Test-Path $basePath)) {
    Write-Host "No se encontró carpeta MetaQuotes."
    exit
}

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
