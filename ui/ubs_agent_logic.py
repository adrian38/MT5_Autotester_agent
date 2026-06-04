from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from run_tests import infer_tester_fields_from_set, load_set_files


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSAgentLogicMixin:
    def _ubs_generator_args(self, *, continue_last: bool = False) -> list[str]:
        source_dir = (
            Path(self.set_files_root.get().strip()).expanduser()
            if self.set_files_root.get().strip()
            else BASE_DIR / "sets" / "ubs_ready"
        )
        if not continue_last:
            source_dir = self._ubs_generator_source_dir()
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
        generations = int(self.ubs_generation_count.get())
        variants = int(self.ubs_variants_per_seed.get())
        max_seeds = int(self.ubs_max_seeds.get())
        continuation_info: dict[str, object] = {}
        if continue_last:
            continuation_info = self._ubs_continuation_info()
            if not continuation_info.get("available"):
                raise ValueError(str(continuation_info.get("message") or "No hay iteracion UBS pendiente para continuar."))
            generations = max(1, int(continuation_info["remaining"]))
            variants = int(continuation_info["variants_per_seed"])
            max_seeds = int(continuation_info["max_seeds"])
        if generations <= 0:
            raise ValueError("Generaciones UBS debe ser mayor que 0.")
        if variants <= 0:
            raise ValueError("Variantes por set debe ser mayor que 0.")
        if max_seeds < 0:
            raise ValueError("Max seeds/gen no puede ser negativo.")
        args = [
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--memory", str(BASE_DIR / "outputs" / "ubs_memory.sqlite"),
            "--template", self.template_path.get(),
            "--generations", str(generations),
            "--variants-per-seed", str(variants),
            "--max-seeds", str(max_seeds),
            "--delay", str(self.delay.get()),
        ]
        if self.ubs_agent_from_date.get().strip():
            args.extend(["--from-date", self.ubs_agent_from_date.get().strip()])
        if self.ubs_agent_to_date.get().strip():
            args.extend(["--to-date", self.ubs_agent_to_date.get().strip()])
        if continue_last:
            args.append("--continue-last-run")
        args.extend(self._ubs_score_args())
        should_execute_backtests = (
            bool(continuation_info.get("execute_backtests"))
            if continue_last
            else self.ubs_agent_execute.get()
        )
        if should_execute_backtests:
            args.append("--execute-backtests")
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args
    def _run_ubs_generator(self) -> None:
        self._run_ubs_agent(continue_last=False)
    def _run_ubs_continue(self) -> None:
        self._run_ubs_agent(continue_last=True)
    def _run_ubs_agent(self, *, continue_last: bool) -> None:
        try:
            args = self._ubs_generator_args(continue_last=continue_last)
            total, target = (
                self._count_ubs_continuation_generations()
                if continue_last
                else self._count_ubs_generations()
            )
            continuation_info = self._ubs_continuation_info() if continue_last else {}
        except Exception as exc:
            self._show_error("No se pudo iniciar Agente UBS", str(exc))
            return
        pending_count = int(continuation_info.get("pending_count") or 0)
        new_sets = max(0, total - pending_count)
        shown_generations = continuation_info.get("remaining", self.ubs_generation_count.get())
        shown_variants = continuation_info.get("variants_per_seed", self.ubs_variants_per_seed.get())
        shown_max_seeds = continuation_info.get("max_seeds", self.ubs_max_seeds.get())
        shown_backtests = bool(continuation_info.get("execute_backtests")) if continue_last else self.ubs_agent_execute.get()
        details = [
            f"Accion: {'Continuar iteracion UBS' if continue_last else 'Agente UBS'}",
            f"Seeds: {target}",
            f"Generaciones nuevas restantes: {shown_generations}",
            f"Variantes por set: {shown_variants}",
            f"Max seeds/gen: {shown_max_seeds}",
            f"Backtests: {'si' if shown_backtests else 'no'}",
            f"Pass: PF>={self.ubs_pass_min_profit_factor.get().strip()} | DD<={self.ubs_pass_max_drawdown_pct.get().strip()}% | Trades>={self.ubs_pass_min_trades.get()}",
            f"Pass: Profit neto>{self.ubs_pass_min_net_profit.get().strip()} | Recovery>={self.ubs_pass_min_recovery_factor.get().strip()}",
            f"Backtests pendientes existentes: {pending_count}",
            f"Sets nuevos previstos: {new_sets}",
        ]
        if shown_backtests:
            details.extend(self._multiterminal_execution_details())
        title = "Confirmar continuacion UBS" if continue_last else "Confirmar Agente UBS"
        if self._confirm_execution_start(title, total, details):
            self._run_script("ubs_agent.py", args)

    def _save_ubs_agent_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Agente UBS", str(exc))
            return
        self.status_text.set("Configuracion Agente UBS guardada")
        messagebox.showinfo("Agente UBS", "La configuracion del Agente UBS se guardo correctamente.")

    def _count_ubs_tests(self) -> tuple[int, str]:
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            files = load_set_files(Path(set_dir).expanduser(), None, recursive=True)
            return len(files), set_dir
        return 1, self._required_ubs_set_file()

    def _ubs_generator_source_dir(self) -> Path:
        set_dir = self.set_files_root.get().strip() or str(BASE_DIR / "sets" / "ubs_ready")
        source_dir = Path(set_dir).expanduser()
        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError(f"No existe la carpeta de seeds UBS: {source_dir}")
        return source_dir

    def _count_ubs_generations(self) -> tuple[int, str]:
        source_dir = self._ubs_generator_source_dir()
        files = load_set_files(source_dir, None, recursive=True)
        if not files:
            return 0, str(source_dir)
        return self._planned_ubs_generation_total(len(files)), str(source_dir)

    def _planned_ubs_generation_total(
        self,
        seed_files: int,
        *,
        generations: int | None = None,
        variants: int | None = None,
        max_seeds: int | None = None,
    ) -> int:
        generations = max(0, int(self.ubs_generation_count.get() if generations is None else generations))
        variants = max(0, int(self.ubs_variants_per_seed.get() if variants is None else variants))
        max_seeds = max(0, int(self.ubs_max_seeds.get() if max_seeds is None else max_seeds))
        seed_count = seed_files if max_seeds == 0 else min(seed_files, max_seeds)
        total = 0
        current = seed_count
        for _ in range(generations):
            produced = current * variants
            total += produced
            current = produced if max_seeds == 0 else min(produced, max_seeds)
        return total

    def _count_ubs_continuation_generations(self) -> tuple[int, str]:
        info = self._ubs_continuation_info()
        if not info.get("available"):
            raise ValueError(str(info.get("message") or "No hay iteracion UBS pendiente para continuar."))
        total = self._planned_ubs_generation_total(
            int(info["seed_count"]),
            generations=int(info["remaining"]),
            variants=int(info["variants_per_seed"]),
            max_seeds=int(info["max_seeds"]),
        )
        pending_count = int(info.get("pending_count") or 0)
        total += pending_count
        if pending_count:
            target = f"memoria run #{info['run_id']} gen {info['pending_generation']} sin backtest -> luego faltan {info['remaining']}"
        else:
            target = f"memoria run #{info['run_id']} gen {info['latest_generation']} -> faltan {info['remaining']}"
        return total, target

    def _score_float(self, variable: tk.StringVar, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
        raw = variable.get().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} debe ser numerico.") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} no puede ser menor que {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} no puede ser mayor que {maximum}.")
        return value

    def _score_args_from_vars(
        self,
        *,
        min_net_profit_var: tk.StringVar,
        min_profit_factor_var: tk.StringVar,
        min_trades_var: tk.IntVar,
        max_drawdown_pct_var: tk.StringVar,
        min_recovery_factor_var: tk.StringVar,
        context: str,
    ) -> list[str]:
        min_net_profit = self._score_float(min_net_profit_var, f"{context} profit neto min")
        min_profit_factor = self._score_float(min_profit_factor_var, f"{context} profit factor min", minimum=0)
        max_drawdown_pct = self._score_float(max_drawdown_pct_var, f"{context} DD max %", minimum=0)
        min_recovery_factor = self._score_float(min_recovery_factor_var, f"{context} recovery min")
        min_trades = int(min_trades_var.get())
        if min_trades < 0:
            raise ValueError(f"{context} trades min no puede ser menor que 0.")
        return [
            "--min-net-profit", str(min_net_profit),
            "--min-profit-factor", str(min_profit_factor),
            "--min-trades", str(min_trades),
            "--max-drawdown-pct", str(max_drawdown_pct),
            "--min-recovery-factor", str(min_recovery_factor),
        ]

    def _ubs_score_args(self) -> list[str]:
        return self._score_args_from_vars(
            min_net_profit_var=self.ubs_pass_min_net_profit,
            min_profit_factor_var=self.ubs_pass_min_profit_factor,
            min_trades_var=self.ubs_pass_min_trades,
            max_drawdown_pct_var=self.ubs_pass_max_drawdown_pct,
            min_recovery_factor_var=self.ubs_pass_min_recovery_factor,
            context="Agente UBS",
        )

    def _ubs_seed_score_args(self) -> list[str]:
        return self._score_args_from_vars(
            min_net_profit_var=self.ubs_seed_pass_min_net_profit,
            min_profit_factor_var=self.ubs_seed_pass_min_profit_factor,
            min_trades_var=self.ubs_seed_pass_min_trades,
            max_drawdown_pct_var=self.ubs_seed_pass_max_drawdown_pct,
            min_recovery_factor_var=self.ubs_seed_pass_min_recovery_factor,
            context="Seeds UBS",
        )

    def _ubs_tester_args(self) -> list[str]:
        args = [
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
            "--infer-tester-from-set",
        ]
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            args.extend(["--set-dir", set_dir])
            args.append("--recursive")
        else:
            set_file = self._required_ubs_set_file()
            args.extend(["--set-file", set_file])
        if self.symbol_suffix_enabled.get() and self.symbol_suffix.get().strip():
            args.extend(["--symbol-suffix", self.symbol_suffix.get().strip()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        if self.experts_root.get().strip():
            args.extend(["--experts-dir", self.experts_root.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.mt5_data_root.get().strip():
            args.extend(["--data-dir", self.mt5_data_root.get()])
        return args

    def _run_ubs_tester(self) -> None:
        try:
            args = self._ubs_tester_args()
            total, target = self._count_ubs_tests()
            missing_symbol_sets = self._ubs_sets_without_inferred_symbol()
        except Exception as exc:
            self._show_error("No se pudo iniciar Tester UBS", str(exc))
            return
        details = [
            "Accion: Tester UBS",
            f"Modo: {'recursivo' if self.recursive.get() else 'set unico'}",
            f"Set(s): {target}",
            f"Total backtests: {total}",
        ]
        details.extend(self._multiterminal_execution_details())
        if missing_symbol_sets:
            self._warn_ubs_template_symbol_fallback(missing_symbol_sets)
        if self._confirm_execution_start("Confirmar Tester UBS", total, details):
            self._run_script("run_tests.py", args)

    def _ubs_set_paths(self) -> list[Path]:
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            return load_set_files(Path(set_dir).expanduser(), None, recursive=True)

        set_file = Path(self._required_ubs_set_file()).expanduser()
        if not set_file.exists():
            raise FileNotFoundError(f"No existe el set file: {set_file}")
        return [set_file]

    def _ubs_sets_without_inferred_symbol(self) -> list[Path]:
        missing: list[Path] = []
        for set_file in self._ubs_set_paths():
            inferred = infer_tester_fields_from_set(set_file)
            if not inferred.get("Symbol", "").strip():
                missing.append(set_file)
        return missing

    def _warn_ubs_template_symbol_fallback(self, set_files: list[Path]) -> None:
        symbol_var = self.tester_vars.get("Symbol")
        template_symbol = symbol_var.get().strip() if symbol_var else ""
        template_symbol = template_symbol or "(vacio)"
        shown = "\n".join(f"- {path.name}" for path in set_files[:12])
        if len(set_files) > 12:
            shown += f"\n- ... y {len(set_files) - 12} mas"
        messagebox.showwarning(
            "Symbol no inferido",
            "No pude inferir el Symbol desde uno o mas .set.\n\n"
            "Se usara el template como esta para esos tests.\n"
            f"Symbol actual del template: {template_symbol}\n\n"
            f"Sets afectados:\n{shown}",
        )

    def _required_ubs_ex5_file(self) -> str:
        ex5_file = self.ubs_ex5_file.get().strip()
        if not ex5_file:
            raise ValueError("Archivo .ex5 UBS es obligatorio para Tester UBS.")
        if Path(ex5_file).suffix.lower() != ".ex5":
            raise ValueError("Archivo .ex5 UBS debe ser un archivo .ex5.")
        return ex5_file

    def _required_ubs_set_file(self) -> str:
        set_file = self.ubs_set_file.get().strip()
        if not set_file:
            raise ValueError("Archivo .set UBS es obligatorio cuando Recursivo esta apagado.")
        if Path(set_file).suffix.lower() != ".set":
            raise ValueError("Archivo .set UBS debe ser un archivo .set.")
        return set_file

