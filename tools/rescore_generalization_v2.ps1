$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"),
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
)
$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    throw "Python no encontrado. Instala Python o ejecuta tools/rescore_generalization_v2.py con un interprete valido."
}

& $python (Join-Path $PSScriptRoot "rescore_generalization_v2.py") @args
exit $LASTEXITCODE
