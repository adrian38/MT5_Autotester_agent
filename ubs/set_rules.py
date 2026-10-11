"""Reglas de generacion inmutables que todo .set del proyecto debe cumplir.

Una regla aqui no es una preferencia del agente: es una invariante del fichero.
Ningun camino que cree o edite un .set puede dejarla sin aplicar, y ninguna
pantalla puede marcar sus claves como mutables.

Regla NFP: los cinco parametros del filtro Non-Farm Payroll estan fijados. Los
cuatro de comportamiento son constantes, y `EnableNFP_Filter` depende solo de
`Run_Strategy`: la estrategia 1 (Breakout Support/Resistance) opera contra
niveles que el NFP rompe por pura volatilidad y se pausa; la 2 (Volatility
Breakout) y la 3 (Range) viven de esa volatilidad y no se pausan.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ubs.set_utils import force_fixed_lot_text, read_set_with_encoding, write_set_text


RUN_STRATEGY_KEY = "Run_Strategy"
NFP_FILTER_KEY = "EnableNFP_Filter"
NFP_CONSTANT_VALUES: dict[str, str] = {
    "NFP_CloseOpenTrades": "true",
    "NFP_ClosePendingOrders": "true",
    "NFP_MinutesBefore": "50",
    "NFP_MinutesAfter": "30",
}
NFP_FILTER_BY_RUN_STRATEGY: dict[str, str] = {"1": "true", "2": "false", "3": "false"}
NFP_RULE_KEYS: tuple[str, ...] = (NFP_FILTER_KEY, *NFP_CONSTANT_VALUES)

# Marcas con las que se deduce la estrategia de un .set que perdio su
# Run_Strategy; son las mismas que usa `infer_missing_run_strategy_from_set`.
_STRATEGY_MARKER_KEYS: dict[str, tuple[str, ...]] = {
    "1": ("ST1_Timeframe", "Entry_Timing"),
    "2": ("VolTimeframe",),
    "3": ("RNG_Timeframe",),
}


@dataclass(frozen=True)
class NfpRuleResult:
    """Resultado de aplicar la regla NFP a un .set."""

    text: str
    run_strategy: str
    changed: tuple[str, ...]
    absent: tuple[str, ...]


def split_line_ending(line: str) -> tuple[str, str]:
    """Separa el contenido de la linea de su terminador."""
    content = line.rstrip("\r\n")
    return content, line[len(content):]


def parse_set_line(line: str) -> tuple[str, str] | None:
    """Devuelve (clave, valor actual) de una linea de parametro, o None."""
    content, _ = split_line_ending(line)
    if "=" not in content or content.lstrip().startswith(";"):
        return None
    key, raw_value = content.split("=", 1)
    return key.strip(), raw_value.split("||")[0].strip()


def set_line_values(lines: list[str]) -> dict[str, str]:
    """Valor actual de cada clave, ignorando comentarios y rangos ``||``."""
    values: dict[str, str] = {}
    for line in lines:
        parsed = parse_set_line(line)
        if parsed is not None:
            values.setdefault(parsed[0], parsed[1])
    return values


def _marker_active(values: dict[str, str], keys: tuple[str, ...]) -> bool:
    return any((values.get(key) or "").strip() not in {"", "0"} for key in keys)


def infer_run_strategy(values: dict[str, str]) -> str:
    """Estrategia declarada o, si falta, la unica que el .set tiene activa."""
    current = (values.get(RUN_STRATEGY_KEY) or "").strip()
    if current in NFP_FILTER_BY_RUN_STRATEGY:
        return current
    active = [
        strategy
        for strategy, keys in _STRATEGY_MARKER_KEYS.items()
        if _marker_active(values, keys)
    ]
    return active[0] if len(active) == 1 else ""


def nfp_expected_values(run_strategy: str) -> dict[str, str]:
    """Valores que la regla exige; sin estrategia conocida no fija el filtro."""
    expected = dict(NFP_CONSTANT_VALUES)
    forced = NFP_FILTER_BY_RUN_STRATEGY.get((run_strategy or "").strip())
    if forced is not None:
        expected[NFP_FILTER_KEY] = forced
    return expected


def nfp_rule_violations(values: dict[str, str], run_strategy: str = "") -> dict[str, str]:
    """Claves presentes que incumplen la regla, con el valor que les toca."""
    strategy = (run_strategy or "").strip() or infer_run_strategy(values)
    return {
        key: wanted
        for key, wanted in nfp_expected_values(strategy).items()
        if key in values and values[key] != wanted
    }


def _rewrite_current_value(line: str, value: str) -> str:
    content, ending = split_line_ending(line)
    lhs, raw_value = content.split("=", 1)
    if "||" in raw_value:
        parts = raw_value.split("||")
        parts[0] = value
        return f"{lhs}={'||'.join(parts)}{ending}"
    return f"{lhs}={value}{ending}"


def apply_nfp_rules_text(text: str, run_strategy: str = "") -> NfpRuleResult:
    """Fija las claves NFP presentes y deja el resto del fichero intacto.

    Se reescriben solo las lineas que incumplen, conservando su terminador: un
    .set sin bloque NFP (otra version del EA) no recibe claves inventadas, se
    anota en ``absent`` y se devuelve sin tocar.
    """
    lines = text.splitlines(keepends=True)
    values = set_line_values(lines)
    strategy = (run_strategy or "").strip() or infer_run_strategy(values)
    expected = nfp_expected_values(strategy)
    changed: list[str] = []
    for index, line in enumerate(lines):
        parsed = parse_set_line(line)
        if parsed is None:
            continue
        key, current = parsed
        wanted = expected.get(key)
        if wanted is None or current == wanted:
            continue
        lines[index] = _rewrite_current_value(line, wanted)
        changed.append(key)
    absent = tuple(key for key in NFP_RULE_KEYS if key not in values)
    if not changed:
        return NfpRuleResult(text, strategy, (), absent)
    return NfpRuleResult("".join(lines), strategy, tuple(changed), absent)


def apply_nfp_rules_file(path: Path) -> NfpRuleResult:
    """Aplica la regla sobre el fichero y lo reescribe solo si cambia algo."""
    text, encoding = read_set_with_encoding(path)
    result = apply_nfp_rules_text(text)
    if result.changed:
        write_set_text(path, result.text, encoding)
    return result


def normalize_generated_set_text(text: str) -> tuple[str, set[str], set[str]]:
    """Lotaje fijo 0.01 mas las reglas inmutables, en un solo paso.

    Es el normalizador de todo .set que el proyecto crea: variantes, semillas
    preparadas e importadas. Sustituye a ``force_fixed_lot_text`` en esos
    puntos para que una regla nueva no dependa de recordar cada llamada.
    """
    normalized, found, missing = force_fixed_lot_text(text)
    return apply_nfp_rules_text(normalized).text, found, missing
