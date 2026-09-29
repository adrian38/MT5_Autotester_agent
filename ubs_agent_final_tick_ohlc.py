"""Puntuacion de los informes OHLC de cada candidato del Final Tick."""
from __future__ import annotations

import json
from pathlib import Path

from ubs.score import ScoreResult, score_report_file
from ubs_agent_final_tick import final_tick_ohlc_trades_pending_payload
from ubs_agent_reports import (
    find_report_for_set,
    report_matches_variant,
)
from ubs_agent_universe import (
    min_trades_for_period,
    missing_report_status,
    score_config_for_variant,
)


def collect_final_tick_ohlc_results(
    args,
    memory,
    score_config,
    copied,
    symbol_map,
    run_id,
    skip_ohlc_flag,
    ohlc_min_report_mtime,
    default_min_ohlc_trades,
    status_counts,
    ready_for_tick,
    ohlc_results,
) -> None:
    """Rellena los contadores, los listos para tick y los resultados OHLC."""
    if skip_ohlc_flag:
        for row, ohlc_variant, real_tick_variant in copied:
            candidate_id = int(row["id"])
            try:
                data = json.loads(str(row["ft_ohlc_metrics_json"]))
                data["reasons"] = tuple(data.get("reasons", []))
                ohlc_result = ScoreResult(**data)
            except Exception as exc:
                print(f"AVISO: no pude reconstruir OHLC metrics para candidate #{candidate_id}: {exc}")
                memory.record_candidate_final_tick(
                    candidate_id, run_id, "parse_error", None, None,
                    None, None, None, None,
                    args.final_tick_min_history_quality, args.from_date, args.to_date,
                    args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
                )
                status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
                continue
            stored_path = row["ft_ohlc_report_path"]
            ohlc_report = Path(stored_path) if stored_path else ohlc_variant.path
            ready_for_tick.append((row, ohlc_variant, real_tick_variant))
            ohlc_results[candidate_id] = (ohlc_report, ohlc_result)
    else:
        for row, ohlc_variant, real_tick_variant in copied:
            candidate_id = int(row["id"])
            ohlc_report = find_report_for_set(ohlc_variant.path, min_mtime=ohlc_min_report_mtime)
            if not ohlc_report:
                status = missing_report_status(ohlc_variant.target_symbol, args)
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    status,
                    None,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts[status] = status_counts.get(status, 0) + 1
                continue

            ohlc_score_config = score_config_for_variant(
                score_config,
                ohlc_variant,
                min_trades_w1=args.final_tick_min_trades_w1,
                min_trades_mn=args.final_tick_min_trades_mn,
            )
            try:
                ohlc_result = score_report_file(ohlc_report, config=ohlc_score_config, broker=args.broker)
            except Exception as exc:
                print(f"AVISO: no pude parsear OHLC Final Tick candidate #{candidate_id}: {exc}")
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "parse_error",
                    None,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
                continue

            ohlc_matches, ohlc_mismatch = report_matches_variant(
                ohlc_variant,
                ohlc_result,
                symbol_map,
                args.symbol_suffix,
                args.broker,
            )
            if not ohlc_matches:
                print(f"AVISO: reporte OHLC Final Tick no coincide para candidate #{candidate_id}: {ohlc_mismatch}")
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "report_mismatch",
                    ohlc_result,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
                continue

            min_ohlc_trades = min_trades_for_period(
                ohlc_variant.target_period,
                default_min_ohlc_trades,
                args.final_tick_min_trades_w1,
                args.final_tick_min_trades_mn,
            )
            if ohlc_result.trades < min_ohlc_trades:
                payload = final_tick_ohlc_trades_pending_payload(ohlc_result, min_ohlc_trades)
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "pending_ohlc_trades",
                    ohlc_result,
                    None,
                    ohlc_report,
                    None,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["pending_ohlc_trades"] = status_counts.get("pending_ohlc_trades", 0) + 1
                continue

            ready_for_tick.append((row, ohlc_variant, real_tick_variant))
            ohlc_results[candidate_id] = (ohlc_report, ohlc_result)
