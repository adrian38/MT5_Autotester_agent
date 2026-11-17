"""Reparto de las pruebas en grupos por area.

La suite completa se corre solo cuando se pide (``--full``). El trabajo normal
pasa por un grupo: el del area que se esta tocando. Para que eso sea fiable, el
reparto es explicito y una prueba guarda comprueba que ningun ``test_*.py``
queda fuera ni sobra en el mapa; un modulo nuevo no puede quedarse sin grupo en
silencio, que es como un reparto por nombre acaba dejando pruebas sin correr.

    python -m tools.test_groups              # lista los grupos
    python -m tools.verify_project --group final_tick
"""
from __future__ import annotations

from pathlib import Path

from tools.source_files import ROOT

TESTS_DIR = ROOT / "tests"

# Grupo -> modulos de tests, sin el prefijo "tests.". Cada modulo va en
# exactamente un grupo.
TEST_GROUPS: dict[str, tuple[str, ...]] = {
    # Las auditorias mecanicas y los contratos del repositorio. Es el grupo que
    # corre siempre, tambien en el modo rapido de la puerta.
    "guardas": (
        "quality.test_ai_context_index",
        "quality.test_file_length",
        "quality.test_function_length",
        "quality.test_project_rules",
        "quality.test_repository_root",
        "quality.test_undefined_names",
        "quality.test_verify_project",
        "quality.test_test_groups",
    ),
    # Generacion, seeds, seleccion y los modos sueltos del agente.
    "agente": (
        "ubs.agent.test_ubs_agent_discovery",
        "ubs.agent.test_ubs_agent_files",
        "ubs.agent.test_ubs_agent_history_probe",
        "ubs.agent.test_ubs_agent_reconcile",
        "ubs.agent.test_ubs_agent_rescore",
        "ubs.agent.test_ubs_agent_retry_spelling",
        "ubs.agent.test_ubs_agent_run",
        "ubs.agent.test_ubs_agent_run_modes",
        "ubs.agent.test_ubs_agent_seeds",
        "ubs.agent.test_ubs_agent_selection",
        "ubs.agent.test_ubs_agent_status",
        "ubs.agent.test_ubs_agent_variants",
        "ubs.agent.test_ubs_run_config",
        "ubs.seeds.test_ubs_seed_dedup",
        "ubs.seeds.test_ubs_seed_metadata",
        "ubs.seeds.test_ubs_v6_parameters",
        "ubs.seeds.test_ubs_version_rules",
        "ubs.sets.test_ubs_set_rules",
        "ubs.sets.test_ubs_set_stage_params",
    ),
    # Las etapas que contrastan OHLC contra real tick, de candidatos y de
    # seeds, mas robustez, regresiva y veredictos manuales.
    "final_tick": (
        "ubs.final_tick.test_ubs_agent_final_tick_passes",
        "ubs.final_tick.test_ubs_final_tick_6m",
        "ubs.final_tick.test_ubs_final_tick_dd_gap",
        "ubs.final_tick.test_ubs_final_tick_lossless_control",
        "ubs.final_tick.test_ubs_final_tick_rescore_window",
        "ubs.final_tick.test_ubs_final_tick_status_repair",
        "ubs.final_tick.test_ubs_regression",
        "ubs.scoring.test_ubs_degradation",
        "ubs.scoring.test_ubs_manual_status",
        "ubs.seeds.test_ubs_seed_final_tick",
        "ubs.ui.test_ubs_final_tick_ui",
        "ubs.ui.test_ubs_robustness_ui",
    ),
    # Portafolio UBS: construccion, persistencia, margenes y reparaciones.
    "portafolio": (
        "guided.test_prepared_candidates",
        "portfolio.test_risk_profit",
        "ubs.portfolio.test_ubs_portfolio",
        "ubs.portfolio.test_ubs_portfolio_bundles",
        "ubs.portfolio.test_ubs_portfolio_persistence",
        "ubs.portfolio.test_ubs_portfolio_repair",
        "ubs.portfolio.test_ubs_portfolio_strict",
        "ubs.portfolio.test_ubs_risk_profit_repair",
        "ubs.ui.test_ubs_risk_profit_repair_button",
        "ui.test_app_state_portfolio",
    ),
    # Universo del broker, normalizacion, pesos y score.
    "universo": (
        "ubs.account.test_ubs_account",
        "ubs.account.test_ubs_account_migration",
        "ubs.diagnostics.test_mt5_symbol_extract",
        "ubs.diagnostics.test_ubs_broker_symbol_spelling",
        "ubs.diagnostics.test_ubs_symbol_missing_in_journal",
        "ubs.diagnostics.test_ubs_symbol_not_exist_status",
        "ubs.scoring.test_ubs_normalization_gen",
        "ubs.scoring.test_ubs_score",
        "ubs.scoring.test_ubs_selection",
        "ubs.scoring.test_ubs_selection_model",
        "ubs.scoring.test_ubs_stage_audit_fixes",
        "ubs.scoring.test_ubs_stage_pending_runs",
        "ubs.scoring.test_ubs_stage_rules_audit",
        "ubs.scoring.test_ubs_stage_settled_statuses",
        "ubs.scoring.test_ubs_weights",
        "ubs.sets.test_ubs_timeframes",
        "ubs.ui.test_ubs_universe_sync_ui",
    ),
    # Runtime del nodo y el protocolo guiado con el manager.
    "nodo": (
        "guided.test_guided_http",
        "guided.test_guided_node",
        "manager_node.test_cleanup",
        "manager_node.test_exclusions",
        "manager_node.test_lifecycle",
        "manager_node.test_live_audit",
        "manager_node.test_live_audit_report_upload",
        "manager_node.test_live_audit_set_names",
        "manager_node.test_live_audit_sets",
        "manager_node.test_live_audit_symbols",
        "manager_node.test_live_audit_terminals",
        "manager_node.test_live_audit_wrapper",
        "manager_node.test_manual_check",
        "manager_node.test_manual_portfolio",
        "manager_node.test_manual_portfolio_universe",
        "manager_node.test_notifications",
        "manager_node.test_pause_resume",
        "manager_node.test_portfolio_save",
        "manager_node.test_queue",
        "manager_node.test_regression",
        "manager_node.test_repair_phases",
        "manager_node.test_runs_pagination",
        "manager_node.test_settings",
        "manager_node.test_status",
        "manager_node.test_stop",
        "manager_node.test_universe",
    ),
    # Lanzamiento de MT5: terminales, sets, informes y diagnosticos del tester.
    "runner": (
        "backtest.test_crash_log",
        "backtest.test_multiterminal_cleanup",
        "backtest.test_run_tests_parallel",
        "backtest.test_run_tests_reports",
        "backtest.test_run_tests_runtime",
        "backtest.test_run_tests_sets_config",
        "backtest.test_run_tests_symbol_policy",
        "backtest.test_run_tests_watchdog_history",
        "backtest.test_run_tests_watchdog_progress",
        "backtest.test_terminal_release",
        "backtest.test_terminal_server_guard",
        "portfolio.test_mt5_report",
        "ubs.diagnostics.test_incompatible_volume_diagnostics",
        "ubs.diagnostics.test_invalid_stops_diagnostics",
    ),
    # Pantallas, ajustes y avisos de la aplicacion de escritorio.
    "ui": (
        "integrations.test_telegram_notify",
        "ubs.agent.test_ubs_report_lookup",
        "ubs.agent.test_ubs_results_continuation",
        "ubs.scoring.test_ubs_search_audit",
        "ubs.ui.test_ubs_auditor_pending",
        "ubs.ui.test_ubs_auditor_rows",
        "ubs.ui.test_ubs_auditor_ui",
        "ubs.ui.test_ubs_complete_run_button",
        "ubs.ui.test_ubs_final_tick_run_args",
        "ubs.ui.test_ubs_final_tick_shared_rules",
        "ui.test_app_script_commands",
        "ui.test_app_ui_output_queue",
        "ui.test_app_ui_paths",
        "ui.test_app_ui_theme",
        "ui.test_settings_logic",
    ),
    # Utilidades de tools/: auditorias, repuntuaciones y copias de memoria.
    "herramientas": (
        "ubs.agent.test_ubs_backup",
        "ubs.scoring.test_audit_generalization_v2",
        "ubs.scoring.test_fast_rescore_from_metrics",
        "ubs.scoring.test_rescore_generalization_v2",
        "ubs.sets.test_ubs_mutation_key_repair",
    ),
}

# El grupo que la puerta corre siempre, con o sin suite completa.
GUARD_GROUP = "guardas"


def group_names() -> tuple[str, ...]:
    return tuple(TEST_GROUPS)


def modules_for(group: str) -> tuple[str, ...]:
    try:
        return TEST_GROUPS[group]
    except KeyError:
        raise ValueError(
            f"Grupo de pruebas desconocido: {group}. Grupos: {', '.join(group_names())}"
        ) from None


def unittest_targets(group: str) -> tuple[str, ...]:
    return tuple(f"tests.{module}" for module in modules_for(group))


def grouped_modules() -> list[str]:
    """Todos los modulos del mapa, con repeticiones si alguna hay."""
    return [module for modules in TEST_GROUPS.values() for module in modules]


def discovered_modules(tests_dir: Path | None = None) -> set[str]:
    base = tests_dir or TESTS_DIR
    return {
        str(path.relative_to(base).with_suffix("")).replace("\\", "/").replace("/", ".")
        for path in base.rglob("test_*.py")
    }


def orphan_modules(tests_dir: Path | None = None) -> set[str]:
    """Modulos de prueba que existen pero no estan en ningun grupo."""
    return discovered_modules(tests_dir) - set(grouped_modules())


def unknown_modules(tests_dir: Path | None = None) -> set[str]:
    """Modulos citados en el mapa que ya no existen en disco."""
    return set(grouped_modules()) - discovered_modules(tests_dir)


def duplicated_modules() -> set[str]:
    """Modulos repartidos en mas de un grupo."""
    seen: set[str] = set()
    repeated: set[str] = set()
    for module in grouped_modules():
        if module in seen:
            repeated.add(module)
        seen.add(module)
    return repeated


def main() -> int:
    for group, modules in TEST_GROUPS.items():
        print(f"{group}: {len(modules)} modulo(s)")
    total = len(grouped_modules())
    print(f"total repartido: {total} de {len(discovered_modules())} modulos en tests/")
    problems = {
        "sin grupo": sorted(orphan_modules()),
        "inexistentes": sorted(unknown_modules()),
        "repetidos": sorted(duplicated_modules()),
    }
    for label, items in problems.items():
        if items:
            print(f"{label}: {', '.join(items)}")
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
