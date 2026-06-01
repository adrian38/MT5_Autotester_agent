# MT5 Autotester

Ejecuta backtests de RoboForex MetaTrader 5 en serie usando un `.ini` general y la lista de Expert Advisors de `experts_list.txt`.

## Estructura

```text
D:\TRADING\MT5_Autotester
|-- run_tests.py
|-- tester_template.ini
|-- experts_list.txt
|-- configs\
|-- logs\
`-- reports\
```

## Configuracion general

Edita `tester_template.ini` para cambiar los parametros comunes de todos los backtests:

```ini
[Tester]
Symbol=XAUUSD
Period=M1
Model=1
FromDate=2024.01.01
ToDate=2025.12.31
Deposit=10000
Currency=USD
Leverage=1:500
Optimization=0
Visual=0
ReplaceReport=1
ShutdownTerminal=1
```

El script rellena automaticamente `Expert` y `Report` para cada estrategia de `experts_list.txt`. En este terminal RoboForex, `Model=1` corresponde a `OHLC en M1`.

## Rutas del terminal MT5

Puedes dejar las rutas del terminal configuradas para todo el proyecto en `.env`:

```text
MT5_TERMINAL_PATH=C:\Program Files\RoboForex MT5 Terminal\terminal64.exe
# MT5_METAEDITOR_PATH=C:\Program Files\RoboForex MT5 Terminal\MetaEditor64.exe
```

Tambien puedes definir esas mismas variables como variables de entorno de Windows. Los scripts usan este orden de prioridad:

1. Parametros de consola como `--mt5-path` o `--metaeditor-path`.
2. Variables de entorno de Windows.
3. Variables del archivo `.env` del proyecto.
4. Rutas estandar de RoboForex/MetaTrader.

`MT5_METAEDITOR_PATH` es opcional si `MetaEditor64.exe` esta en la misma carpeta que `terminal64.exe`, porque el compilador lo deduce desde `MT5_TERMINAL_PATH`.

## Preparar la lista de EAs

Edita `experts_list.txt` con rutas relativas a la carpeta `MQL5` de MT5:

```text
Experts\Strategy 2.7.12.ex5
Experts\Strategy 3.3.19.ex5
```

Tambien puedes saltarte `experts_list.txt` y coger todos los `.ex5` de la raiz de una carpeta:

```powershell
python .\run_tests.py --experts-dir "C:\Users\Adrian\AppData\Roaming\MetaQuotes\Terminal\2BA1EC2B1F7155D72C3AAB96B4870673\MQL5\Experts"
```

Para procesar todos los `.ex5` de esa misma carpeta, usa `--recursive`:

```powershell
python .\run_tests.py --experts-dir "C:\Users\Adrian\AppData\Roaming\MetaQuotes\Terminal\2BA1EC2B1F7155D72C3AAB96B4870673\MQL5\Experts" --recursive
```

Si no quieres pasar la ruta por consola, ponla en `experts_root.txt`:

```text
C:\Users\Adrian\AppData\Roaming\MetaQuotes\Terminal\2BA1EC2B1F7155D72C3AAB96B4870673\MQL5\Experts
```

Cuando `experts_root.txt` tiene una ruta activa, el script ignora `experts_list.txt` y usa todos los `.ex5` de esa carpeta.

## Probar sin abrir MT5

```powershell
python .\run_tests.py --dry-run
```

Esto genera los `.ini` dentro de `configs` y muestra los comandos que se ejecutarian.

Tambien puedes hacer doble clic en `dry_run.bat`.

## Ejecutar backtests

```powershell
python .\run_tests.py
```

Tambien puedes hacer doble clic en `run_backtests.bat`.

## Interfaz grafica

Puedes abrir una UI de escritorio para editar rutas, ajustar el `tester_template.ini`, compilar, ejecutar backtests y ver logs en vivo:

```powershell
python .\app_ui.py
```

Tambien puedes hacer doble clic en `run_ui.bat`. La UI usa los mismos scripts del proyecto y ejecuta en modo real; si quieres validar sin abrir MT5, usa las opciones `--dry-run` desde consola.

En la pestana `Configuracion`, `Archivo .mq5` es obligatorio cuando `Recursivo` esta apagado. Si escribes un nombre como `Strategy 1.4.14.mq5`, la UI compila solo ese archivo y el flujo completo usa su `.ex5` para el backtest. Si activas `Recursivo`, se usan solo los `.mq5` y `.ex5` encontrados directamente en `Carpeta .mq5`; no se entra en subcarpetas.

## Generar instalador

Para crear un instalador Windows con la UI y los ejecutables auxiliares empaquetados:

```powershell
.\tools\build_installer.ps1
```

El resultado queda en `dist_installer\MT5AutotesterSetup.exe`. Tambien se genera `dist_installer\MT5AutotesterPortable.zip`.

Antes de ejecutar, cierra RoboForex MT5 completamente. Si el terminal ya esta abierto, MT5 puede ignorar el `/config` y cerrarse en menos de un segundo sin lanzar el tester.

Si MT5 no esta en la ruta configurada, indica la ruta manualmente:

```powershell
python .\run_tests.py --mt5-path "C:\Program Files\RoboForex MT5 Terminal\terminal64.exe"
```

Con el `.bat`, puedes pasar parametros desde PowerShell asi:

```powershell
.\run_backtests.bat --mt5-path "C:\TuRuta\MetaTrader 5\terminal64.exe"
```

## Cambiar parametros

Para cambiar simbolo, timeframe, fechas, deposito o apalancamiento, edita `tester_template.ini`.

Si quieres usar otro `.ini` general:

```powershell
python .\run_tests.py --template .\mi_config.ini
```

MT5 guarda primero los reportes en su carpeta de datos; el script los detecta y los copia a `reports`. MT5 se cierra automaticamente al terminar cada backtest con `ShutdownTerminal=1`.

## Compilar .mq5 a .ex5

Para compilar todos los `.mq5` de la raiz de una carpeta, escribe la carpeta en `compile_root.txt`:

```text
C:\Users\Adrian\AppData\Roaming\MetaQuotes\Terminal\2BA1EC2B1F7155D72C3AAB96B4870673\MQL5\Experts
```

Luego ejecuta:

```powershell
python .\compile_mq5.py
```

Tambien puedes pasar la carpeta directamente:

```powershell
python .\compile_mq5.py --source-dir "C:\Users\Adrian\AppData\Roaming\MetaQuotes\Terminal\2BA1EC2B1F7155D72C3AAB96B4870673\MQL5\Experts"
```

Por defecto solo compila el archivo indicado. Para compilar todos los `.mq5` de la raiz de la carpeta:

```powershell
python .\compile_mq5.py --recursive
```

Para validar sin compilar:

```powershell
python .\compile_mq5.py --dry-run
```

Los logs quedan en `logs\compile_*.log` y `logs\last_compile.log`.

## Compilar y ejecutar backtests

Para compilar los `.mq5` de `compile_root.txt` y luego lanzar backtests sobre los `.ex5` de esa misma carpeta:

```powershell
python .\compile_and_backtest.py
```

O con doble clic:

```text
compile_and_backtest.bat
```

En otra PC puedes cambiar `.env` o sobrescribir la ruta por consola:

```powershell
python .\compile_and_backtest.py --mt5-path "C:\Users\Adrian\Documents\mt5_Orb_02\terminal64.exe"
```

Para validar el flujo completo sin compilar ni abrir MT5:

```powershell
python .\compile_and_backtest.py --dry-run
```

Para compilar y backtestear todos los EAs de la raiz de la carpeta:

```powershell
python .\compile_and_backtest.py --recursive
```

## Logs

Cada ejecucion crea un archivo en `logs`, por ejemplo:

```text
logs\run_20260426_124500.log
```

El log guarda el comando ejecutado, el contenido del `.ini`, la duracion, el codigo de salida de MT5 y si aparecio o no el reporte esperado. Si MT5 termina con codigo `0` pero no genera reporte, el script lo marca como fallo para poder diagnosticarlo.
