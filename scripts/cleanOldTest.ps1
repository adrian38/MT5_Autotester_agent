param(
    [string]$DataDirs = "",
    [string]$TerminalPaths = ""
)

# Las carpetas de datos y los ejecutables de ESTE agente, separados por '|', tal
# y como los pasa `build_historical_cleanup_command` desde el `ui_settings.ini`.
#
# El criterio viejo recorria %APPDATA%\MetaQuotes\Terminal entero, asi que cada
# agente entraba en las terminales de los demas: el 2026-11-17 la limpieza de IC
# borro la cache del Tester de AXI y fallo al no poder vaciar sus `bases`, que
# AXI tenia abiertos desde otra sesion de Windows. Ver
# ai_context/historical_cleanup_cards.md.
$dataDirs = @($DataDirs -split '\|' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$terminalPaths = @($TerminalPaths -split '\|' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

Write-Host "========================================="
Write-Host "LIMPIEZA DEL TESTER DE ESTE AGENTE"
Write-Host "========================================="

if (-not $dataDirs) {
    Write-Host "ERROR: no se recibio ninguna carpeta de datos (-DataDirs); la limpieza no sabe que borrar."
    exit 1
}

function Remove-Tree([string]$path, [string]$label) {
    if (-not (Test-Path $path)) { return }
    Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue -ErrorVariable failures
    if ($failures) {
        Write-Host "$label NO se pudo borrar del todo: $($failures[0].Exception.Message)"
    } else {
        Write-Host "$label eliminado"
    }
}

function Get-TesterCache([string]$dataDir) {
    $terminalRoot = Split-Path -Parent $dataDir
    if ((Split-Path -Leaf $terminalRoot) -ine "Terminal") { return "" }
    $metaquotes = Split-Path -Parent $terminalRoot
    return (Join-Path (Join-Path $metaquotes "Tester") (Split-Path -Leaf $dataDir))
}

# Solo se cierran los procesos que salen de las instalaciones de este agente.
# Un proceso de otra sesion de Windows no deja leer su `Path`, y ese es
# justamente el que no es nuestro: se omite en vez de intentar matarlo.
$installRoots = @(
    $terminalPaths | ForEach-Object { Split-Path -Parent $_ } | Where-Object { $_ }
) | Select-Object -Unique

Write-Host "Cerrando MetaTrader y agentes Tester de este agente..."
foreach ($process in @(Get-Process terminal*, metatester*, metaeditor* -ErrorAction SilentlyContinue)) {
    $path = ""
    try { $path = [string]$process.Path } catch { $path = "" }
    if (-not $path) { continue }
    foreach ($root in $installRoots) {
        if ($path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Host "  cerrando $($process.ProcessName) ($($process.Id))"
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            break
        }
    }
}
Start-Sleep -Seconds 2

$reportPatterns = @("*.htm", "*.html", "*.xml", "*.png", "*.gif", "*.set")

foreach ($dataDir in $dataDirs) {

    Write-Host ""
    Write-Host "Procesando terminal: $dataDir"

    if (!(Test-Path $dataDir)) {
        Write-Host "No existe esa carpeta de datos; se omite."
        continue
    }

    Remove-Tree (Join-Path $dataDir "tester") "Carpeta tester"

    $reportDirs = @(
        $dataDir,
        (Join-Path $dataDir "Reports"),
        (Join-Path $dataDir "MQL5\Files")
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

    $basesPath = Join-Path $dataDir "bases"
    if (Test-Path $basesPath) {
        Get-ChildItem $basesPath -Recurse -Include *.fxt,*.tick -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Host "Archivos tester eliminados en bases"
    }

    Get-ChildItem $dataDir -Recurse -Include *.fxt -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $testerCache = Get-TesterCache $dataDir
    if ($testerCache) {
        Remove-Tree $testerCache "Cache del Tester"
    }

    Write-Host "Limpieza completada en esta terminal"
}

Write-Host ""
Write-Host "========================================="
Write-Host "LIMPIEZA COMPLETADA EN LAS TERMINALES DEL AGENTE"
Write-Host "========================================="
