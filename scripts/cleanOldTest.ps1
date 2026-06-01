Write-Host "========================================="
Write-Host "LIMPIEZA GLOBAL META TRADER TESTER"
Write-Host "========================================="

# 1️⃣ Cerrar todas las instancias de MT4 y MT5
Write-Host "Cerrando MetaTrader..."
Get-Process terminal* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# 2️⃣ Ruta base donde están todas las terminales
$basePath = Join-Path $env:APPDATA "MetaQuotes\Terminal"

if (!(Test-Path $basePath)) {
    Write-Host "No se encontró carpeta MetaQuotes."
    exit
}

# 3️⃣ Recorrer todas las carpetas de terminal
Get-ChildItem $basePath -Directory | ForEach-Object {

    $terminalPath = $_.FullName
    Write-Host ""
    Write-Host "Procesando terminal: $terminalPath"

    # 🔹 Borrar carpeta tester si existe
    $testerPath = Join-Path $terminalPath "tester"
    if (Test-Path $testerPath) {
        Remove-Item $testerPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Carpeta tester eliminada"
    }

    # 🔹 Borrar bases tester MT5
    $basesPath = Join-Path $terminalPath "bases"
    if (Test-Path $basesPath) {
        Get-ChildItem $basesPath -Recurse -Include *.fxt,*.tick -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Host "Archivos tester eliminados en bases"
    }

    # 🔹 Borrar .fxt dentro de la terminal
    Get-ChildItem $terminalPath -Recurse -Include *.fxt -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host "Limpieza completada en esta terminal"
}

Write-Host ""
Write-Host "========================================="
Write-Host "LIMPIEZA COMPLETADA EN TODAS LAS TERMINALES"
Write-Host "========================================="
