from __future__ import annotations

import json
import sys
from pathlib import Path

from run_tests import apply_symbol_map, apply_symbol_suffix, parse_symbol_map
from ubs.account import axi_cash_future_family_targets, default_symbol_map_for_broker
from ubs.universe import canonical_symbol


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseSymbolsMixin:
    """Simbolos canonicos, alias de senal y busqueda del universo."""

    def _canonical_ubs_symbol(
        self,
        symbol: str,
        aliases: dict[str, str],
        *,
        symbol_map: dict[str, str] | None = None,
        suffix_universe: dict[str, str] | None = None,
        symbol_suffix: str = "",
        futures_suffix: str = "",
        shares_suffix: str = "",
    ) -> str:
        mapped = apply_symbol_map(str(symbol or ""), symbol_map or {})
        suffixed = apply_symbol_suffix(
            mapped,
            symbol_suffix,
            futures_suffix,
            shares_suffix,
            suffix_universe,
        )
        return canonical_symbol(suffixed, aliases)

    def _ubs_seed_row_canonical_symbols(
        self,
        row: object,
        universe_symbols: tuple[str, ...],
        aliases: dict[str, str],
        symbol_map: dict[str, str],
        suffix_universe: dict[str, str],
        symbol_suffix: str,
        futures_suffix: str,
        shares_suffix: str,
    ) -> tuple[str, ...]:
        try:
            raw_symbol = str(row["symbol"] or "")  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            raw_symbol = ""
        metric_symbol = self._ubs_row_metric_symbol(row)
        primary = metric_symbol or raw_symbol
        targets = []
        if primary:
            targets.append(
                self._canonical_ubs_symbol(
                    primary,
                    aliases,
                    symbol_map=symbol_map,
                    suffix_universe=suffix_universe,
                    symbol_suffix=symbol_suffix,
                    futures_suffix=futures_suffix,
                    shares_suffix=shares_suffix,
                )
            )
        for source in (metric_symbol, raw_symbol, apply_symbol_map(raw_symbol, symbol_map)):
            for target in axi_cash_future_family_targets(source, universe_symbols):
                targets.append(
                    self._canonical_ubs_symbol(
                        target,
                        aliases,
                        symbol_map=symbol_map,
                        suffix_universe=suffix_universe,
                        symbol_suffix=symbol_suffix,
                        futures_suffix=futures_suffix,
                        shares_suffix=shares_suffix,
                    )
                )
        return tuple(dict.fromkeys(target for target in targets if target))

    def _ubs_universe_suffix_config(self) -> tuple[str, str, str]:
        enabled = getattr(self, "symbol_suffix_enabled", None)
        if enabled is not None and not bool(enabled.get()):
            return "", "", ""
        suffix_var = getattr(self, "symbol_suffix", None)
        futures_var = getattr(self, "symbol_futures_suffix", None)
        shares_var = getattr(self, "symbol_shares_suffix", None)
        suffix = suffix_var.get().strip() if suffix_var is not None else ""
        futures_suffix = futures_var.get().strip() if futures_var is not None else ""
        shares_suffix = shares_var.get().strip() if shares_var is not None else ""
        return suffix, futures_suffix, shares_suffix

    def _ubs_universe_symbol_map(self) -> dict[str, str]:
        parts = [default_symbol_map_for_broker(self._ubs_broker())]
        enabled = getattr(self, "symbol_map_enabled", None)
        custom = getattr(self, "symbol_map", None)
        if enabled is not None and bool(enabled.get()) and custom is not None and custom.get().strip():
            parts.append(custom.get().strip())
        try:
            return parse_symbol_map(",".join(part for part in parts if part.strip()))
        except ValueError:
            return parse_symbol_map(parts[0])

    def _ubs_universe_signal_aliases(
        self,
        aliases: dict[str, str],
        symbol_map: dict[str, str],
        suffix_universe: dict[str, str],
        symbol_suffix: str,
        futures_suffix: str,
        shares_suffix: str,
    ) -> dict[str, str]:
        signal_aliases = {str(key).upper(): str(value).upper() for key, value in aliases.items()}
        sources = set(symbol_map) | set(suffix_universe)
        for source in sources:
            canonical = self._canonical_ubs_symbol(
                source,
                aliases,
                symbol_map=symbol_map,
                suffix_universe=suffix_universe,
                symbol_suffix=symbol_suffix,
                futures_suffix=futures_suffix,
                shares_suffix=shares_suffix,
            )
            if canonical:
                signal_aliases[str(source).upper()] = canonical.upper()
        return signal_aliases

    def _ubs_row_metric_symbol(self, row: object) -> str:
        try:
            raw = row["metrics_json"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return ""
        if not raw:
            return ""
        try:
            metrics = json.loads(str(raw))
        except json.JSONDecodeError:
            return ""
        if not isinstance(metrics, dict):
            return ""
        return str(metrics.get("symbol") or "").strip()

    def _canonical_ubs_symbol_set(self, symbols: set[str], aliases: dict[str, str]) -> set[str]:
        return {
            self._canonical_ubs_symbol(symbol, aliases).upper()
            for symbol in symbols
            if str(symbol or "").strip()
        }

    def _selected_ubs_universe_symbols(self) -> set[str]:
        symbols = set(self.ubs_universe_checked)
        if not symbols and hasattr(self, "ubs_universe_assets_tree"):
            selected = self.ubs_universe_assets_tree.selection()
            symbols = {
                self.ubs_universe_paths.get(item, {}).get("symbol", "")
                for item in selected
            }
            symbols.discard("")
        return symbols

    def _active_ubs_symbol_policy(self, aliases: dict[str, str]) -> tuple[set[str], set[str]]:
        disabled = self._canonical_ubs_symbol_set(self._load_disabled_ubs_symbols(), aliases)
        seed_enabled = self._canonical_ubs_symbol_set(self._load_seed_enabled_disabled_ubs_symbols(), aliases)
        return disabled, seed_enabled & disabled

    def _empty_ubs_stat(self) -> dict[str, object]:
        return {
            "scores": [],
            "weights": [],
            "weight_groups": {},
            "tests": 0,
            "accepted": 0,
            "pending": 0,
            "best": None,
        }

    def _tag_for_weight(self, value: float | None) -> str:
        if value is None:
            return "neutral"
        return "positive" if value >= 0 else "negative"

    def _ubs_universe_search_terms(self, var_name: str) -> list[str]:
        variable = getattr(self, var_name, None)
        if variable is None:
            return []
        return [term for term in variable.get().strip().lower().split() if term]

    def _ubs_universe_asset_matches_search(self, group: str, symbol: str, aliases: list[str], terms: list[str]) -> bool:
        if not terms:
            return True
        haystack = " ".join([group, symbol, *aliases]).lower()
        return all(term in haystack for term in terms)

    def _ubs_universe_tf_matches_search(self, period: str, terms: list[str]) -> bool:
        if not terms:
            return True
        haystack = period.lower()
        return all(term in haystack for term in terms)

    def _clear_ubs_universe_search(self) -> None:
        changed = False
        for variable in (getattr(self, "ubs_universe_asset_search", None), getattr(self, "ubs_universe_tf_search", None)):
            if variable is not None and variable.get():
                variable.set("")
                changed = True
        if not changed:
            self._refresh_ubs_universe()
