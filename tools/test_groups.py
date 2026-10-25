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
        "test_ai_context_index",
        "test_file_length",
        "test_function_length",
        "test_project_rules",
        "test_test_groups",
        "test_undefined_names",
        "test_verify_project",
    ),
    # Generacion, seeds, seleccion y los modos sueltos del agente.
    "agente": (
        "test_ubs_agent_discovery",
        "test_ubs_agent_files",
        "test_ubs_agent_reconcile",
        "test_ubs_agent_rescore",
        "test_ubs_agent_retry_spelling",
        "test_ubs_agent_run",
        "test_ubs_agent_run_modes",
        "test_ubs_agent_seeds",
        "test_ubs_agent_selection",
        "test_ubs_agent_status",
        "test_ubs_agent_variants",
        "test_ubs_run_config",
        "test_ubs_seed_dedup",
        "test_ubs_set_rules",
        "test_ubs_set_stage_params",
    ),
    # Las etapas que contrastan OHLC contra real tick, de candidatos y de
    # seeds, mas robustez, regresiva y veredictos manuales.
    "final_tick": (
        "test_ubs_agent_final_tick_passes",
        "test_ubs_degradation",
        "test_ubs_final_tick_6m",
        "test_ubs_final_tick_lossless_control",
        "test_ubs_final_tick_status_repair",
        "test_ubs_final_tick_ui",
        "test_ubs_manual_status",
        "test_ubs_regression",
        "test_ubs_robustness_ui",
        "test_ubs_seed_final_tick",
    ),
    # Portafolio UBS: construccion, persistencia, margenes y reparaciones.
    "portafolio": (
        "test_app_state_portfolio",
        "test_prepared_candidates",
        "test_risk_profit",
        "test_ubs_portfolio",
        "test_ubs_portfolio_bundles",
        "test_ubs_portfolio_persistence",
        "test_ubs_portfolio_repair",
        "test_ubs_portfolio_strict",
        "test_ubs_risk_profit_repair",
        "test_ubs_risk_profit_repair_button",
    ),
    # Universo del broker, normalizacion, pesos y score.
    "universo": (
        "test_mt5_symbol_extract",
        "test_ubs_account",
        "test_ubs_account_migration",
        "test_ubs_normalization_gen",
        "test_ubs_score",
        "test_ubs_selection",
        "test_ubs_selection_model",
        "test_ubs_symbol_missing_in_journal",
        "test_ubs_symbol_not_exist_status",
        "test_ubs_timeframes",
        "test_ubs_universe_sync_ui",
        "test_ubs_weights",
    ),
    # Runtime del nodo y el protocolo guiado con el manager.
    "nodo": (
        "test_guided_http",
        "test_guided_node",
        "test_manager_node_cleanup",
        "test_manager_node_exclusions",
        "test_manager_node_lifecycle",
        "test_manager_node_live_audit",
        "test_manager_node_live_audit_report_upload",
        "test_manager_node_live_audit_set_names",
        "test_manager_node_live_audit_sets",
        "test_manager_node_live_audit_symbols",
        "test_manager_node_live_audit_terminals",
        "test_manager_node_live_audit_wrapper",
        "test_manager_node_manual_check",
        "test_manager_node_manual_portfolio",
        "test_manager_node_notifications",
        "test_manager_node_pause_resume",
        "test_manager_node_portfolio_save",
        "test_manager_node_queue",
        "test_manager_node_regression",
        "test_manager_node_repair_phases",
        "test_manager_node_runs_pagination",
        "test_manager_node_settings",
        "test_manager_node_status",
        "test_manager_node_stop",
        "test_manager_node_universe",
    ),
    # Lanzamiento de MT5: terminales, sets, informes y diagnosticos del tester.
    "runner": (
        "test_incompatible_volume_diagnostics",
        "test_invalid_stops_diagnostics",
        "test_mt5_report",
        "test_multiterminal_cleanup",
        "test_run_tests_parallel",
        "test_run_tests_reports",
        "test_run_tests_runtime",
        "test_run_tests_sets_config",
        "test_run_tests_symbol_policy",
        "test_run_tests_watchdog_history",
        "test_terminal_release",
    ),
    # Pantallas, ajustes y avisos de la aplicacion de escritorio.
    "ui": (
        "test_app_ui_output_queue",
        "test_app_ui_paths",
        "test_app_ui_theme",
        "test_settings_logic",
        "test_telegram_notify",
        "test_ubs_report_lookup",
        "test_ubs_results_continuation",
        "test_ubs_search_audit",
    ),
    # Utilidades de tools/: auditorias, repuntuaciones y copias de memoria.
    "herramientas": (
        "test_audit_generalization_v2",
        "test_fast_rescore_from_metrics",
        "test_rescore_generalization_v2",
        "test_ubs_backup",
        "test_ubs_mutation_key_repair",
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
    return {path.stem for path in base.glob("test_*.py")}


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
