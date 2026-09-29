"""Lectura de la linea de ordenes del agente UBS."""
from __future__ import annotations

import argparse
from pathlib import Path

from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    broker_asset_universe_path_with_fallback,
    default_symbol_map_for_broker,
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
)
from ubs.degradation import (
    DEFAULT_MAX_DD_INFLATION,
    DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY,
    DEFAULT_MIN_BOOTSTRAP_PF_P05,
    DEFAULT_MIN_NET_RETENTION,
    DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO,
    DEFAULT_MIN_PF_EDGE_RETENTION,
    DEFAULT_MIN_RECOVERY_RETENTION,
    DEFAULT_MIN_RESIDUAL_PROFIT_RATIO,
    DEFAULT_MIN_STABILITY_RETENTION,
    DEFAULT_MIN_TRADE_CURVE_STABILITY,
    DEFAULT_MIN_TRADE_RATE_RETENTION,
)
from ubs.path_utils import resolve_workspace_path
from ubs.regression_rules import (
    DEFAULT_REGRESSION_FROM_DATE,
    DEFAULT_REGRESSION_MAX_DD_RATIO,
    DEFAULT_REGRESSION_MAX_DRAWDOWN_PCT,
    DEFAULT_REGRESSION_MIN_NET_PROFIT,
    DEFAULT_REGRESSION_MIN_PF_EFFICIENCY,
    DEFAULT_REGRESSION_MIN_POSITIVE_MONTH_RATIO,
    DEFAULT_REGRESSION_MIN_PROFIT_FACTOR,
    DEFAULT_REGRESSION_MIN_RECOVERY_FACTOR,
    DEFAULT_REGRESSION_MIN_TRADES,
    DEFAULT_REGRESSION_MIN_TRADES_MN,
    DEFAULT_REGRESSION_MIN_TRADES_W1,
    DEFAULT_REGRESSION_NEGATIVE_POINTS,
    DEFAULT_REGRESSION_POSITIVE_POINTS,
    DEFAULT_REGRESSION_TO_DATE,
)
from ubs.score import ScoreConfig
from ubs.weights import DEFAULT_ROBUST_NEGATIVE_BONUS, DEFAULT_ROBUST_POSITIVE_BONUS
from ubs_agent_config import (
    ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION,
    ASSET_UNSEEDED_FORCE_PROB_LATE,
    BASE_DIR,
    DEFAULT_ASSETS,
    DEFAULT_MEMORY,
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    DEFAULT_TEMPLATE,
    GENERATION_MODES,
    LosslessControlGate,
    TF_UNSEEDED_FORCE_PROB_BY_GENERATION,
    TF_UNSEEDED_FORCE_PROB_LATE,
    augment_symbol_map_with_suffix_targets,
    paths_belong_to_workspace,
    probability_argument,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agente UBS con seleccion de assets, mutacion guiada y memoria.")
    score_defaults = ScoreConfig()
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER, help="Broker UBS: ROBOFOREX, ICTRADING o AXI.")
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE, help="Tipo de cuenta UBS del broker seleccionado.")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS))
    parser.add_argument("--expert", help="Ruta .ex5 UBS para ejecutar backtests.")
    parser.add_argument("--mt5-path", help="Ruta terminal64.exe.")
    parser.add_argument("--data-dir", help="Carpeta de datos MT5.")
    parser.add_argument("--terminals-config", help="Archivo .ini con perfiles multiterminal.")
    parser.add_argument("--multi-terminal", action="store_true", help="Ejecuta backtests UBS repartidos entre terminales configuradas.")
    parser.add_argument("--max-workers", type=int, default=1, help="Maximo de terminales simultaneas con --multi-terminal.")
    parser.add_argument("--symbol-suffix", default="", help="Sufijo de simbolo del broker, por ejemplo .sa para AXI.")
    parser.add_argument("--symbol-futures-suffix", default="", help="Sufijo de futuros/CFDs del broker, por ejemplo .fs para AXI.")
    parser.add_argument("--symbol-shares-suffix", default="", help="Sufijo de shares/ETFs del broker, por ejemplo + para AXI.")
    parser.add_argument("--symbol-map")
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--variants-per-seed", type=int, default=3)
    parser.add_argument("--max-seeds", type=int, default=30)
    parser.add_argument("--mutations-per-variant", type=int, default=6)
    parser.add_argument("--prepared-manifest", type=Path, help="Lote recibido por el nodo; se evalúa sin volver a mutar")
    parser.add_argument("--top-percent", type=float, default=20.0)
    parser.add_argument(
        "--asset-unseeded-prob-gen1",
        type=probability_argument,
        default=ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[1],
        help="Probabilidad discovery de forzar un activo sin seed en generacion 1.",
    )
    parser.add_argument(
        "--asset-unseeded-prob-gen2",
        type=probability_argument,
        default=ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[2],
        help="Probabilidad discovery de forzar un activo sin seed en generacion 2.",
    )
    parser.add_argument(
        "--asset-unseeded-prob-late",
        type=probability_argument,
        default=ASSET_UNSEEDED_FORCE_PROB_LATE,
        help="Probabilidad discovery de forzar un activo sin seed desde generacion 3.",
    )
    parser.add_argument(
        "--timeframe-unseeded-prob-gen1",
        type=probability_argument,
        default=TF_UNSEEDED_FORCE_PROB_BY_GENERATION[1],
        help="Probabilidad discovery de forzar un timeframe sin seed en generacion 1.",
    )
    parser.add_argument(
        "--timeframe-unseeded-prob-gen2",
        type=probability_argument,
        default=TF_UNSEEDED_FORCE_PROB_BY_GENERATION[2],
        help="Probabilidad discovery de forzar un timeframe sin seed en generacion 2.",
    )
    parser.add_argument(
        "--timeframe-unseeded-prob-late",
        type=probability_argument,
        default=TF_UNSEEDED_FORCE_PROB_LATE,
        help="Probabilidad discovery de forzar un timeframe sin seed desde generacion 3.",
    )
    parser.add_argument(
        "--generation-mode",
        choices=GENERATION_MODES,
        default="production",
        help="production prioriza rendimiento conocido; discovery activa exploracion del universo sin seed.",
    )
    parser.add_argument(
        "--force-unseeded-universe",
        action="store_true",
        help="Alias legacy de --generation-mode discovery.",
    )
    parser.add_argument(
        "--experimental-long-timeframes",
        action="store_true",
        help="Incluye W1/MN como targets experimentales de generacion.",
    )
    parser.add_argument("--continue-last-run", action="store_true", help="Usa la ultima generacion registrada como seeds.")
    parser.add_argument(
        "--backtest-pending-only",
        action="store_true",
        help="Con --continue-last-run, ejecuta solo candidatos generated pendientes y no crea generaciones nuevas.",
    )
    parser.add_argument("--evaluate-seeds", action="store_true", help="Backtestea y puntua las semillas UBS nuevas o modificadas.")
    parser.add_argument("--probe-universe-history", action="store_true", help="Prueba si los simbolos activos del universo tienen historico para el rango configurado.")
    parser.add_argument("--probe-history-timeframe", default="H1", help="Timeframe usado para el probe de historico del universo.")
    parser.add_argument("--probe-history-limit", type=int, default=0, help="Limita el numero de simbolos a probar; 0 = todos los GEN=si.")
    parser.add_argument("--evaluate-robustness", action="store_true", help="Backtestea candidatos accepted de un run en ventana OOS/robustez.")
    parser.add_argument("--robust-run-id", type=int, help="Run SQLite cuyos accepted se enviaran al test de robustez.")
    parser.add_argument(
        "--robust-candidate-id",
        type=int,
        action="append",
        help="Limita robustez a un candidate id concreto. Puede repetirse para varios candidatos.",
    )
    parser.add_argument("--robust-pending-only", action="store_true", help="Con --evaluate-robustness, testea solo accepted sin robustez registrada.")
    parser.add_argument("--robust-positive-bonus", type=float, default=DEFAULT_ROBUST_POSITIVE_BONUS, help="Bonus de peso si el candidato pasa robustez.")
    parser.add_argument("--robust-negative-bonus", type=float, default=DEFAULT_ROBUST_NEGATIVE_BONUS, help="Bonus de peso si el candidato falla robustez.")
    parser.add_argument("--robust-min-net-retention", type=float, default=DEFAULT_MIN_NET_RETENTION, help="Retencion minima del net anualizado OOS frente a construccion; 0 desactiva.")
    parser.add_argument("--robust-min-pf-edge-retention", type=float, default=DEFAULT_MIN_PF_EDGE_RETENTION, help="Retencion minima de la ventaja PF sobre 1.0; 0 desactiva.")
    parser.add_argument("--robust-min-recovery-retention", type=float, default=DEFAULT_MIN_RECOVERY_RETENTION, help="Retencion minima del Recovery Factor; 0 desactiva.")
    parser.add_argument("--robust-max-dd-inflation", type=float, default=DEFAULT_MAX_DD_INFLATION, help="Inflacion maxima del drawdown OOS frente a construccion; 0 desactiva.")
    parser.add_argument("--robust-min-trade-rate-retention", type=float, default=DEFAULT_MIN_TRADE_RATE_RETENTION, help="Retencion minima del ritmo de operaciones OOS; 0 desactiva.")
    parser.add_argument("--robust-min-residual-profit-ratio", type=float, default=DEFAULT_MIN_RESIDUAL_PROFIT_RATIO, help="Fraccion minima del neto OOS que queda al excluir los tres mejores meses; 0 desactiva.")
    parser.add_argument("--robust-min-oos-positive-month-ratio", type=float, default=DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO, help="Fraccion minima de meses OOS positivos; 0 desactiva.")
    parser.add_argument("--robust-min-trade-curve-stability", type=float, default=DEFAULT_MIN_TRADE_CURVE_STABILITY, help="R2 minimo de la curva acumulada OOS por trade; 0 desactiva.")
    parser.add_argument("--robust-min-stability-retention", type=float, default=DEFAULT_MIN_STABILITY_RETENTION, help="Retencion minima de estabilidad OOS frente a construccion; 0 desactiva.")
    parser.add_argument("--robust-min-bootstrap-net-probability", type=float, default=DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY, help="Probabilidad bootstrap minima de neto OOS positivo; 0 desactiva.")
    parser.add_argument("--robust-min-bootstrap-pf-p05", type=float, default=DEFAULT_MIN_BOOTSTRAP_PF_P05, help="Percentil 5 bootstrap minimo del PF OOS; 0 desactiva.")
    parser.add_argument("--evaluate-final-tick", action="store_true", help="Compara OHLC vs Every tick based on real ticks para robustez accepted.")
    parser.add_argument("--final-tick-run-id", type=int, help="Run SQLite cuyos robust accepted se enviaran al test Final Tick.")
    parser.add_argument(
        "--final-tick-stage",
        choices=("probe", "six_month"),
        default="probe",
        help="Etapa Final Tick: probe=filtro corto; six_month=validacion 6M para uso en portafolio.",
    )
    parser.add_argument("--final-tick-pending-only", action="store_true", help="Con --evaluate-final-tick, testea solo robust accepted sin Final Tick.")
    parser.add_argument("--final-tick-retry-pending-quality", action="store_true", help="Con --final-tick-pending-only, reintenta exclusivamente las filas pending_history_quality, coincidan o no las fechas guardadas.")
    parser.add_argument("--final-tick-reconcile-only", action="store_true", help="Con --evaluate-final-tick, concilia reportes OHLC/Every Tick ya existentes en disco sin abrir MT5.")
    parser.add_argument("--final-tick-skip-ohlc", action="store_true", help="Salta el backtest OHLC y reutiliza ohlc_metrics_json guardado en DB; solo ejecuta Every Tick.")
    parser.add_argument("--final-tick-min-history-quality", type=float, default=80.0, help="Calidad minima History Quality del reporte real tick.")
    parser.add_argument("--final-tick-min-ohlc-trades", type=int, default=5, help="Operaciones OHLC minimas para pasar a Every Tick.")
    parser.add_argument("--final-tick-min-trades-w1", type=int, default=2, help="Operaciones minimas Final Tick para W1.")
    parser.add_argument("--final-tick-min-trades-mn", type=int, default=1, help="Operaciones minimas Final Tick para MN.")
    parser.add_argument("--final-tick-ohlc-from-date", default="", help="Fecha alternativa para reintentar pendientes por pocas operaciones OHLC.")
    parser.add_argument("--final-tick-ohlc-to-date", default="", help="Fecha alternativa final para reintentar pendientes por pocas operaciones OHLC.")
    parser.add_argument("--final-tick-max-net-delta-pct", type=float, default=35.0, help="Diferencia maxima de net normalizado vs OHLC.")
    parser.add_argument("--final-tick-max-pf-delta-pct", type=float, default=35.0, help="Diferencia maxima de PF vs OHLC.")
    parser.add_argument("--final-tick-max-dd-delta-pct", type=float, default=35.0, help="Diferencia maxima de DD pct vs OHLC.")
    parser.add_argument("--final-tick-max-trades-delta-pct", type=float, default=35.0, help="Diferencia maxima de trades vs OHLC.")
    # Respaldo para 6M cuando el control OHLC cierra sin ninguna operacion
    # perdedora: PF y DD dejan de ser comparables y la pata de tick pasa a
    # juzgarse sola. Defaults = p05 (p95 para DD) de la pata real-tick de las
    # filas candidate_final_tick_6m aceptadas en memoria a 2026-09.
    lossless_defaults = LosslessControlGate()
    parser.add_argument("--final-tick-6m-lossless-min-trades", type=int, default=lossless_defaults.min_trades, help="6M control sin perdidas: operaciones minimas de la pata real tick.")
    parser.add_argument("--final-tick-6m-lossless-min-net", type=float, default=lossless_defaults.min_normalized_net_profit, help="6M control sin perdidas: net normalizado minimo de la pata real tick.")
    parser.add_argument("--final-tick-6m-lossless-min-pf", type=float, default=lossless_defaults.min_profit_factor, help="6M control sin perdidas: profit factor minimo de la pata real tick.")
    parser.add_argument("--final-tick-6m-lossless-max-dd-pct", type=float, default=lossless_defaults.max_drawdown_pct, help="6M control sin perdidas: drawdown maximo de la pata real tick.")
    parser.add_argument("--final-tick-6m-lossless-min-recovery", type=float, default=lossless_defaults.min_recovery_factor, help="6M control sin perdidas: recovery factor minimo de la pata real tick.")
    parser.add_argument("--final-tick-6m-lossless-min-positive-month-ratio", type=float, default=lossless_defaults.min_positive_month_ratio, help="6M control sin perdidas: ratio minimo de meses positivos de la pata real tick.")
    parser.add_argument(
        "--evaluate-regression",
        action="store_true",
        help="Valida con Model=1 OHLC un rango historico anterior sobre Final Tick 6M accepted.",
    )
    parser.add_argument("--regression-run-id", type=int, help="Run SQLite cuyos Final Tick 6M accepted se evaluaran.")
    parser.add_argument(
        "--regression-candidate-id",
        type=int,
        action="append",
        help="Limita la prueba regresiva a un candidate id. Puede repetirse.",
    )
    parser.add_argument(
        "--regression-pending-only",
        action="store_true",
        help="Ejecuta solo Final Tick 6M accepted sin resultado regresivo final o con error tecnico retryable.",
    )
    parser.add_argument("--regression-from-date", default=DEFAULT_REGRESSION_FROM_DATE)
    parser.add_argument("--regression-to-date", default=DEFAULT_REGRESSION_TO_DATE)
    parser.add_argument("--regression-min-net-profit", type=float, default=DEFAULT_REGRESSION_MIN_NET_PROFIT)
    parser.add_argument("--regression-min-profit-factor", type=float, default=DEFAULT_REGRESSION_MIN_PROFIT_FACTOR)
    parser.add_argument("--regression-min-trades", type=int, default=DEFAULT_REGRESSION_MIN_TRADES)
    parser.add_argument("--regression-min-trades-w1", type=int, default=DEFAULT_REGRESSION_MIN_TRADES_W1)
    parser.add_argument("--regression-min-trades-mn", type=int, default=DEFAULT_REGRESSION_MIN_TRADES_MN)
    parser.add_argument("--regression-max-drawdown-pct", type=float, default=DEFAULT_REGRESSION_MAX_DRAWDOWN_PCT)
    parser.add_argument("--regression-min-recovery-factor", type=float, default=DEFAULT_REGRESSION_MIN_RECOVERY_FACTOR)
    parser.add_argument(
        "--regression-min-pf-efficiency",
        type=float,
        default=DEFAULT_REGRESSION_MIN_PF_EFFICIENCY,
        help="Minima eficiencia PF regresiva/base (WFE). 0 desactiva la comprobacion relativa.",
    )
    parser.add_argument(
        "--regression-max-dd-ratio",
        type=float,
        default=DEFAULT_REGRESSION_MAX_DD_RATIO,
        help="Maximo cociente DD%% regresiva/base (regla de crisis <=2x). 0 desactiva la comprobacion.",
    )
    parser.add_argument(
        "--regression-min-positive-month-ratio",
        type=float,
        default=DEFAULT_REGRESSION_MIN_POSITIVE_MONTH_RATIO,
    )
    parser.add_argument("--regression-positive-points", type=float, default=DEFAULT_REGRESSION_POSITIVE_POINTS)
    parser.add_argument("--regression-negative-points", type=float, default=DEFAULT_REGRESSION_NEGATIVE_POINTS)
    parser.add_argument("--rescore-seeds-only", action="store_true", help="Recalcula accepted/rejected de seeds existentes sin abrir MT5.")
    parser.add_argument("--rescore-candidates-only", action="store_true", help="Recalcula candidatos existentes desde metrics_json sin abrir MT5.")
    parser.add_argument("--rescore-robustness-only", action="store_true", help="Recalcula resultados OOS existentes desde metrics_json sin abrir MT5.")
    parser.add_argument("--rescore-final-tick-only", action="store_true", help="Recalcula Final Tick existente desde metrics_json sin abrir MT5.")
    parser.add_argument(
        "--rescore-regression-only",
        action="store_true",
        help="Recalcula la prueba regresiva desde metrics_json sin abrir MT5.",
    )
    parser.add_argument(
        "--rescore-from-reports",
        action="store_true",
        help="Fuerza que los modos --rescore-* vuelvan a parsear HTML; usar solo si cambio el parser o la normalizacion.",
    )
    parser.add_argument(
        "--reconcile-seed-eval-only",
        action="store_true",
        help="Con --evaluate-seeds, clasifica reportes de evaluaciones seed incompletas sin abrir MT5.",
    )
    parser.add_argument("--reevaluate-seeds", action="store_true", help="Con --evaluate-seeds, vuelve a testear todas las semillas activas.")
    parser.add_argument(
        "--retry-candidate-id",
        type=int,
        action="append",
        help="Relanza un candidato concreto y actualiza su estado en memoria. Puede repetirse para varios candidatos.",
    )
    parser.add_argument("--retry-seed-path", action="append", help="Relanza una semilla concreta y actualiza seed_scores. Puede repetirse.")
    parser.add_argument("--retry-run-id", type=int, help="Run SQLite para retry de mismatches. Si se omite usa el ultimo run.")
    parser.add_argument(
        "--retry-mismatch-run",
        action="store_true",
        help="Relanza los problemas tecnicos reintentables de un run.",
    )
    parser.add_argument("--retry-full-run", action="store_true", help="Relanza todos los candidatos de un run y reemplaza sus resultados.")
    parser.add_argument(
        "--retry-mismatch-generation",
        type=int,
        help="Relanza los problemas tecnicos reintentables de una generacion.",
    )
    parser.add_argument("--min-net-profit", type=float, default=score_defaults.min_net_profit)
    parser.add_argument("--risk-profit-mode", choices=("off", "shadow", "enforce"), default=None,
                        help="Via alternativa por riesgo: shadow audita; enforce permite rescates.")
    parser.add_argument("--risk-profit-config", type=Path,
                        help="JSON con umbrales RiskProfitConfig para base y robustez.")
    parser.add_argument("--min-profit-factor", type=float, default=score_defaults.min_profit_factor)
    parser.add_argument("--min-trades", type=int, default=score_defaults.min_trades)
    parser.add_argument("--min-trades-w1", type=int, default=12, help="Trades minimos para W1 en score base/robustez.")
    parser.add_argument("--min-trades-mn", type=int, default=4, help="Trades minimos para MN en score base/robustez.")
    parser.add_argument("--max-drawdown-pct", type=float, default=score_defaults.max_drawdown_pct)
    parser.add_argument("--min-recovery-factor", type=float, default=score_defaults.min_recovery_factor)
    parser.add_argument("--min-positive-month-ratio", type=float, default=score_defaults.min_positive_month_ratio)
    parser.add_argument("--delay", type=int, default=1)
    parser.add_argument("--from-date", default="", help="Fecha inicio YYYY.MM.DD. Sobreescribe FromDate del template.")
    parser.add_argument("--to-date", default="", help="Fecha fin YYYY.MM.DD. Sobreescribe ToDate del template.")
    parser.add_argument("--execute-backtests", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No abre MT5; pasa --dry-run a run_tests.")
    parser.add_argument("--random-seed", type=int)
    args = parser.parse_args()
    if args.force_unseeded_universe:
        args.generation_mode = "discovery"
    args.force_unseeded_universe = args.generation_mode == "discovery"
    args.broker = normalize_broker(args.broker)
    args.account_type = normalize_account_type(args.account_type, args.broker)
    if args.symbol_map is None:
        args.symbol_map = default_symbol_map_for_broker(args.broker)
    args.run_tests_symbol_map = args.symbol_map
    # Legacy migration only belongs to the checkout's persistent storage.
    # Integration runs often point output and memory at a temporary directory;
    # scanning/mutating the live legacy store in that case is surprising and
    # can dominate the whole command on mature histories.
    if paths_belong_to_workspace(args.output_dir, args.memory):
        migrate_legacy_account_storage(BASE_DIR, args.account_type, args.broker)
    if Path(args.source_dir).expanduser() == DEFAULT_SOURCE:
        args.source_dir = str(account_seed_dir(BASE_DIR, args.account_type, args.broker))
    else:
        args.source_dir = str(resolve_workspace_path(args.source_dir))
    if Path(args.output_dir).expanduser() == DEFAULT_OUTPUT:
        args.output_dir = str(account_output_dir(BASE_DIR, args.account_type, args.broker))
    else:
        args.output_dir = str(resolve_workspace_path(args.output_dir))
    legacy_memory = BASE_DIR / "outputs" / "ubs_memory.sqlite"
    if Path(args.memory).expanduser() in {DEFAULT_MEMORY, legacy_memory}:
        args.memory = str(account_memory_path(BASE_DIR, args.account_type, args.broker))
    else:
        args.memory = str(resolve_workspace_path(args.memory))
    if Path(args.assets).expanduser() == DEFAULT_ASSETS:
        args.assets = str(broker_asset_universe_path_with_fallback(BASE_DIR, args.broker))
    else:
        args.assets = str(resolve_workspace_path(args.assets))
    args.symbol_map = augment_symbol_map_with_suffix_targets(args.symbol_map, args)
    return args
