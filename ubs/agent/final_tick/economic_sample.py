"""Ampliacion de muestras economicamente divergentes pero operativamente afines."""
from __future__ import annotations

import json

from ubs.agent.final_tick.trade_alignment import final_tick_trade_alignment
from ubs.path_utils import resolve_workspace_path


PENDING_ECONOMIC_SAMPLE = "pending_economic_sample"


def economic_sample_pending_check(
    similarity: dict, min_trades: int, trade_alignment: dict | None,
) -> dict | None:
    """Decide si la divergencia economica necesita una ventana mas larga."""
    reasons = {str(reason) for reason in similarity.get("reasons", [])}
    if reasons - {"economic_direction"}:
        return None
    checks = similarity.get("checks")
    if not isinstance(checks, dict):
        return None
    economic = checks.get("economic_direction")
    trades = checks.get("trades")
    if not isinstance(economic, dict) or not isinstance(trades, dict):
        return None
    if economic.get("matches", True) or not trades.get("accepted", False):
        return None
    try:
        ohlc_trades = int(float(trades["ohlc"]))
        tick_trades = int(float(trades["real_tick"]))
    except (KeyError, TypeError, ValueError):
        return None
    minimum = max(1, int(min_trades))
    if min(ohlc_trades, tick_trades) >= minimum:
        return None
    if not isinstance(trade_alignment, dict) or not trade_alignment.get("accepted"):
        return None
    return {
        "accepted": False,
        "checked": True,
        "ohlc_trades": ohlc_trades,
        "real_tick_trades": tick_trades,
        "min_trades": minimum,
    }


def reviewed_economic_sample(
    similarity_json: object, ohlc_report: object, tick_report: object,
    min_trades: int, retry_from: str, retry_to: str,
) -> dict | None:
    """Devuelve el blob reclasificado cuando informes guardados cumplen la regla."""
    try:
        similarity = json.loads(str(similarity_json or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(similarity, dict) or not ohlc_report or not tick_report:
        return None
    alignment = final_tick_trade_alignment(
        resolve_workspace_path(ohlc_report), resolve_workspace_path(tick_report),
    )
    detail = economic_sample_pending_check(similarity, min_trades, alignment)
    if detail is None:
        return None
    detail.update({"retry_from": retry_from, "retry_to": retry_to})
    similarity.setdefault("checks", {})["trade_alignment"] = alignment
    similarity.setdefault("checks", {})["economic_sample"] = detail
    return similarity


def economic_sample_status(
    status: str, ohlc_result, real_tick_result, similarity: dict, args,
) -> str:
    """Aplaza accepted/rejected hasta el segundo rango configurado."""
    if status not in {"accepted", "rejected"}:
        return status
    retry_from = str(getattr(args, "final_tick_ohlc_from_date", "") or "").strip()
    retry_to = str(getattr(args, "final_tick_ohlc_to_date", "") or "").strip()
    current = (str(args.from_date or "").strip(), str(args.to_date or "").strip())
    if not retry_from or not retry_to or current == (retry_from, retry_to):
        return status
    alignment = final_tick_trade_alignment(
        resolve_workspace_path(ohlc_result.report_path),
        resolve_workspace_path(real_tick_result.report_path),
    )
    detail = economic_sample_pending_check(
        similarity, int(getattr(args, "final_tick_economic_min_trades", 50)), alignment,
    )
    if detail is None:
        return status
    detail.update({"retry_from": retry_from, "retry_to": retry_to})
    similarity.setdefault("checks", {})["trade_alignment"] = alignment
    similarity.setdefault("checks", {})["economic_sample"] = detail
    return PENDING_ECONOMIC_SAMPLE


def candidate_economic_sample_review_rows(
    rows: list, min_trades: int, from_date: str, to_date: str,
    retry_from: str, retry_to: str,
) -> list:
    """Resultados del run visible que deben pasar al segundo rango."""
    matches = []
    for row in rows:
        if str(row["final_status"] or "") not in {"accepted", "rejected"}:
            continue
        if (str(row["from_date"] or ""), str(row["to_date"] or "")) != (
            from_date, to_date,
        ):
            continue
        reviewed = reviewed_economic_sample(
            row["similarity_json"], row["ohlc_report_path"],
            row["real_tick_report_path"], min_trades, retry_from, retry_to,
        )
        if reviewed is not None:
            matches.append(row)
    return matches


def mark_candidate_economic_sample_pending(
    conn, stage: str, rows: list, min_trades: int,
    retry_from: str, retry_to: str,
) -> int:
    """Reclasifica candidatos y retira evidencia downstream ya obsoleta."""
    six_month = str(stage).strip().lower().replace("-", "_") in {
        "six_month", "sixmonth", "6m",
    }
    table = "candidate_final_tick_6m" if six_month else "candidate_final_tick"
    updated = 0
    for row in rows:
        similarity = reviewed_economic_sample(
            row["similarity_json"], row["ohlc_report_path"],
            row["real_tick_report_path"], min_trades, retry_from, retry_to,
        )
        if similarity is None:
            continue
        candidate_id = int(row["id"])
        conn.execute(
            f"update {table} set status=?, accepted=0, similarity_json=? "
            "where candidate_id=?",
            (PENDING_ECONOMIC_SAMPLE, json.dumps(similarity, sort_keys=True), candidate_id),
        )
        conn.execute(
            "delete from candidate_regression where candidate_id=?", (candidate_id,),
        )
        if not six_month:
            conn.execute(
                "delete from candidate_final_tick_6m where candidate_id=?",
                (candidate_id,),
            )
        updated += 1
    conn.commit()
    return updated
