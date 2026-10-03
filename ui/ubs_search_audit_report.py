from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

from ubs.account import normalize_account_type, normalize_broker
from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path, workspace_path_exists
from ubs.weights import ASSET_ACCEPTED_BONUS, feedback_weight
from ui.ubs_audit_utils import AuditReportFormatter


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_search_audit_base import (  # noqa: F401  fachada del modulo
    AUDIT_FINAL_STATUSES,
    audit_nonfinal_count,
)


from ui.ubs_search_audit_accounts import UBSSearchAuditAccountsMixin
from ui.ubs_search_audit_weights import UBSSearchAuditWeightsMixin


class _RunAuditContext:
    """Consultas del run y lineas del informe compartidas por las secciones."""

    def __init__(self, conn: sqlite3.Connection, run_id: int) -> None:
        self.conn = conn
        self.run_id = run_id
        self.out: list[str] = []

    def rows(self, sql: str, params: tuple[object, ...] | None = None) -> list[sqlite3.Row]:
        return self.conn.execute(sql, self._params(params)).fetchall()

    def one(self, sql: str, params: tuple[object, ...] | None = None) -> int:
        value = self.conn.execute(sql, self._params(params)).fetchone()[0]
        return int(value or 0)

    def counts(self, sql: str, params: tuple[object, ...] | None = None) -> dict[str, int]:
        return {
            str(row[0]): int(row[1] or 0)
            for row in self.conn.execute(sql, self._params(params))
        }

    def line(self, text: str = "") -> None:
        self.out.append(str(text))

    def _params(self, params: tuple[object, ...] | None) -> tuple[object, ...]:
        return (self.run_id,) if params is None else params

    fmt_counts = staticmethod(AuditReportFormatter.fmt_counts)
    stat = staticmethod(AuditReportFormatter.stat)
    parse_json = staticmethod(AuditReportFormatter.parse_json)
    fnum = staticmethod(AuditReportFormatter.fnum)


class UBSSearchAuditReportMixin(
    UBSSearchAuditAccountsMixin,
    UBSSearchAuditWeightsMixin,
):
    """Construccion del informe de auditoria de un run UBS."""

    def _build_ubs_run_audit(
        self,
        memory_path: Path,
        account_type: str,
        run_id: int,
    ) -> tuple[list[tuple[str, str, str]], Path]:
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs where id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"Run #{run_id} no existe en {account_type}.")
            out, summary = self._compose_ubs_run_audit(conn, account_type, run)
        finally:
            conn.close()
        report_path = BASE_DIR / "outputs" / f"run{run_id}_{self._ubs_account_context_file_label(account_type)}_audit.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return summary, report_path

    def _audit_run_header(self, ctx, run, run_id, expected_label):
        """Cabecera del informe: run, fechas y configuracion."""
        config = ctx.parse_json(run["config_json"] if "config_json" in run.keys() else "")
        args = config.get("args") if isinstance(config.get("args"), dict) else {}
        generation = config.get("generation") if isinstance(config.get("generation"), dict) else {}
        score_cfg = config.get("score") if isinstance(config.get("score"), dict) else {}
        caps = generation.get("target_diversity_caps") if isinstance(generation.get("target_diversity_caps"), dict) else {}

        ctx.line(f"AUDITORIA RUN #{run_id} - {expected_label}")
        ctx.line("=" * 96)
        ctx.line(f"created_at: {run['created_at']}")
        ctx.line(f"run_dir: {run['output_dir']}")
        ctx.line(
            f"gens={run['generations']} variants/set={run['variants_per_seed']} "
            f"max_seeds={run['max_seeds']} execute_backtests={run['execute_backtests']} "
            f"dry_run={run['dry_run']} hidden={run['hidden']}"
        )
        ctx.line(f"fechas base: {args.get('from_date')} -> {args.get('to_date')}")
        ctx.line(
            f"force_unseeded={generation.get('force_unseeded_universe')} "
            f"long_tf={generation.get('experimental_long_timeframes')} "
            f"TFs={','.join(generation.get('timeframe_universe') or [])}"
        )
        ctx.line(
            f"score base: net>{score_cfg.get('min_net_profit')} pf>={score_cfg.get('min_profit_factor')} "
            f"trades>={score_cfg.get('min_trades')} DD<={score_cfg.get('max_drawdown_pct')} "
            f"RF>={score_cfg.get('min_recovery_factor')}"
        )
        ctx.line(
            f"caps: group={caps.get('group_ratios')} symbol={caps.get('symbol_ratio')} "
            f"tf={caps.get('timeframe_ratio')} pair={caps.get('symbol_timeframe_ratio')}"
        )

    def _audit_base_generation(self, ctx, run, run_id):
        """Recuento de candidatos base por generacion y estado."""
        base = ctx.counts("select status,count(*) from candidates where run_id=? group by status")
        theoretical = int(run["generations"]) * int(run["variants_per_seed"]) * int(run["max_seeds"])
        ctx.line("\nBASE / GENERACION")
        ctx.line("-" * 96)
        ctx.line(f"candidatos DB={sum(base.values())} teorico={theoretical} | {ctx.fmt_counts(base)}")
        for row in ctx.rows("select generation,status,count(*) n from candidates where run_id=? group by generation,status order by generation,status"):
            ctx.line(f"  gen {row['generation']}: {row['status']}={row['n']}")
        for status in ("accepted", "rejected", "no_trades"):
            values = [row["score"] for row in ctx.rows("select score from candidates where run_id=? and status=?", (run_id, status))]
            if values:
                ctx.line(f"  score {status}: {ctx.stat(values)}")
        return {
            "base": base,
            "theoretical": theoretical,
        }

    def _report_artifact_lines(self, ctx, run, run_id, run_dir) -> None:
        """Recuento de ficheros por generacion y por etapa en disco."""
        ctx.line("\nARTEFACTOS EN DISCO")
        ctx.line("-" * 96)
        ctx.line(f"run_dir existe: {run_dir.exists()}")
        for gen_no in range(1, int(run["generations"]) + 1):
            gen_dir = run_dir / f"gen_{gen_no:03d}"
            accepted_dir = run_dir / f"accepted_gen_{gen_no:03d}"
            db_gen = ctx.one("select count(*) from candidates where run_id=? and generation=?", (run_id, gen_no))
            db_acc = ctx.one("select count(*) from candidates where run_id=? and generation=? and status='accepted'", (run_id, gen_no))
            set_files = len(list(gen_dir.rglob("*.set"))) if gen_dir.exists() else 0
            accepted_copies = len(list(accepted_dir.glob("*.set"))) if accepted_dir.exists() else 0
            ctx.line(f"gen_{gen_no:03d}: db={db_gen} set_files={set_files} accepted_db={db_acc} accepted_copies={accepted_copies}")
        for name in ("robustness", "final_tick", "final_tick_6m", "retry_mismatch", "retry_full"):
            folder = run_dir / name
            dirs = len([path for path in folder.iterdir() if path.is_dir()]) if folder.exists() else 0
            set_files = len(list(folder.rglob("*.set"))) if folder.exists() else 0
            ctx.line(f"{name}: exists={folder.exists()} dirs={dirs} sets={set_files}")

    def _audit_run_artifacts(self, ctx, run, run_id, run_dir):
        """Sets y reportes que la memoria declara y el disco no tiene."""
        missing_set_path = ctx.one("select count(*) from candidates where run_id=? and (set_path is null or set_path='')")
        missing_set_files: list[int] = []
        missing_report_path: list[int] = []
        missing_report_files: list[int] = []
        invalid_metrics: list[int] = []
        artifact_detail = ["GEN\tID\tSTATUS\tPROBLEMA\tSET_PATH\tREPORT_PATH\tSET\tREPORTE"]
        for row in ctx.rows("select id,generation,status,set_path,report_path,metrics_json from candidates where run_id=?"):
            set_path = str(row["set_path"] or "")
            report_path = str(row["report_path"] or "")
            problems: list[str] = []
            if not set_path:
                problems.append("set_path vacio")
            if set_path and not workspace_path_exists(set_path):
                missing_set_files.append(int(row["id"]))
                problems.append("set file no existe")
            if str(row["status"]) in {"accepted", "rejected", "no_trades", "report_mismatch", "parse_error"}:
                if not report_path:
                    missing_report_path.append(int(row["id"]))
                    problems.append("report_path vacio")
                elif not workspace_path_exists(report_path):
                    missing_report_files.append(int(row["id"]))
                    problems.append("report file no existe")
            if row["metrics_json"] and not ctx.parse_json(row["metrics_json"]):
                invalid_metrics.append(int(row["id"]))
                problems.append("metrics_json invalido")
            if problems:
                artifact_detail.append(
                    "\t".join(
                        [
                            str(row["generation"] or ""),
                            str(row["id"] or ""),
                            str(row["status"] or ""),
                            ", ".join(problems),
                            set_path,
                            report_path,
                            Path(set_path).name if set_path else "",
                            Path(report_path).name if report_path else "",
                        ]
                    )
                )
        ctx.line(
            f"set_path faltantes={missing_set_path} | set files faltantes={len(missing_set_files)} | "
            f"report_path faltantes={len(missing_report_path)} | report files faltantes={len(missing_report_files)} | "
            f"metrics_json invalidos={len(invalid_metrics)}"
        )

        self._report_artifact_lines(ctx, run, run_id, run_dir)
        return {
            "missing_set_path": missing_set_path,
            "missing_set_files": missing_set_files,
            "missing_report_path": missing_report_path,
            "missing_report_files": missing_report_files,
            "artifact_detail": artifact_detail,
        }

    def _audit_robustness_stage(self, ctx):
        """Estados, pendientes y filas obsoletas de la robustez OOS."""
        robust = ctx.counts("select cr.status,count(*) from candidate_robustness cr join candidates c on c.id=cr.candidate_id where c.run_id=? group by cr.status")
        nonfinal_robust = audit_nonfinal_count(robust)
        missing_robust = ctx.one(
            "select count(*) from candidates c left join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.candidate_id is null"
        )
        stale_robust = ctx.one(
            "select count(*) from candidate_robustness cr join candidates c on c.id=cr.candidate_id "
            "where c.run_id=? and c.status<>'accepted'"
        )
        ctx.line("\nROBUSTEZ")
        ctx.line("-" * 96)
        ctx.line(ctx.fmt_counts(robust))
        ctx.line(
            f"base accepted sin robustez={missing_robust} | "
            f"estados no finales={nonfinal_robust} | stale={stale_robust}"
        )
        for row in ctx.rows(
            "select c.generation,cr.status,count(*) n from candidate_robustness cr "
            "join candidates c on c.id=cr.candidate_id where c.run_id=? "
            "group by c.generation,cr.status order by c.generation,cr.status"
        ):
            ctx.line(f"  gen {row['generation']}: {row['status']}={row['n']}")
        return {
            "robust": robust,
            "nonfinal_robust": nonfinal_robust,
            "missing_robust": missing_robust,
            "stale_robust": stale_robust,
        }

    def _audit_final_tick_stage(self, ctx):
        """Estados y pendientes del Final Tick corto."""
        ft = ctx.counts("select ft.status,count(*) from candidate_final_tick ft join candidates c on c.id=ft.candidate_id where c.run_id=? group by ft.status")
        # pending_ohlc_trades is an intentional hand-off from the short window
        # to 6M. Missing/unresolved 6M rows are audited separately below.
        short_handoff_ft = int(ft.get("pending_ohlc_trades", 0) or 0)
        nonfinal_ft = audit_nonfinal_count(
            ft,
            additional_final_statuses={"pending_ohlc_trades"},
        )
        eligible_ft = ctx.one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted'"
        )
        missing_ft = ctx.one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "left join candidate_final_tick ft on ft.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft.candidate_id is null"
        )
        stale_ft = ctx.one(
            "select count(*) from candidate_final_tick ft join candidates c on c.id=ft.candidate_id "
            "left join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and not (c.status='accepted' and cr.status='accepted')"
        )
        ctx.line("\nFINAL TICK CORTO")
        ctx.line("-" * 96)
        ctx.line(ctx.fmt_counts(ft))
        ctx.line(
            f"elegibles base+robust={eligible_ft} | sin FT={missing_ft} | "
            f"bloqueantes={nonfinal_ft} | derivados a 6M={short_handoff_ft} | stale={stale_ft}"
        )
        for row in ctx.rows(
            "select c.generation,ft.status,count(*) n from candidate_final_tick ft "
            "join candidates c on c.id=ft.candidate_id where c.run_id=? "
            "group by c.generation,ft.status order by c.generation,ft.status"
        ):
            ctx.line(f"  gen {row['generation']}: {row['status']}={row['n']}")
        return {
            "ft": ft,
            "short_handoff_ft": short_handoff_ft,
            "nonfinal_ft": nonfinal_ft,
            "missing_ft": missing_ft,
            "stale_ft": stale_ft,
        }

    def _audit_final_tick_6m_stage(self, ctx):
        """Estados, causas de rechazo y usables del Final Tick 6M."""
        ft6 = ctx.counts("select ft6.status,count(*) from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id where c.run_id=? group by ft6.status")
        nonfinal_ft6 = audit_nonfinal_count(ft6)
        eligible_ft6 = ctx.one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted'"
        )
        missing_ft6 = ctx.one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.candidate_id is null"
        )
        stale_ft6 = ctx.one(
            "select count(*) from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id "
            "left join candidate_robustness cr on cr.candidate_id=c.id "
            "left join candidate_final_tick ft on ft.candidate_id=c.id "
            "where c.run_id=? and not (c.status='accepted' and cr.status='accepted' and ft.status in ('accepted','pending_ohlc_trades'))"
        )
        usable = ctx.one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.status='accepted'"
        )
        reason_counts: dict[str, int] = {}
        for row in ctx.rows(
            "select ft6.similarity_json from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id "
            "where c.run_id=? and ft6.status='rejected'"
        ):
            sim = ctx.parse_json(row["similarity_json"])
            for reason in sim.get("reasons") or ("sin_reason",):
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
        ctx.line("\nFINAL TICK 6M")
        ctx.line("-" * 96)
        ctx.line(ctx.fmt_counts(ft6))
        ctx.line(
            f"elegibles 6M={eligible_ft6} | sin fila 6M={missing_ft6} | "
            f"pendientes/problema={nonfinal_ft6} | stale={stale_ft6} | "
            f"usable portfolio/live={usable}"
        )
        ctx.line("causas rejected 6M: " + (", ".join(f"{key}={value}" for key, value in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))) or "sin rechazos"))
        for row in ctx.rows(
            "select c.generation,ft6.status,count(*) n from candidate_final_tick_6m ft6 "
            "join candidates c on c.id=ft6.candidate_id where c.run_id=? "
            "group by c.generation,ft6.status order by c.generation,ft6.status"
        ):
            ctx.line(f"  gen {row['generation']}: {row['status']}={row['n']}")
        return {
            "ft6": ft6,
            "nonfinal_ft6": nonfinal_ft6,
            "missing_ft6": missing_ft6,
            "stale_ft6": stale_ft6,
            "usable": usable,
        }

    def _audit_regression_stage(self, ctx, conn, run_id):
        """Estados y puntos acumulados de la regresiva OHLC."""
        regression = ctx.counts(
            "select rg.status,count(*) from candidate_regression rg join candidates c on c.id=rg.candidate_id "
            "where c.run_id=? group by rg.status"
        )
        nonfinal_regression = audit_nonfinal_count(regression, additional_final_statuses={"no_trades"})
        missing_regression = ctx.one(
            "select count(*) from candidates c "
            "join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted' "
            "left join candidate_regression rg on rg.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and rg.candidate_id is null"
        )
        stale_regression = ctx.one(
            "select count(*) from candidate_regression rg join candidates c on c.id=rg.candidate_id "
            "left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id "
            "where c.run_id=? and not (c.status='accepted' and ft6.status='accepted')"
        )
        regression_points_total = conn.execute(
            "select coalesce(sum(rg.points_applied),0) from candidate_regression rg "
            "join candidates c on c.id=rg.candidate_id where c.run_id=?",
            (run_id,),
        ).fetchone()[0]
        ctx.line("\nREGRESIVA OHLC")
        ctx.line("-" * 96)
        ctx.line(ctx.fmt_counts(regression))
        ctx.line(
            f"6M accepted sin regresiva={missing_regression} | tecnicos/retryables={nonfinal_regression} | "
            f"stale={stale_regression} | puntos acumulados={float(regression_points_total or 0):+.2f}"
        )
        return {
            "nonfinal_regression": nonfinal_regression,
            "stale_regression": stale_regression,
        }

    def _audit_accepted_6m_detail(self, ctx):
        """Detalle de los candidatos accepted en Final Tick 6M."""
        accepted_6m_detail = ctx.rows(
            """
            select c.id,c.set_path,c.target_symbol,c.period,c.score base_score,cr.score robust_score,
                   ft6.ohlc_score,ft6.real_tick_score,ft6.similarity_json,
                   c.run_id,c.seed_path,c.symbol,c.family,c.mutated_keys,c.accepted,c.metrics_json,c.status,c.report_path,
                   cr.status robust_status,cr.positive_bonus robust_positive_bonus,
                   cr.negative_bonus robust_negative_bonus,cr.metrics_json robust_metrics_json,
                   ft.status final_tick_status,ft.similarity_json final_tick_similarity_json,
                   ft6.status final_tick_6m_status,ft6.similarity_json final_tick_6m_similarity_json,
                   rg.status regression_status,rg.points_applied regression_points_applied
            from candidates c
            join candidate_robustness cr on cr.candidate_id=c.id
            join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades')
            join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            left join candidate_regression rg on rg.candidate_id=c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.status='accepted'
            order by c.id
            """
        )
        ctx.line("\n6M ACCEPTED DETALLE")
        ctx.line("-" * 96)
        if not accepted_6m_detail:
            ctx.line("sin accepted 6M")
        for row in accepted_6m_detail:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            sim = ctx.parse_json(row["similarity_json"])
            checks = sim.get("checks") if isinstance(sim.get("checks"), dict) else {}
            pf = checks.get("profit_factor", {}) if isinstance(checks, dict) else {}
            floor = checks.get("profit_factor_floor", {}) if isinstance(checks, dict) else {}
            ctx.line(
                f"  id={row['id']} {row['target_symbol']} {row['period']} "
                f"base={ctx.fnum(row['base_score'])} robust={ctx.fnum(row['robust_score'])} "
                f"ohlc={ctx.fnum(row['ohlc_score'])} tick={ctx.fnum(row['real_tick_score'])} "
                f"weight={ctx.fnum(value)} pf_delta={pf.get('delta_pct')} floor_ok={floor.get('accepted')} "
                f"set={Path(str(row['set_path'] or '')).name}"
            )

    def _audit_findings(self, ctx, state, run_id) -> None:
        """Incoherencias detectadas entre memoria, disco y etapas."""
        state["issues"]: list[str] = []
        if sum(state["base"].values()) != state["theoretical"]:
            state["issues"].append(f"candidatos DB {sum(state['base'].values())} != teorico {state['theoretical']}")
        if state["missing_set_path"] or state["missing_set_files"]:
            state["issues"].append(f"sets faltantes path={state['missing_set_path']} files={len(state['missing_set_files'])}")
        if state["missing_report_path"] or state["missing_report_files"]:
            state["issues"].append(f"reportes base faltantes path={len(state['missing_report_path'])} files={len(state['missing_report_files'])}")
        if state["account_mismatches"]:
            state["issues"].append(f"mismatch cuenta MT5 en reportes={len(state['account_mismatches'])}")
        if state["stale_robust"] or state["stale_ft"] or state["stale_ft6"] or state["stale_regression"]:
            state["issues"].append(
                f"stale rows robust={state['stale_robust']} ft={state['stale_ft']} ft6={state['stale_ft6']} reg={state['stale_regression']}"
            )
        if state["missing_robust"] or state["missing_ft"] or state["missing_ft6"]:
            state["issues"].append(f"pendientes missing robust={state['missing_robust']} ft={state['missing_ft']} ft6={state['missing_ft6']}")
        if state["nonfinal_robust"] or state["nonfinal_ft"] or state["nonfinal_ft6"]:
            state["issues"].append(
                "estados no finales "
                f"robust={state['nonfinal_robust']} ft={state['nonfinal_ft']} ft6={state['nonfinal_ft6']}"
            )
        if state["nonfinal_regression"]:
            state["issues"].append(f"regresiva tecnica/retryable={state['nonfinal_regression']}")
        if state["weight_mismatches"]:
            state["issues"].append(f"mismatch diagnostico legacy vs feedback_weight={state['weight_mismatches']}")
        if not state["issues"]:
            state["issues"].append(f"sin inconsistencias estructurales detectadas en run {run_id}")
        ctx.line("\nHALLAZGOS")
        ctx.line("-" * 96)
        for issue in state["issues"]:
            ctx.line(f"- {issue}")

    def _audit_summary_rows(self, ctx, state, run_id, run, account_type):
        """Filas de resumen que la pantalla muestra junto al informe."""
        issue_tag = "accepted" if state["issues"] and state["issues"][0].startswith("sin inconsistencias") else "rejected"
        base_account_detail, base_account_tag, base_account_lines = self._account_process_summary(ctx, state, ("Base",))
        robust_account_detail, robust_account_tag, robust_account_lines = self._account_process_summary(ctx, state, ("Robustez",))
        ft_account_detail, ft_account_tag, ft_account_lines = self._account_process_summary(ctx, state, ("FT corto OHLC", "FT corto Tick"))
        ft6_account_detail, ft6_account_tag, ft6_account_lines = self._account_process_summary(ctx, state, ("FT 6M OHLC", "FT 6M Tick"))
        summary = [
            ("Run", f"#{run_id} {account_type} | {run['created_at']}", "pending"),
            ("Base", f"{ctx.fmt_counts(state['base'])} | teorico={state['theoretical']}", "accepted" if sum(state["base"].values()) == state["theoretical"] else "rejected"),
            (
                "Sets/reportes",
                f"set files faltantes={len(state['missing_set_files'])} | reportes base faltantes={len(state['missing_report_path'])+len(state['missing_report_files'])}",
                "accepted" if len(state["artifact_detail"]) == 1 else "rejected",
                state["artifact_detail"] if len(state["artifact_detail"]) > 1 else [],
            ),
            ("Cuenta MT5 base", base_account_detail, base_account_tag, base_account_lines),
            (
                "Robustez",
                f"{ctx.fmt_counts(state['robust'])} | sin robust={state['missing_robust']} | no finales={state['nonfinal_robust']} | stale={state['stale_robust']}",
                "accepted" if not (state["missing_robust"] or state["nonfinal_robust"] or state["stale_robust"]) else "rejected",
            ),
            ("Cuenta MT5 robustez", robust_account_detail, robust_account_tag, robust_account_lines),
            (
                "Final Tick corto",
                f"{ctx.fmt_counts(state['ft'])} | sin FT={state['missing_ft']} | bloqueantes={state['nonfinal_ft']} | derivados 6M={state['short_handoff_ft']} | stale={state['stale_ft']}",
                "accepted" if not (state["missing_ft"] or state["nonfinal_ft"] or state["stale_ft"]) else "rejected",
            ),
            ("Cuenta MT5 FT corto", ft_account_detail, ft_account_tag, ft_account_lines),
            (
                "Final Tick 6M",
                f"{ctx.fmt_counts(state['ft6'])} | usable={state['usable']} | sin fila={state['missing_ft6']} | pendientes/problema={state['nonfinal_ft6']} | stale={state['stale_ft6']}",
                "accepted" if state["usable"] > 0 and not (state["missing_ft6"] or state["nonfinal_ft6"] or state["stale_ft6"]) else "rejected",
            ),
            ("Cuenta MT5 FT 6M", ft6_account_detail, ft6_account_tag, ft6_account_lines),
            (
                "Formula pesos",
                "base + robustez + final corto + final 6M; corto accepted=0, 6M accepted=+120, rejected penaliza",
                "pending",
                state["formula_detail"],
            ),
            ("Peso run", ctx.stat(state["weights"]), "pending"),
            (
                "Detalle pesos",
                f"filas={max(len(state['weight_detail'])-1, 0)} | check formula_vs_funcion mismatch={state['weight_mismatches']}",
                "accepted" if state["weight_mismatches"] == 0 else "rejected",
                state["weight_detail"],
            ),
            ("Hallazgo", "; ".join(state["issues"]), issue_tag),
        ]
        return summary

    def _compose_ubs_run_audit(
        self,
        conn: sqlite3.Connection,
        account_type: str,
        run: sqlite3.Row,
    ) -> tuple[list[str], list[tuple[str, str, str]]]:
        run_id = int(run["id"])
        run_dir = resolve_workspace_path(str(run["output_dir"] or ""))
        ctx = _RunAuditContext(conn, run_id)
        expected_context = self._parse_ubs_account_context(account_type)
        if expected_context is None:
            if "/" in str(account_type):
                broker_raw, account_raw = str(account_type).split("/", 1)
                expected_context = (
                    normalize_broker(broker_raw),
                    normalize_account_type(account_raw, normalize_broker(broker_raw)),
                )
            else:
                expected_broker = self._ubs_broker()
                expected_context = (expected_broker, normalize_account_type(account_type, expected_broker))
        expected_broker, expected_account = expected_context
        expected_label = self._ubs_account_context_label(expected_broker, expected_account)
        state: dict[str, object] = {}
        self._audit_run_header(ctx, run, run_id, expected_label)
        state.update(self._audit_base_generation(ctx, run, run_id))
        state.update(self._audit_run_artifacts(ctx, run, run_id, run_dir))
        state.update(self._audit_report_accounts(
            ctx, expected_context, expected_broker, expected_label,
        ))
        state.update(self._audit_robustness_stage(ctx))
        state.update(self._audit_final_tick_stage(ctx))
        state.update(self._audit_final_tick_6m_stage(ctx))
        state.update(self._audit_regression_stage(ctx, conn, run_id))
        state.update(self._audit_weight_rows(ctx))
        self._audit_accepted_6m_detail(ctx)
        self._audit_findings(ctx, state, run_id)
        summary = self._audit_summary_rows(ctx, state, run_id, run, account_type)
        return ctx.out, summary
