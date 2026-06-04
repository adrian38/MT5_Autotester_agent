from __future__ import annotations

import sys
from pathlib import Path


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

