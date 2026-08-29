# Live-account audit in the embedded manager node

The executing implementation is `manager_node_runtime/live_audit.py`, loaded by
`app_ui.py` through `manager_node_lifecycle.py`. The similarly named manager
module is the reference implementation; changing only the manager does not
change this broker node.

Each audit use is identified by `audit_key`, independently from `portfolio_id`.
The same saved bundle can therefore be audited in different accounts or in
different A/M/C variants. Requests must include one exact `portfolio_type`:
`aggressive`, `balanced`, or `conservative`. The runtime never falls back to all
bundle members.

## MT5 source-history rules

- Verify login, exact server, and `terminal_info.connected` before trusting
  account history.
- `history_deals_get()` immediately after `initialize()` can return an empty or
  stale cache. Poll until a non-empty fingerprint is stable; for a genuinely
  empty account, retain every empty snapshot in diagnostics.
- A close inside the audited period can belong to a position opened before the
  period. Recover its full deal history by `position_id` before reconstructing
  trades, then retain only trades whose close falls inside the requested range.
- Persist safe diagnostics: sync snapshots, raw/market/open/close deal counts,
  missing and recovered prior opens, reconstructed trades, portfolio closures,
  and ignored foreign closures.
- Filter the account closures by `(symbol, EA_MagicNumber)` from the selected
  bundle variant before comparing them with Strategy Tester.

The 2026-08-20 verification for audit use `9` connected to MT5 profile
`MT5_IC_1`, login `52958158`, server `CapitalPointTrading-Demo`. The exact
seven-day interval contained 17 market deals, 8 in-period opens and 9 closes.
One close had an opening before the period and was recovered successfully.
Five of the nine closes matched the selected Moderate bundle signatures; four
belonged to other strategies. The corrected end-to-end run produced 39 tester
trades and 4 real-tester matches.

## Tester artifacts and logging

Source `.set` files are UTF-16. Detect their encoding, preserve it in the audit
copy, and replace `StartLots` rather than appending a second parameter. Logs
must redact both account secrets and every `Password=` value, including the log
files written internally by `run_tests.py`. The verified run created six
UTF-16 set copies with exactly one `StartLots` each and 27 log/text artifacts
with no persisted password or configured secret.

## Auditable comparison contract

The runtime persists one safe JSON row per tester trade in
`comparison_detail.operation_comparisons`. Each row contains the tester trade,
the selected real trade or nearest unused candidate, measured deltas, applied
limits, and exact reason codes. It also persists per-strategy summaries,
unmatched real operations, methodology, and aggregate drawdown validation.

`matched_trades` is the number of symbol/side/open-time aligned pairs. It is not
proof that a pair passed every check; `within_tolerance_trades` is that stricter
count. This distinction must remain visible in summaries. The manager renders
the contract on a dedicated result page. Old aggregate-only results cannot be
expanded retrospectively and must request a fresh audit instead of inventing
row detail.

## Reports and executed-lot evidence

Every completed run now persists `strategy_artifacts`. For each selected member
it records the configured portfolio lot and rereads `StartLots` from the exact
runtime `.set` copy after writing it. It also records magic, source/runtime set
filenames, trade count, History Quality, and the MT5 report filename. The
manager result page shows an explicit match/mismatch status and opens each
original MT5 report in a separate tab.

The real-account artifact is now the original HTML saved by the MT5 terminal,
not a Python reconstruction. The runtime activates Toolbox/History, invokes
the native Custom Period command, explicitly selects its custom mode (internal
index 0), writes both dates, and runs Report/HTML. It publishes
`real_account_mt5_report.html` plus MT5's companion PNG only after validating
the `client terminal` generator marker, the native title (`Trade History
Report` or the official Spanish localization `Informe del historial de
trading`), and the audited login. Export or signature failure fails the audit
instead of falling back to an invented table. Reconstructed deals remain
internal comparison diagnostics.

The node may run in an RDP session different from the console terminal used by
the MT5 Python API. Cross-session windows cannot be automated. The runtime then
opens another configured IC terminal in its own session, connects the same
account, exports the native report, and closes only the PID it launched. The
Save As edit and button are driven with direct control messages because RDP can
reject `SetForegroundWindow`. Production run `20260821_021042_643587` proved
this path through `MT5_IC_2`: 82,696 UTF-16 bytes, the `client terminal`
signature, login 52958158, custom dates 2026-08-14 through 2026-08-21, and the
native companion PNG.

Run `20260821_000733_052028` (audit use 9, aggressive) produced nine account
closures: five selected and four foreign. Its six reread lot checks matched:
BTCUSD 0.06, XAUUSD 0.03, USDJPY 0.04, XAGUSD 0.04, EURUSD 0.06, and USTEC
0.01; every MT5 report had 100% History Quality.
Those checks prove the runtime set values, not the final EA order size. Five
reports traded exactly their `StartLots`; USTEC traded 0.10 while its runtime
set contained `StartLots=0.01`. Keep `observed_trade_volumes` and
`report_volumes_match_start_lots` visible so this discrepancy cannot be hidden
behind a successful set rewrite.

`LiveAuditController.artifact_path` only serves HTML/image files from the
current run's `reports` directory. The authenticated node endpoint serves the
bytes and the manager proxies them. Never broaden it to `.set`, INI, logs, or
arbitrary runtime paths; those can contain implementation details or secrets.
## Cerrar el terminal sin matarlo

El auditor abre terminales MT5 con `initialize(login=...)`. Cerrarlos después
con `taskkill /F` mata el proceso antes de que MT5 escriba su configuración y le
**borra la sesión guardada**. El terminal vuelve a abrirse sin cuenta, y el
Strategy Tester del pipeline —que no inyecta credenciales— se queda esperando
hasta registrar `not synchronized with trade server`, no genera informe y
reintenta en bucle cada ~16 minutos.

Ocurrió el 2026-08-21. Cronología, para reconocerlo si vuelve:

| Momento | Hecho |
| --- | --- |
| 08-20 20:36 | Último informe generado con éxito (`reports/`). |
| 08-21 02:10-02:13 | Auditoría 9 sobre `MT5_IC_1`; termina matando `tester_pids` con `/T /F`. |
| 08-21 16:04 en adelante | `Tester/logs`: `not synchronized with trade server` cada ~16 min. |
| 08-22 18:33-18:53 | Discovery: 45/45 backtests sin informe, `scored=0 survivors=0`. |
| 08-23 02:48 | Los terminales colgados nunca llegan a `ShutdownTerminal=1`; la reparación aborta con `MT5_IC_1 ya esta abierta`. |

El síntoma no se parece a la causa: parece que falla el generador o MT5, y lo
que falla es la cuenta del terminal. La pista fiable es el journal del tester en
`<data_dir>\Tester\logs\<fecha>.log`, no el log del runner.

Por qué la auditoría no se notaba a sí misma: su propio `tester.ini` escribe
`[Common] Login/Password/Server` (`live_audit.py`, preparación del Strategy
Tester), así que sus backtests autorizan aunque el terminal haya perdido la
cuenta. `tester_template.ini` —el del pipeline— no tiene esa sección y depende
por completo de la cuenta recordada. Mientras siga así, cualquier cosa que
deslogee un terminal deja el pipeline a cero sin previo aviso.

Reglas:

- Todo cierre de terminal pasa por `_close_terminal_pids_gracefully`
  (WM_CLOSE, y `/F` solo para los que no obedecen tras 30 s).
- `_close_terminal_pids` es únicamente el último recurso de esa función.
  `test_no_terminal_is_ever_force_killed_outside_the_graceful_close` falla si
  vuelve a llamarse desde otro sitio.
- Recuperar un terminal ya desconectado exige loguearlo a mano: la restauración
  del auditor solo cubre los terminales que él tocó durante una ejecución.

## Cuenta final independiente del tester (2026-08-29)

La restauración recibe `restore_login`, `restore_server` y `restore_password`
desde el manager. No debe volver a usar `tester_*`: la cuenta del tester
pertenece a la prueba y puede no ser la cuenta que el operador quiere conservar
en los terminales al finalizar.

`_remember_real_account_terminal` registra todas las rutas donde la auditoría
activó la cuenta real y deduplica la misma ruta. El `finally` recorre la lista
completa, conecta la cuenta `restore_*`, confirma login y servidor con
`account_info()`, registra una fila `terminal_restore` por ruta y solo después
reanuda el pipeline. El flujo aplica igual si falla la extracción, el reporte,
el tester o la comparación. El secreto de restauración participa en la
redacción de excepciones y nunca entra en el estado persistente del nodo.

Este runtime anuncia `capabilities.live_audit_restore_account=true`. El manager
debe rechazar la auditoría si el proceso aún cargado no publica esa capacidad;
es la barrera de compatibilidad durante un despliegue con el agente ocupado.

## Normalización del lote en portfolios antiguos

Desde 2026-08-21 el runtime ejecutado lee `assets/<broker>_symbol_specs.json` y
normaliza `StartLots` a `max(lot_guardado, units * volume_min)`, redondeado a
`volume_step`. Así USTEC con una unidad guardada históricamente como `0.01` se
audita a `0.10`, que es el mínimo real publicado por ICTrading. La evidencia de
la auditoría conserva ambos lotes y la regla aplicada.
