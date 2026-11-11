# El reparto de la raíz y las trampas de mover un fichero

La regla y las dos tablas están en `AGENTS.md` («Dónde va cada cosa: la raíz es
una lista cerrada») y las hace cumplir
`tests/quality/test_repository_root.py`. Esta nota es lo otro: lo que se rompió
al mover, que ninguna tabla puede enseñar.

El 2026-11-11 y el 2026-11-12 la raíz pasó de 80 ficheros a 16. Salieron 33
módulos `ubs_agent_*.py` a `ubs/agent/`, once `run_tests_*.py` a `backtest/`,
tres `ubs_*.py` a `ubs/`, tres módulos de apoyo a `common/`, cinco `.bat` a
`scripts/`, dos plantillas a `config/` y `requirements.md` a `docs/`.

## `Path(__file__).parent` deja de ser la raíz

Cuatro veces en dos días: `backtest/base.py`, `ubs/generate_sets.py`,
`ubs/prepare_sets.py`, `ubs/set_rules_sweep.py` y `common/mt5_env.py`. Todos
calculaban su `BASE_DIR` con `.parent` y un nivel más abajo apuntaban a la
carpeta nueva. Lo que lo hace peligroso es que **no explota**: `CONFIG_DIR`,
`REPORT_DIR` o el `.env` simplemente pasan a otro sitio, y el síntoma aparece
luego como «no encuentro el informe». Al mover un módulo, buscar
`__file__` antes que los imports.

El `getattr(sys, "frozen", False)` de al lado **no** se toca: congelado manda
`sys.executable`, que sigue en la raíz de la instalación.

## Un script dentro de un paquete no se lanza por su ruta

La interfaz lanza scripts por nombre (`_run_script("ubs/set_rules_sweep.py")`),
y `python <ruta>/x.py` pone la carpeta del script en `sys.path[0]`, no la raíz:
los `from ubs.* import` del propio script dejan de resolver. `_script_command`
lanza con `-m` todo lo que no está en la raíz, apoyándose en el `cwd=BASE_DIR`
que ya pasaba. El trinquete es `tests/ui/test_app_script_commands.py`, que
además comprueba que cada nombre lanzado existe y tiene `main()`: un nombre
viejo no rompe ningún import, falla delante del usuario al pulsar el botón.

## Las guardas miden lo que su lista alcanza

`tools/source_files.py` tiene una lista de raíces propias (`OWNED_ROOTS`), y un
paquete nuevo que no esté ahí **sale del alcance de las tres guardas de código
sin que nadie lo note**: los doce módulos de `backtest/` y los cuatro de
`common/` habrían dejado de contarse. El número de ficheros que imprime
`tools.undefined_names` es la señal: si baja después de un movimiento, falta una
raíz.

## El runtime del nodo arrastra a los otros tres agentes

`manager_node_runtime/*.py` importaba `run_tests_symbols`, `run_tests_parallel`,
`run_tests_experts` y `telegram_notify` desde la raíz. Cambiar esos imports es
correcto aquí y obliga a portar el commit a AXI, RoboForex y Vantage: hasta que
el usuario lo porte y reinicie, esa copia sigue corriendo la antigua.

Y `manager_node_lifecycle.py` **no se mueve**: las guardas de paridad del
manager lo leen por esa ruta exacta en cada copia
(`tests/node/test_node_runtime_fork_parity_live.py`), y las otras tres lo tienen
en su raíz.

## Dos lanzadores reapuntaban el runner por prefijo de módulo

La auditoría real y la comprobación manual redirigen
`REPORT_DIR`/`CONFIG_DIR`/`LOG_DIR` recorriendo `sys.modules` y, antes, lo
hacían con `key.startswith('run_tests_')`. Con el runner en `backtest/` eso no
reapuntaba nada y los informes se habrían quedado en las carpetas del proyecto.
Peor: la prueba del envoltorio montaba su propio árbol plano de
`run_tests_*.py`, así que **habría seguido pasando sin comprobar nada**. Ahora
monta el paquete y cubre los dos envoltorios.

## El instalador copia carpetas enteras

`tools/build_installer.ps1` copiaba todo lo que hubiera en `scripts/`. Con los
cinco `.bat` dentro habría metido en la instalación cinco atajos que llaman a
los `.py` del proyecto, cuando lo que se instala son los cuatro `.exe`: filtra
`*.ps1`. Su lista `$contentFiles` también nombra ficheros de la raíz uno a uno
(`config/.env.example` desde el movimiento), y sigue dejándolos en la raíz de la
instalación, que es donde hacen falta.

## Lo que sí se queda en la raíz hay que ganárselo

La pregunta no es «¿se descubre por nombre?» sino «¿lo busca algo **de verdad**
ahí?». `ubs_generate_sets.py` tenía su entrada en el mapa de etiquetas de
`ui/app_process.py` como si la interfaz lo lanzara, y no lo lanza nadie: sólo se
importa. La entrada salió con el movimiento.
