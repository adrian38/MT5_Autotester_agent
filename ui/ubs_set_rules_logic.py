"""Barrido de las reglas de generacion inmutables sobre los .set existentes."""
from __future__ import annotations

from tkinter import messagebox

from ubs.set_rules import (
    NFP_CONSTANT_VALUES,
    NFP_FILTER_BY_RUN_STRATEGY,
    NFP_FILTER_KEY,
)


RUN_STRATEGY_LABELS = {
    "1": "Breakout Support/Resistance",
    "2": "Volatility Breakout",
    "3": "Range",
}


def set_rules_summary() -> str:
    """Texto de la regla vigente, leido de la propia regla."""
    lines = [
        f"{NFP_FILTER_KEY} queda fijado por estrategia:",
        *[
            f"    Run_Strategy {strategy} ({RUN_STRATEGY_LABELS.get(strategy, strategy)}) → {value}"
            for strategy, value in sorted(NFP_FILTER_BY_RUN_STRATEGY.items())
        ],
        "",
        "Constantes en todos los sets:",
        *[f"    {key} = {value}" for key, value in NFP_CONSTANT_VALUES.items()],
    ]
    return "\n".join(lines)


class UBSSetRulesLogicMixin:
    """Lanza `ubs_set_rules.py` sobre todo el historico de .set."""

    def _ubs_apply_set_rules(self) -> None:
        answer = messagebox.askyesnocancel(
            "Reglas de generación",
            "Aplica las reglas inmutables a TODOS los .set del proyecto\n"
            "(sets, live_sets, sets_1.7, MQL5/Profiles/Tester y outputs).\n\n"
            f"{set_rules_summary()}\n\n"
            "Solo se reescriben los sets que incumplen, con copia previa en\n"
            "outputs/backups/.\n\n"
            "Sí = aplicar     No = solo simular     Cancelar = salir",
        )
        if answer is None:
            return
        args = ["--dry-run"] if not answer else []
        self.status_text.set(
            "Simulando reglas de generación" if args else "Aplicando reglas de generación"
        )
        self._run_script("ubs_set_rules.py", args)
