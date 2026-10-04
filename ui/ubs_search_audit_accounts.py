from __future__ import annotations

from pathlib import Path
import sys

from ubs.path_utils import resolve_workspace_path


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_search_audit_base import (  # noqa: F401  fachada del modulo
    AUDIT_FINAL_STATUSES,
    audit_nonfinal_count,
)


class UBSSearchAuditAccountsMixin:
    """Cuenta real de MT5 en los reportes de cada proceso."""

    def _report_account_lines(self, ctx, expected_label, account_by_gen,
                              account_mismatches, account_unknowns,
                              checked_report_files, process_by_column) -> None:
        """Lineas del informe con la cuenta real por generacion."""
        ctx.line("\nCUENTA REAL EN REPORTES POR GENERACION")
        ctx.line("-" * 96)
        ctx.line(
            f"reportes inspeccionados={len(checked_report_files)} | "
            f"mismatch cuenta={len(account_mismatches)} | sin encabezado={len(account_unknowns)}"
        )
        for gen_no in sorted(account_by_gen):
            parts: list[str] = []
            for process in [item[0] for item in process_by_column]:
                data = account_by_gen[gen_no].get(process)
                if not data:
                    continue
                parts.append(f"{process}: ok={data['ok']} mismatch={data['mismatch']} unknown={data['unknown']}")
            ctx.line(f"  gen {gen_no}: " + " | ".join(parts))
        if account_mismatches:
            ctx.line("  mismatches:")
            for item in account_mismatches[:80]:
                ctx.line(f"    - {item}")
            if len(account_mismatches) > 80:
                ctx.line(f"    ... {len(account_mismatches) - 80} mas")
        if account_unknowns:
            ctx.line("  sin encabezado legible:")
            for item in account_unknowns[:40]:
                ctx.line(f"    - {item}")
            if len(account_unknowns) > 40:
                ctx.line(f"    ... {len(account_unknowns) - 40} mas")

    def _account_row_totals(
        self, row, ctx, expected_context, expected_broker, expected_label, account_cache,
        account_by_gen, account_ids_by_gen, account_bad_types_by_process,
        account_bad_ids_by_process, account_details_by_process,
        account_mismatches, account_unknowns, checked_report_files, process_by_column,
    ) -> None:
        """Suma una fila de reporte al recuento de cuentas reales."""
        gen_no = int(row["generation"] or 0)
        for process, column in process_by_column:
            raw_path = str(row[column] or "").strip()
            if not raw_path:
                continue
            report_path = resolve_workspace_path(raw_path)
            if not report_path.exists():
                continue
            cache_key = str(report_path.resolve()).casefold()
            checked_report_files.add(cache_key)
            header, detected_broker, detected_account = account_cache.get(cache_key, ("", "", ""))
            if not header and cache_key not in account_cache:
                header, detected_broker, detected_account = self._detect_ubs_report_account_header(report_path, expected_broker)
                account_cache[cache_key] = (header, detected_broker, detected_account)
            detected_label = (
                self._ubs_account_context_label(detected_broker, detected_account)
                if detected_broker and detected_account
                else ""
            )
            bucket = account_by_gen.setdefault(gen_no, {}).setdefault(
                process,
                {"ok": 0, "mismatch": 0, "unknown": 0},
            )
            id_bucket = account_ids_by_gen.setdefault(gen_no, {}).setdefault(
                process,
                {"ok": set(), "mismatch": set(), "unknown": set()},
            )
            candidate_id = int(row["id"] or 0)
            if not detected_account:
                bucket["unknown"] += 1
                id_bucket["unknown"].add(candidate_id)
                detail = (
                    f"{gen_no}\t{row['id']}\t{Path(str(row['set_path'] or '')).name}\t{process}\t"
                    f"{expected_label}\tSIN_ENCABEZADO\t\t{report_path.name}"
                )
                account_details_by_process.setdefault(process, []).append(detail)
                account_unknowns.append(detail)
            elif (detected_broker, detected_account) != expected_context:
                bucket["mismatch"] += 1
                id_bucket["mismatch"].add(candidate_id)
                account_bad_types_by_process.setdefault(process, {})[detected_label] = (
                    account_bad_types_by_process.setdefault(process, {}).get(detected_label, 0) + 1
                )
                account_bad_ids_by_process.setdefault(process, {}).setdefault(detected_label, set()).add(candidate_id)
                detail = (
                    f"{gen_no}\t{row['id']}\t{Path(str(row['set_path'] or '')).name}\t{process}\t"
                    f"{expected_label}\t{detected_label}\t{header}\t{report_path.name}"
                )
                account_details_by_process.setdefault(process, []).append(detail)
                account_mismatches.append(detail)
            else:
                bucket["ok"] += 1
                id_bucket["ok"].add(candidate_id)

    def _audit_report_accounts(self, ctx, expected_context, expected_broker, expected_label):
        """Cuenta real de MT5 en los reportes, por generacion y proceso."""
        report_account_rows = ctx.rows(
            """
            select c.generation,c.id,c.set_path,
                   c.report_path as base_report,
                   cr.report_path as robust_report,
                   ft.ohlc_report_path as ft_ohlc_report,
                   ft.real_tick_report_path as ft_tick_report,
                   ft6.ohlc_report_path as ft6_ohlc_report,
                   ft6.real_tick_report_path as ft6_tick_report
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id
            left join candidate_final_tick ft on ft.candidate_id=c.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            where c.run_id=?
            order by c.generation,c.id
            """
        )
        process_by_column = [
            ("Base", "base_report"),
            ("Robustez", "robust_report"),
            ("FT corto OHLC", "ft_ohlc_report"),
            ("FT corto Tick", "ft_tick_report"),
            ("FT 6M OHLC", "ft6_ohlc_report"),
            ("FT 6M Tick", "ft6_tick_report"),
        ]
        account_cache: dict[str, tuple[str, str, str]] = {}
        account_by_gen: dict[int, dict[str, dict[str, int]]] = {}
        account_ids_by_gen: dict[int, dict[str, dict[str, set[int]]]] = {}
        account_bad_types_by_process: dict[str, dict[str, int]] = {}
        account_bad_ids_by_process: dict[str, dict[str, set[int]]] = {}
        account_details_by_process: dict[str, list[str]] = {}
        account_mismatches: list[str] = []
        account_unknowns: list[str] = []
        checked_report_files: set[str] = set()
        for row in report_account_rows:
            self._account_row_totals(
                row, ctx, expected_context, expected_broker, expected_label,
                account_cache,
                account_by_gen, account_ids_by_gen, account_bad_types_by_process,
                account_bad_ids_by_process, account_details_by_process,
                account_mismatches, account_unknowns, checked_report_files,
                process_by_column,
            )

        self._report_account_lines(
            ctx, expected_label, account_by_gen, account_mismatches,
            account_unknowns, checked_report_files, process_by_column,
        )
        return {
            "account_by_gen": account_by_gen,
            "account_ids_by_gen": account_ids_by_gen,
            "account_bad_types_by_process": account_bad_types_by_process,
            "account_bad_ids_by_process": account_bad_ids_by_process,
            "account_details_by_process": account_details_by_process,
            "account_mismatches": account_mismatches,
        }

    def _account_gen_totals(self, state, process_names, ok_ids, mismatch_ids,
                            unknown_ids, detail_lines, gen_parts):
        """Totales por generacion de los procesos pedidos."""
        ok_report_total = 0
        mismatch_report_total = 0
        unknown_report_total = 0
        for gen_no in sorted(state["account_by_gen"]):
            gen_ok_reports = 0
            gen_mismatch_reports = 0
            gen_unknown_reports = 0
            gen_ok_ids: set[int] = set()
            gen_mismatch_ids: set[int] = set()
            gen_unknown_ids: set[int] = set()
            for process_name in process_names:
                data = state["account_by_gen"][gen_no].get(process_name)
                if not data:
                    continue
                gen_ok_reports += int(data["ok"])
                gen_mismatch_reports += int(data["mismatch"])
                gen_unknown_reports += int(data["unknown"])
                id_data = state["account_ids_by_gen"].get(gen_no, {}).get(process_name, {})
                gen_ok_ids.update(id_data.get("ok", set()))
                gen_mismatch_ids.update(id_data.get("mismatch", set()))
                gen_unknown_ids.update(id_data.get("unknown", set()))
            if gen_ok_reports or gen_mismatch_reports or gen_unknown_reports:
                gen_parts.append(
                    f"g{gen_no}: ok_rep={gen_ok_reports} "
                    f"m_cand={len(gen_mismatch_ids)}/rep={gen_mismatch_reports} "
                    f"u_cand={len(gen_unknown_ids)}/rep={gen_unknown_reports}"
                )
                ok_report_total += gen_ok_reports
                mismatch_report_total += gen_mismatch_reports
                unknown_report_total += gen_unknown_reports
                ok_ids.update(gen_ok_ids)
                mismatch_ids.update(gen_mismatch_ids)
                unknown_ids.update(gen_unknown_ids)
        return ok_report_total, mismatch_report_total, unknown_report_total

    def _account_process_summary(self, ctx, state, process_names):
        """Resumen de cuenta real para un grupo de procesos."""
        ok_ids: set[int] = set()
        mismatch_ids: set[int] = set()
        unknown_ids: set[int] = set()
        bad_accounts: dict[str, int] = {}
        bad_account_ids: dict[str, set[int]] = {}
        detail_lines: list[str] = []
        gen_parts: list[str] = []
        ok_report_total, mismatch_report_total, unknown_report_total = (
            self._account_gen_totals(
                state, process_names, ok_ids, mismatch_ids, unknown_ids,
                detail_lines, gen_parts,
            )
        )
        for process_name in process_names:
            for account_name, count in state["account_bad_types_by_process"].get(process_name, {}).items():
                bad_accounts[account_name] = bad_accounts.get(account_name, 0) + count
            for account_name, ids in state["account_bad_ids_by_process"].get(process_name, {}).items():
                bad_account_ids.setdefault(account_name, set()).update(ids)
            detail_lines.extend(state["account_details_by_process"].get(process_name, []))
        bad_text = ""
        if bad_accounts:
            bad_text = " | cuenta mal: " + ", ".join(
                f"{name}={len(bad_account_ids.get(name, set()))} cand/{count} rep"
                for name, count in sorted(bad_accounts.items())
            )
        detail = (
            f"ok reportes={ok_report_total} | "
            f"mismatch candidatos={len(mismatch_ids)} reportes={mismatch_report_total} | "
            f"sin encabezado candidatos={len(unknown_ids)} reportes={unknown_report_total}"
            + bad_text
            + (f" | {'; '.join(gen_parts)}" if gen_parts else "")
        )
        tag = "rejected" if mismatch_ids else "accepted"
        if detail_lines:
            detail_lines = ["GEN\tID\tSET\tTEST\tESPERADO\tREPORTE\tHEADER\tARCHIVO", *detail_lines]
        return detail, tag, detail_lines
