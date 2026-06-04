from __future__ import annotations

from pathlib import Path

from run_tests import load_experts_from_dir


class DashboardLogicMixin:
    def _compile_args(self) -> list[str]:
        source_dir, source_file = self._compile_source_selection()
        args = ["--source-dir", source_dir]
        if not self.recursive.get():
            args.extend(["--source-file", source_file])
        if self.metaeditor_path.get().strip():
            args.extend(["--metaeditor-path", self.metaeditor_path.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.recursive.get():
            args.append("--recursive")
        return args
    def _count_backtests(self) -> tuple[int, str]:
        if not self.recursive.get():
            _source_dir, source_file = self._compile_source_selection()
            return 1, source_file
        root = self.experts_root.get().strip()
        if not root and self.multiterminal_enabled.get():
            active = self._active_multiterminal_profiles()
            root = str(active[0].get("experts_root") or "") if active else ""
        if not root:
            raise ValueError("Indica la carpeta .ex5 o configura al menos una terminal activa con MQL5\\Experts.")
        experts = load_experts_from_dir(Path(root).expanduser(), recursive=True)
        return len(experts), root
    def _backtest_args(self) -> list[str]:
        args = ["--template", self.template_path.get(), "--delay", str(self.delay.get())]
        if self.symbol_suffix_enabled.get() and self.symbol_suffix.get().strip():
            args.extend(["--symbol-suffix", self.symbol_suffix.get().strip()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        if not self.recursive.get():
            source_dir, source_file = self._compile_source_selection()
            args.extend(["--experts-dir", self._experts_dir_for_single_file(source_dir)])
            args.extend(["--expert", str(Path(source_file).with_suffix(".ex5"))])
        elif self.experts_root.get().strip():
            args.extend(["--experts-dir", self.experts_root.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.mt5_data_root.get().strip():
            args.extend(["--data-dir", self.mt5_data_root.get()])
        if self.recursive.get():
            args.append("--recursive")
        args.extend(self._multiterminal_args(require_ubs=False))
        return args
    def _run_compile(self) -> None:
        try:
            args = self._compile_args()
            total, target = self._count_compile_sources()
        except Exception as exc:
            self._show_error("No se pudo preparar la compilacion", str(exc))
            return
        details = [
            f"Accion: Compilar .mq5",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Total compilaciones: {total}",
        ]
        if self._confirm_execution_start("Confirmar compilacion", total, details):
            self._run_script("compile_mq5.py", args)
    def _run_backtests(self) -> None:
        try:
            args = self._backtest_args()
            total, target = self._count_backtests()
        except Exception as exc:
            self._show_error("No se pudo preparar backtests", str(exc))
            return
        details = [
            "Accion: Ejecutar backtests",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Backtests: {total}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar backtests", total, details):
            self._run_script("run_tests.py", args)
    def _run_full_flow(self) -> None:
        try:
            args = self._full_flow_args()
            compile_total, target = self._count_compile_sources()
            total = compile_total * 2
        except Exception as exc:
            self._show_error("No se pudo preparar el flujo completo", str(exc))
            return
        details = [
            "Accion: Compilar y backtest",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Compilaciones previstas: {compile_total}",
            f"Backtests previstos: {compile_total}",
        ]
        if self._confirm_execution_start("Confirmar flujo completo", total, details):
            self._run_script("compile_and_backtest.py", args)

