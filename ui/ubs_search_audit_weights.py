from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_REJECTED_PENALTY,
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    FINAL_TICK_REASON_PENALTIES,
    NO_TRADES_WEIGHT,
    REJECTED_BASE_PENALTY,
    REJECTED_REASON_PENALTIES,
    ROBUST_REASON_PENALTIES,
    feedback_weight,
    metric_reasons,
    reason_penalty,
    robust_bonus,
)


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_search_audit_base import (  # noqa: F401  fachada del modulo
    AUDIT_FINAL_STATUSES,
    audit_nonfinal_count,
)


class UBSSearchAuditWeightsMixin:
    """Utilidad legacy por fila del informe de auditoria."""

    def _report_weight_lines(self, ctx, formula_detail, weights, weight_detail,
                             weights_by_ft6, weights_by_asset, weights_by_tf,
                             weight_mismatches) -> None:
        """Lineas del informe con la utilidad legacy por fila."""
        ctx.line("\nUTILIDAD LEGACY POR FILA (DIAGNOSTICO; NO USADA PARA SELECCION)")
        ctx.line("-" * 96)
        for item in formula_detail[1:]:
            ctx.line(item.replace("\t", ": "))
        ctx.line(f"utilidad legacy run: {ctx.stat(weights)}")
        ctx.line(f"detalle pesos verificable: filas={max(len(weight_detail) - 1, 0)} mismatch_formula_vs_funcion={weight_mismatches}")
        for status, values in sorted(weights_by_ft6.items()):
            ctx.line(f"  ft6 {status}: {ctx.stat(values)}")
        ctx.line("asset top run:")
        for key, values in sorted(weights_by_asset.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True)[:12]:
            ctx.line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")
        ctx.line("asset bottom run:")
        for key, values in sorted(weights_by_asset.items(), key=lambda item: sum(item[1]) / len(item[1]))[:12]:
            ctx.line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")
        ctx.line("TF run:")
        for key, values in sorted(weights_by_tf.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True):
            ctx.line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")

    def _weight_stage_parts(self, ctx, row, status, score, parts, reason_parts):
        """Aporte de cada etapa al peso legacy de la fila."""
        if status == "accepted":
            parts["base"] = score + ASSET_ACCEPTED_BONUS
            reason_parts.append(f"base accepted score {score:.2f}+{ASSET_ACCEPTED_BONUS:.2f}")
        else:
            base_reasons = metric_reasons(row["metrics_json"])
            penalty = reason_penalty(base_reasons, REJECTED_REASON_PENALTIES)
            value = score - REJECTED_BASE_PENALTY - penalty
            ceiling = -penalty if base_reasons else -REJECTED_BASE_PENALTY
            parts["base"] = min(value, ceiling)
            reason_parts.append(
                f"base rejected score {score:.2f}-{REJECTED_BASE_PENALTY:.2f}-{penalty:.2f} cap {ceiling:.2f}"
            )
            if base_reasons:
                reason_parts.append("base razones=" + ",".join(base_reasons))

        robust_status = str(row["robust_status"] or "").lower()
        if robust_status == "accepted":
            parts["robust"] = robust_bonus(row)
            reason_parts.append(f"robust accepted {parts['robust']:.2f}")
        elif robust_status == "rejected":
            robust_reasons = metric_reasons(row["robust_metrics_json"])
            penalty = reason_penalty(robust_reasons, ROBUST_REASON_PENALTIES)
            parts["robust"] = robust_bonus(row) - penalty
            reason_parts.append(f"robust rejected {robust_bonus(row):.2f}-{penalty:.2f}")
            if robust_reasons:
                reason_parts.append("robust razones=" + ",".join(robust_reasons))

        rejected_ceilings: list[float] = []
        ft_status = str(row["final_tick_status"] or "").lower()
        if ft_status == "rejected":
            ft_reasons = metric_reasons(row["final_tick_similarity_json"])
            penalty = reason_penalty(ft_reasons, FINAL_TICK_REASON_PENALTIES)
            parts["ft"] = DEFAULT_FINAL_TICK_REJECTED_PENALTY - penalty
            rejected_ceilings.append(parts["ft"])
            reason_parts.append(f"ft corto rejected {DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f}-{penalty:.2f}")
            if ft_reasons:
                reason_parts.append("ft razones=" + ",".join(ft_reasons))
        elif ft_status == "accepted":
            reason_parts.append("ft corto accepted 0.00")

        ft6_status = str(row["final_tick_6m_status"] or "").lower()
        if ft6_status == "accepted":
            parts["ft6"] = DEFAULT_FINAL_TICK_ACCEPTED_BONUS
            reason_parts.append(f"ft6 accepted +{DEFAULT_FINAL_TICK_ACCEPTED_BONUS:.2f}")
        elif ft6_status == "rejected":
            ft6_reasons = metric_reasons(row["final_tick_6m_similarity_json"])
            penalty = reason_penalty(ft6_reasons, FINAL_TICK_REASON_PENALTIES)
            parts["ft6"] = DEFAULT_FINAL_TICK_REJECTED_PENALTY - penalty
            rejected_ceilings.append(parts["ft6"])
            reason_parts.append(f"ft6 rejected {DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f}-{penalty:.2f}")
            if ft6_reasons:
                reason_parts.append("ft6 razones=" + ",".join(ft6_reasons))

        regression_status = str(row["regression_status"] or "").lower()
        if regression_status in {"accepted", "rejected", "no_trades"}:
            parts["regression"] = float(row["regression_points_applied"] or 0.0)
            reason_parts.append(f"regresiva {regression_status} {parts['regression']:+.2f}")
        return rejected_ceilings

    def _weight_breakdown_fn(self, ctx):
        """Desglose verificable del peso legacy de una fila."""
        def weight_breakdown(row: sqlite3.Row) -> tuple[float | None, dict[str, float], str]:
            status = str(row["status"] or "").lower()
            parts = {"base": 0.0, "robust": 0.0, "ft": 0.0, "ft6": 0.0, "regression": 0.0}
            reason_parts: list[str] = []
            if status == "no_trades":
                if not str(row["report_path"] or "").strip():
                    return None, parts, "no_trades sin report_path no aporta"
                parts["base"] = NO_TRADES_WEIGHT
                return parts["base"], parts, "no_trades con reporte"
            if status not in {"accepted", "rejected"} or row["score"] in (None, ""):
                return None, parts, "status/score no ponderable"

            score = float(row["score"] or 0.0)
            rejected_ceilings = self._weight_stage_parts(
                ctx, row, status, score, parts, reason_parts,
            )

            total = parts["base"] + parts["robust"] + parts["ft"] + parts["ft6"] + parts["regression"]
            if rejected_ceilings:
                total = min(total, min(rejected_ceilings))
                reason_parts.append(f"cap final tick {min(rejected_ceilings):.2f}")
            if parts["regression"] < 0:
                total = min(total, parts["regression"])
                reason_parts.append(f"cap regresiva {parts['regression']:.2f}")
            return total, parts, " | ".join(reason_parts)
        return weight_breakdown

    def _accumulate_weight_row(self, ctx, row, weight_breakdown, weights,
                               weights_by_ft6, weights_by_asset, weights_by_tf,
                               weight_detail) -> int:
        """Suma una fila al desglose; devuelve 1 si la formula no cuadra."""
        mismatches = 0
        value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
        formula_value, parts, reason_text = weight_breakdown(row)
        check = "OK"
        if value is None and formula_value is not None:
            check = "REVISAR"
            mismatches += 1
        elif value is not None and formula_value is None:
            check = "REVISAR"
            mismatches += 1
        elif value is not None and formula_value is not None and abs(float(value) - float(formula_value)) > 0.01:
            check = "REVISAR"
            mismatches += 1
        weight_detail.append(
            "\t".join(
                [
                    str(row["generation"] or ""),
                    str(row["id"] or ""),
                    str(row["status"] or ""),
                    str(row["robust_status"] or ""),
                    str(row["final_tick_status"] or ""),
                    str(row["final_tick_6m_status"] or ""),
                    str(row["regression_status"] or ""),
                    str(row["target_symbol"] or row["symbol"] or ""),
                    str(row["period"] or ""),
                    ctx.fnum(row["score"]),
                    ctx.fnum(parts["base"]),
                    ctx.fnum(parts["robust"]),
                    ctx.fnum(parts["ft"]),
                    ctx.fnum(parts["ft6"]),
                    ctx.fnum(parts["regression"]),
                    ctx.fnum(formula_value),
                    ctx.fnum(value),
                    check,
                    reason_text,
                    Path(str(row["set_path"] or "")).name,
                ]
            )
        )
        if value is None:
            return mismatches
        weights.append(value)
        weights_by_ft6.setdefault(str(row["final_tick_6m_status"] or "sin_6m"), []).append(value)
        weights_by_asset.setdefault(str(row["target_symbol"] or row["symbol"]).upper(), []).append(value)
        weights_by_tf.setdefault(str(row["period"]).upper(), []).append(value)
        return mismatches

    @staticmethod
    def _weight_formula_detail() -> list[str]:
        """Tabla de la formula legacy que encabeza el desglose."""
        return [
            "CONCEPTO\tVALOR",
            f"Base accepted\tscore + accepted_bonus ({ASSET_ACCEPTED_BONUS:.2f})",
            f"Base rejected\tscore - base_penalty ({REJECTED_BASE_PENALTY:.2f}) - penalizaciones por razones; capado para que no aporte positivo",
            f"Base no_trades\t{NO_TRADES_WEIGHT:.2f} solo si tiene report_path real; si no tiene reporte no aporta peso",
            f"Robustez accepted\t+positive_bonus del row, default {DEFAULT_ROBUST_POSITIVE_BONUS:.2f}",
            f"Robustez rejected\t+negative_bonus del row, default {DEFAULT_ROBUST_NEGATIVE_BONUS:.2f}, menos penalizaciones por razones",
            "Final Tick corto accepted\t0.00; queda esperando Final Tick 6M",
            f"Final Tick corto rejected\t{DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f} menos penalizaciones por razones",
            f"Final Tick 6M accepted\t+{DEFAULT_FINAL_TICK_ACCEPTED_BONUS:.2f}",
            f"Final Tick 6M rejected\t{DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f} menos penalizaciones por razones; gate duro para portafolio",
            "Final Tick pending / sin fila\t0.00; neutral",
            "Regresiva accepted\t+puntos configurados (default +80)",
            "Regresiva rejected/no_trades\tpuntos FAIL y penalizacion por causas (default -100 a -160)",
            "Regresiva tecnica/sin fila\t0.00; neutral",
        ]

    def _audit_weight_rows(self, ctx):
        """Utilidad legacy por fila, con su desglose verificable."""
        feedback_rows = ctx.rows(
            """
            select c.id,c.run_id,c.generation,c.set_path,c.seed_path,c.target_symbol,c.symbol,c.period,c.family,c.mutated_keys,
                   c.score,c.accepted,c.metrics_json,c.status,c.report_path,
                   cr.status as robust_status,cr.positive_bonus as robust_positive_bonus,
                   cr.negative_bonus as robust_negative_bonus,cr.metrics_json as robust_metrics_json,
                   ft.status as final_tick_status,ft.similarity_json as final_tick_similarity_json,
                   ft6.status as final_tick_6m_status,ft6.similarity_json as final_tick_6m_similarity_json,
                   rg.status as regression_status,rg.points_applied as regression_points_applied
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id and c.status='accepted'
            left join candidate_final_tick ft on ft.candidate_id=c.id and c.status='accepted' and cr.status='accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id=c.id
             and c.status='accepted'
             and cr.status='accepted'
             and ft.status in ('accepted','pending_ohlc_trades')
            left join candidate_regression rg on rg.candidate_id=c.id and ft6.status='accepted'
            where c.run_id=? and c.status in ('accepted','rejected','no_trades')
              and (c.score is not null or c.status='no_trades')
            """
        )
        formula_detail = self._weight_formula_detail()


        weight_breakdown = self._weight_breakdown_fn(ctx)
        weights: list[float] = []
        weights_by_ft6: dict[str, list[float]] = {}
        weights_by_asset: dict[str, list[float]] = {}
        weights_by_tf: dict[str, list[float]] = {}
        weight_detail = [
            "GEN\tID\tSTATUS\tROBUST\tFT CORTO\tFT 6M\tREG\tSIMBOLO\tTF\tSCORE\tBASE\tROBUST ADJ\tFT ADJ\tFT6 ADJ\tREG ADJ\tPESO FORMULA\tPESO FUNC\tCHECK\tRAZONES\tSET"
        ]
        weight_mismatches = 0
        for row in feedback_rows:
            weight_mismatches += self._accumulate_weight_row(
                ctx, row, weight_breakdown, weights, weights_by_ft6,
                weights_by_asset, weights_by_tf, weight_detail,
            )
        self._report_weight_lines(
            ctx, formula_detail, weights, weight_detail,
            weights_by_ft6, weights_by_asset, weights_by_tf, weight_mismatches,
        )
        return {
            "formula_detail": formula_detail,
            "weights": weights,
            "weight_detail": weight_detail,
            "weight_mismatches": weight_mismatches,
        }
