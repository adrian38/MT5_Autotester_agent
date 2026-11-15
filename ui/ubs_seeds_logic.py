from __future__ import annotations

import json
from dataclasses import dataclass, field
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from run_tests import KNOWN_TIMEFRAMES
from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path, workspace_path_exists


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_seeds_cleanup import UBSSeedsCleanupMixin
from ui.ubs_seeds_duplicates import UBSSeedsDuplicatesMixin
from ui.ubs_seeds_eval import UBSSeedsEvalMixin
from ui.ubs_seeds_import import UBSSeedsImportMixin
from ui.ubs_seeds_table import UBSSeedsTableMixin


@dataclass
class _SeedRepairTally:
    """Resultado de reparar una tanda de .set de seeds."""

    repaired: int = 0
    unchanged: int = 0
    still_invalid: int = 0
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Linea de estado con el reparto de la reparacion."""
        parts = [f"reparadas={self.repaired}", f"sin cambios={self.unchanged}"]
        if self.still_invalid:
            parts.append(f"aun invalidas={self.still_invalid}")
        if self.failed:
            parts.append(f"fallos={len(self.failed)}")
        return "Reparar sets: " + " | ".join(parts)


class UBSSeedsLogicMixin(
    UBSSeedsEvalMixin,
    UBSSeedsTableMixin,
    UBSSeedsImportMixin,
    UBSSeedsDuplicatesMixin,
    UBSSeedsCleanupMixin,
):
    def _ubs_apply_weights(self) -> None:
        """Check all seeds are evaluated, then unlock and show weights."""
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Calcular pesos", "Sin memoria UBS. Evalúa las semillas primero.")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            seed_files = self._current_ubs_seed_files()
            current_paths = {str(path) for path in seed_files}
            known_paths: set[str] = set()
            pending = conn.execute(
                """
                select count(*) as n
                from seed_scores
                where active=1
                  and (
                    status not in ('accepted','rejected','report_mismatch','disabled_symbol','invalid_seed','symbol_not_exist','trade_disabled')
                    or (status in ('accepted','rejected') and score is null)
                  )
                """
            ).fetchone()
            total = conn.execute(
                "select count(*) as n from seed_scores where active=1"
            ).fetchone()
            if self._sqlite_table_exists(conn, "seed_scores"):
                known_paths = {str(row["seed_path"]) for row in conn.execute("select seed_path from seed_scores where active=1").fetchall()}
            conn.close()
            pending_count = int(pending["n"] if pending else 0)
            total_count = int(total["n"] if total else 0)
            missing_count = len(current_paths - known_paths)
        except sqlite3.Error as exc:
            self._show_error("Error SQLite", str(exc))
            return
        except Exception as exc:
            self._show_error("Error leyendo semillas", str(exc))
            return
        pending_count += missing_count
        total_count += missing_count
        if pending_count > 0:
            messagebox.showwarning(
                "Calcular pesos",
                f"Hay {pending_count} semilla(s) sin evaluar de {total_count} activas.\n\n"
                "Ejecuta 'Evaluar semillas' primero para obtener pesos fiables.",
            )
            return
        self.ubs_weights_locked.set(False)
        self._refresh_ubs_universe()
        messagebox.showinfo("Calcular pesos", "Pesos calculados y aplicados al Universo.")

    def _ubs_seed_override_target(self) -> tuple[str, str] | None:
        """Symbol y timeframe pedidos para el override, ya validados."""
        symbol = self.ubs_seed_override_symbol.get().strip().upper()
        period = self.ubs_seed_override_period.get().strip().upper()
        valid_periods = set(KNOWN_TIMEFRAMES)
        if not symbol:
            self._show_error("Symbol invalido", "Indica el symbol correcto.")
            return None
        if period not in valid_periods:
            self._show_error(
                "Timeframe invalido",
                f"El timeframe debe ser uno de: {', '.join(sorted(valid_periods))}.",
            )
            return None
        return symbol, period

    def _apply_seed_override_row(
        self, conn: sqlite3.Connection, seed_path: str, symbol: str, period: str, now: str
    ) -> bool:
        """Guarda el override de una seed; True si cambia algo de verdad.

        Un override que no cambia symbol/TF ni ForceSymbol no debe invalidar
        evaluaciones existentes: solo se resetean los seeds con cambio real.
        """
        from ubs_agent import load_set_params, write_set_force_symbol

        row = None
        if self._sqlite_table_exists(conn, "seed_scores"):
            row = conn.execute(
                "select symbol, period from seed_scores where seed_path=?",
                (seed_path,),
            ).fetchone()
        same_target = (
            row is not None
            and str(row["symbol"] or "").strip().upper() == symbol
            and str(row["period"] or "").strip().upper() == period
        )
        try:
            physical_seed_path = resolve_workspace_path(seed_path)
            params = load_set_params(physical_seed_path)
        except OSError:
            params = {}
        force_ok = str(params.get("ForceSymbol", "")).strip().upper() == symbol
        if not force_ok:
            write_set_force_symbol(physical_seed_path, physical_seed_path, symbol)
        conn.execute(
            """
            insert into seed_overrides (seed_path, symbol, period, updated_at)
            values (?, ?, ?, ?)
            on conflict(seed_path) do update set
                symbol=excluded.symbol,
                period=excluded.period,
                updated_at=excluded.updated_at
            """,
            (seed_path, symbol, period, now),
        )
        return not (same_target and force_ok)

    @staticmethod
    def _reset_seed_scores_for_override(
        conn: sqlite3.Connection, seed_paths: list[str], symbol: str, period: str
    ) -> None:
        """Deja pendientes las evaluaciones de las seeds que cambiaron."""
        placeholders = ",".join("?" for _ in seed_paths)
        conn.execute(
            f"""
            update seed_scores
            set symbol=?,
                period=?,
                report_path=case when status in ('accepted', 'rejected', 'no_trades', 'report_mismatch') then null else report_path end,
                score=case when status in ('accepted', 'rejected', 'no_trades', 'report_mismatch') then null else score end,
                accepted=case when status in ('accepted', 'rejected', 'no_trades', 'report_mismatch') then null else accepted end,
                metrics_json=case when status in ('accepted', 'rejected', 'no_trades', 'report_mismatch') then null else metrics_json end,
                status=case when status in ('accepted', 'rejected', 'no_trades', 'report_mismatch') then 'pending' else status end
            where seed_path in ({placeholders})
            """,
            (symbol, period, *seed_paths),
        )

    @staticmethod
    def _revalidate_overridden_seed(
        conn: sqlite3.Connection, seed_path: str, symbol: str, period: str, now: str
    ) -> None:
        """Comprueba el .set tras el override y fija su estado resultante."""
        from ubs_agent import validate_seed_backtest_set
        from ubs.models import Seed

        row = conn.execute(
            "select family, run_strategy from seed_scores where seed_path=?",
            (seed_path,),
        ).fetchone()
        seed = Seed(
            resolve_workspace_path(seed_path),
            symbol,
            period,
            str(row["family"] or "") if row else "",
            str(row["run_strategy"] or "") if row else "",
        )
        reasons = validate_seed_backtest_set(seed)
        if reasons:
            conn.execute(
                """
                update seed_scores
                set status='invalid_seed',
                    report_path=null,
                    score=null,
                    accepted=null,
                    metrics_json=?,
                    evaluated_at=?
                where seed_path=?
                """,
                (json.dumps({"reasons": reasons}, ensure_ascii=False), now, seed_path),
            )
            return
        conn.execute(
            """
            update seed_scores
            set status='pending',
                report_path=null,
                score=null,
                accepted=null,
                metrics_json=null,
                evaluated_at=null
            where seed_path=?
              and status not in ('accepted', 'rejected')
            """,
            (seed_path,),
        )

    def _write_ubs_seed_overrides(
        self, seed_paths: list[str], symbol: str, period: str
    ) -> list[str] | None:
        """Aplica el override a todas las seeds; None si la memoria falla."""
        memory_path = self._ubs_memory_path()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = connect_memory(memory_path)
            self._ensure_ubs_seed_override_schema(conn)
            now = datetime.now().isoformat(timespec="seconds")
            changed_paths = [
                seed_path for seed_path in seed_paths
                if self._apply_seed_override_row(conn, seed_path, symbol, period, now)
            ]
            if self._sqlite_table_exists(conn, "seed_scores") and changed_paths:
                self._reset_seed_scores_for_override(conn, changed_paths, symbol, period)
                for seed_path in changed_paths:
                    self._revalidate_overridden_seed(conn, seed_path, symbol, period, now)
            conn.commit()
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            self._show_error("Error guardando seed", str(exc))
            return None
        return changed_paths

    def _save_ubs_seed_override(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una o mas semillas.")
            return
        target = self._ubs_seed_override_target()
        if target is None:
            return
        symbol, period = target
        seed_paths = [info.get("seed_path", "") for info in infos if info.get("seed_path")]
        changed_paths = self._write_ubs_seed_overrides(seed_paths, symbol, period)
        if changed_paths is None:
            return
        unchanged_count = len(seed_paths) - len(changed_paths)
        self.ubs_seed_checked.clear()
        if changed_paths:
            extra = f" ({unchanged_count} sin cambios, evaluacion conservada)" if unchanged_count else ""
            self.status_text.set(
                f"Override aplicado a {len(changed_paths)} seed(s); estado recalculado{extra}"
            )
        else:
            self.status_text.set(
                f"Override sin cambios en {unchanged_count} seed(s); evaluacion conservada"
            )
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_seeds()

    def _repair_selected_ubs_seed_sets(self) -> None:
        infos = self._checked_ubs_seed_infos()
        self._repair_ubs_seed_sets(
            infos,
            title="Reparar sets",
            empty_message="Marca una o mas seeds existentes para reparar.",
            confirm_label="marcada(s)",
        )

    def _repair_all_ubs_seed_sets(self) -> None:
        try:
            seed_files = self._current_ubs_seed_files()
        except Exception as exc:
            self._show_error("Carpeta de seeds no valida", str(exc))
            return
        infos = [{"seed_path": str(path), "active": "1"} for path in seed_files]
        self._repair_ubs_seed_sets(
            infos,
            title="Reparar todas",
            empty_message="No hay seeds activas/existentes para reparar.",
            confirm_label="activa(s)",
        )

    def _store_repaired_seed(
        self, conn: sqlite3.Connection, seed_path: Path, symbol: str, period: str,
        result: dict, row, changed: list, now: str, tally: _SeedRepairTally,
    ) -> None:
        """Guarda el estado de una seed reparada y si sigue siendo invalida."""
        from ubs.models import Seed
        from ubs_agent import validate_seed_backtest_set

        stat = seed_path.stat()
        seed = Seed(
            seed_path,
            symbol,
            period,
            str(row["family"] or "") if row else "",
            str(result.get("run_strategy") or (row["run_strategy"] if row else "") or ""),
        )
        reasons = validate_seed_backtest_set(seed)
        if reasons:
            tally.still_invalid += 1
            status = "invalid_seed"
            metrics_json = json.dumps({"reasons": reasons, "repair": changed}, ensure_ascii=False)
            evaluated_at = now
        else:
            status = "pending"
            metrics_json = None
            evaluated_at = None
        if not self._sqlite_table_exists(conn, "seed_scores"):
            return
        conn.execute(
            """
            update seed_scores
            set seed_mtime=?,
                seed_size=?,
                symbol=?,
                period=?,
                run_strategy=?,
                report_path=null,
                score=null,
                accepted=null,
                metrics_json=?,
                status=?,
                evaluated_at=?
            where seed_path=?
            """,
            (
                float(stat.st_mtime),
                int(stat.st_size),
                symbol,
                period,
                str(result.get("run_strategy") or ""),
                metrics_json,
                status,
                evaluated_at,
                str(seed_path),
            ),
        )

    def _repair_one_ubs_seed_set(
        self, conn: sqlite3.Connection, info: dict[str, str], now: str, tally: _SeedRepairTally
    ) -> None:
        """Rellena ForceSymbol y Run_Strategy de una seed y anota el resultado."""
        from ubs_agent import repair_seed_backtest_set

        seed_path = resolve_workspace_path(info.get("seed_path", ""))
        symbol, period = self._inferred_ubs_seed_fields(seed_path)
        if symbol in {"", "UNKNOWN"} or period in {"", "UNKNOWN"}:
            tally.failed.append(f"{seed_path.name}: no pude inferir Symbol/TF")
            return
        row = None
        if self._sqlite_table_exists(conn, "seed_scores"):
            row = conn.execute(
                "select family, run_strategy from seed_scores where seed_path=?",
                (str(seed_path),),
            ).fetchone()
        try:
            result = repair_seed_backtest_set(seed_path, symbol, period)
        except OSError as exc:
            tally.failed.append(f"{seed_path.name}: {exc}")
            return
        changed = list(result.get("changed") or [])
        if not changed:
            tally.unchanged += 1
            return
        tally.repaired += 1
        self._store_repaired_seed(conn, seed_path, symbol, period, result, row, changed, now, tally)

    def _confirm_seed_repair(
        self, infos: list[dict[str, str]], title: str, empty_message: str, confirm_label: str
    ) -> list[dict[str, str]]:
        """Seeds activas a reparar, ya confirmadas por el usuario."""
        active_infos = [
            info for info in infos
            if info.get("active") != "0" and workspace_path_exists(info.get("seed_path", ""))
        ]
        if not active_infos:
            messagebox.showinfo(title, empty_message)
            return []
        if not messagebox.askyesno(
            title,
            f"Reparar {len(active_infos)} seed(s) {confirm_label}?\n\n"
            "Se rellenara ForceSymbol y, si se puede inferir, Run_Strategy. "
            "Las seeds modificadas quedaran pendientes para reevaluar.",
        ):
            return []
        return active_infos

    def _repair_ubs_seed_sets(
        self,
        infos: list[dict[str, str]],
        *,
        title: str,
        empty_message: str,
        confirm_label: str,
    ) -> None:
        active_infos = self._confirm_seed_repair(infos, title, empty_message, confirm_label)
        if not active_infos:
            return
        memory_path = self._ubs_memory_path()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        tally = _SeedRepairTally()
        now = datetime.now().isoformat(timespec="seconds")
        try:
            conn = connect_memory(memory_path)
            self._ensure_ubs_seed_override_schema(conn)
            for info in active_infos:
                self._repair_one_ubs_seed_set(conn, info, now, tally)
            conn.commit()
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            self._show_error("Error reparando sets", str(exc))
            return
        self.ubs_seed_checked.clear()
        self.status_text.set(tally.summary())
        if tally.failed:
            messagebox.showwarning(title, "\n".join(tally.failed[:12]))
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_seeds()

    def _save_seed_criteria_clicked(self) -> None:
        try:
            self._ubs_seed_score_args()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudieron guardar criterios Seeds", str(exc))
            return
        self.status_text.set("Criterios Seeds guardados")
        self._refresh_ubs_seeds_panel()

    def _apply_seed_criteria_clicked(self) -> None:
        try:
            self._write_ui_settings()
            args = [
                "--rescore-seeds-only",
                "--source-dir", str(self._ubs_generator_source_dir()),
                "--memory", str(self._ubs_memory_path()),
                "--broker", self._ubs_broker(),
                "--account-type", self._ubs_account_type(),
            ]
            args.extend(self._ubs_seed_score_args())
            symbol_map = self._effective_ubs_symbol_map_text()
            if symbol_map:
                args.extend(["--symbol-map", symbol_map])
            args.extend(self._effective_symbol_suffix_args())
        except Exception as exc:
            self._show_error("No se pudieron aplicar criterios Seeds", str(exc))
            return
        self._run_script("ubs_agent.py", args)
