# AGENTS.md

This repository's AI context lives in [ai_context/main.md](ai_context/main.md).

Open that file first — it indexes every topic-specific document
(architecture, routing, integrations, environment variables, conventions,
development workflow, HTTP endpoints).

## Code discovery: `codebase-memory-mcp`

This project uses the **DeusData `codebase-memory-mcp`** server as the primary
code-discovery tool. Prefer its graph tools (`search_graph`, `search_code`,
`trace_path`, `get_code_snippet`, `query_graph`, `get_architecture`) before
falling back to text search. If the MCP tools are not visible yet in a fresh
Codex chat, load/discover `codebase-memory-mcp` first; the project should
already be indexed and `auto_index` is enabled.

- Project key: derived from the checkout root, so it differs per clone
  (`G-TRADING-MT5_Autotester_agent` for RoboForex,
  `F-TRADING-MT5_Autotester_agent_AXI` for AXI). Git-aware and branch-scoped.
  Resolve it with `list_projects` instead of hardcoding it, and pass the key
  matching the checkout you are actually in.
- Server name in MCP config: `codebase-memory` (stdio, no args). It is declared
  in the project `.mcp.json`, which is **gitignored** because it points to a
  machine-specific binary path (installer default:
  `%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`).
  A fresh clone on another machine must recreate `.mcp.json` and restart the
  session. A binary under another Windows user's profile will not work;
  `C:\Users\<other>` is ACL-restricted.
- Use `index_status` / `detect_changes` to check freshness after large edits,
  and `index_repository` to re-index when the graph is stale. Note
  `detect_changes` diffs against a git baseline (`base_branch`, default `main`),
  not against the graph — a long changed-files list right after a clean
  re-index is normal; trust `index_status` (`status: ready`) instead.
- Fall back to grep/glob for non-indexed material (`.set`, `.ini`, HTML
  reports, generated outputs) or when the index is stale.

## Checkouts, branches and brokers

This repo is cloned **once per broker**, each clone pinned to its own branch.
The broker is not inferable from the source — the code carries all three at once
(`validate_roboforex_margin` / `validate_ttp_margin`, `assets/axi_*`,
`assets/ictrading_*`) — so confirm which checkout you are in before touching
assets, margin profiles or normalization.

| Branch | Broker | Checkout root |
|--------|--------|---------------|
| `dev` | **RoboForex** | `G:\TRADING\MT5_Autotester_agent` |
| `AXI` | **AXI** | `F:\TRADING\MT5_Autotester_agent_AXI` |
| `IC` | **ICTrading** | `C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent` |

Broker branches (`AXI`, `IC`) merge into `dev`. Paths are per-workstation, and
the IC checkout is normally worked on a different PC than `dev`/`AXI`.

## Absolute write boundary

Codex may modify agent code only in this ICTrading checkout while it is on
branch `IC`. It must never modify the AXI or RoboForex checkouts or their
branches directly, even to repair a live failure. The user alone ports commits
from `IC` to the other broker branches and restarts those agents. Codex must
report every agent commit and wait for the user to confirm porting and restart
before asking to resume work that depends on it.

Manager changes are allowed only in the separate
`MT5_Autotester_agent_manager` repository on branch `dev`.

For functional requirements and the technical-debt backlog, see
[requirements.md](requirements.md).

## Matriz de escritura por repositorio y rama

La rama no amplía el alcance: antes de escribir, comprobar raíz, rama y estado
con `git status --short --branch`.

| Repositorio | Rama | Escritura permitida |
| --- | --- | --- |
| Esta copia ICTrading | `IC` | Sí, para la tarea pedida. |
| Manager | `dev` | Sólo si la tarea exige explícitamente un cambio hermano de contrato. |
| Discovery Model Lab | `main` | Sólo si la tarea exige explícitamente un cambio hermano de contrato. |
| AXI, RoboForex o copia genérica | cualquiera | Nunca; el usuario porta el commit de IC. |

Preservar cambios ajenos y no mezclar unidades independientes. Revisar el
estado antes y después de verificaciones largas. Un cambio concurrente de
`HEAD` o del estado tracked invalida la medición.

## El runtime real del nodo está aquí

El proceso embebido por `app_ui.py` mediante `manager_node_lifecycle.py`
ejecuta `manager_node_runtime/`. Un arreglo equivalente en el repositorio del
manager no corrige este agente. Antes de cerrar un cambio del nodo, identificar
qué proceso ejecuta cada línea y probar la implementación de esta copia.

`manager_node_runtime/guided_batches.py` y
`manager_node_runtime/guided_controller.py` son parte del protocolo compartido
con el manager y deben permanecer idénticos byte a byte a sus hermanos, salvo
la normalización CRLF/LF propia de Git. No se
les añade ningún import nuevo. Un cambio exige dos commits hermanos y estas
pruebas, según el alcance:

- IC: `python -m unittest tests.test_prepared_candidates tests.test_guided_node tests.test_guided_http tests.test_manager_node_repair_phases`
- Manager: `python -m unittest tests.test_guided_routing`
- Lab: `python -m unittest tests.test_generation tests.test_manager_bridge tests.test_feedback tests.test_cycle_worker`

Los tests específicos del repositorio no necesitan ser idénticos. Documentar
si algún repositorio hermano no estaba disponible o no se verificó.

## Seguridad operativa y datos locales

- No lanzar MT5, la aplicación, backtests, ciclos UBS ni reinicios por
  iniciativa propia. Las pruebas unitarias y validaciones estáticas sí son
  seguras. Una petición explícita del usuario es necesaria para operar un
  terminal, escribir memoria viva o reiniciar un agente.
- `reports/`, `logs/`, `configs/` y `outputs/`, además de `runtime/`,
  `build_installer/`, `dist_installer/` y temporales, contienen estado generado
  o local. No limpiarlo, regenerarlo ni commitearlo salvo petición explícita.
- Mantener aislados broker, cuenta y run. Usar los helpers de cuenta existentes;
  no reconstruir rutas de memoria, sets o reportes a mano.
- Las pantallas mantienen sus pares `*_view.py` / `*_logic.py`; la vista compone
  widgets y la lógica conserva acceso a memoria, workers y efectos laterales.

## Lectura y análisis antes de editar

1. Leer `ai_context/main.md` y la nota del área. Usar
   `rg -il <tema> ai_context/` para texto y conocimiento no indexado.
2. Resolver el proyecto de esta copia con `list_projects`; indexar al empezar si
   falta o está obsoleto.
3. Usar `search_graph`/`search_code` para símbolos y flujos, y `trace_path`
   antes de cambiar código compartido. `get_code_snippet` sólo acepta el
   `qualified_name` exacto devuelto por el grafo.
4. El grafo no cubre estado generado ni demuestra paridad entre repositorios.
   Usar `rg`, Git y comparaciones byte a byte para esas comprobaciones.
5. Reindexar tras cambios estructurales y volver a consultar el grafo.

Toda nota duradera nueva en `ai_context/` debe añadirse también a
`ai_context/main.md`; `python -m tools.ai_context_index` lo hace cumplir.

## Tamaño y legibilidad del código

El objetivo es un máximo de 60 líneas por función y 600 por fichero. Las
excepciones heredadas están congeladas en
`tests/function_length_baseline.json` y `tests/file_length_baseline.json`:

- código nuevo por encima del techo falla; nunca se añade al baseline;
- una entrada heredada no puede crecer y se elimina en cuanto baja del techo;
- el baseline sólo encoge; no se regenera para silenciar un fallo;
- no ampliar `SKIP_PARTS` para ocultar código propio;
- un fichero grande se divide por dependencias con una fachada que preserve
  imports, y una función larga se parte en pasos con nombre o dataclasses para
  argumentos que viajan juntos.

`tools.undefined_names` detecta nombres globales sueltos después de una
extracción. Las importaciones estrella nuevas están prohibidas; cualquier
excepción heredada debe figurar exactamente en `ALLOWED_STAR_IMPORTS` y se
borra cuando deje de existir.

## Refactor sin cambio de comportamiento

- Un commit por unidad, con verificación verde antes de crearlo. No mezclar un
  movimiento estructural y un cambio funcional.
- Mover literalmente el bloque y conservar nombres de parámetros, orden,
  kwargs, mensajes, estados persistidos y efectos laterales.
- Revisar consumidores y puntos de `mock.patch`: se parchea el nombre en el
  módulo que lo consume, no necesariamente en la fachada que lo reexporta.
- Sin cobertura, limitarse a movimiento literal y comparar la versión anterior
  y la nueva sobre las mismas entradas o mediante traza de llamadas.
- Al partir un fichero, buscar guardas que nombren el fichero original para no
  debilitarlas en silencio.
- El código fuente se edita con la herramienta de parches. Tras toda
  reescritura mecánica, leer el diff completo y ejecutar
  `python -m tools.undefined_names`.

## Verificación obligatoria

Ejecutar primero pruebas focalizadas y después la puerta del repositorio:

```text
python -m tools.verify_project --quick
python -m tools.verify_project
```

La primera ejecuta auditorías y guardas; la segunda añade
`python -m unittest discover -s tests`. No usar `pytest`: no forma parte del
contrato instalado. La puerta no debe lanzar MT5 ni alterar memoria, reportes,
sets o configuración operativa.

## Definición de terminado y entrega

Un cambio sólo está terminado cuando:

1. el alcance real y los repositorios hermanos afectados están identificados;
2. las pruebas focalizadas y `python -m tools.verify_project` pasan;
3. el diff y `git status` contienen sólo cambios intencionados;
4. los cambios estructurales se reindexaron;
5. las decisiones duraderas quedaron en `ai_context/` y su índice;
6. se creó el commit en `IC` y se comunicaron hash, verificaciones, deuda
   heredada restante y cualquier comprobación omitida;
7. se recuerda que el usuario porta el commit a AXI/RoboForex y reinicia los
   agentes cuando el cambio afecte al runtime. El asistente no hace ese porting.
