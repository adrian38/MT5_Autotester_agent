"""Respuestas de la API: estado, universo, portafolios, runs y log."""
from __future__ import annotations

import contextlib
import json
import os
import platform
import sqlite3
from pathlib import Path
from typing import Any

from . import node_settings, node_snapshots
from .common import safe_int, save_json, utc_now
from .portfolio_save import (
    exclude_portfolio_members_payload,
    normalize_portfolio_alias,
    requalify_portfolio_member_payload,
    save_portfolio_payload,
    set_portfolio_alias_payload,
)


class JobApiMixin:
    """Respuestas de la API: estado, universo, portafolios, runs y log."""

    def _launch_defaults(self, cfg: Any) -> dict[str, Any]:
        defaults = self.config.get("defaults") if isinstance(self.config.get("defaults"), dict) else {}
        return {
            "cycles": safe_int(defaults.get("cycles", 1), 1, minimum=1, maximum=100),
            "generations": safe_int(defaults.get("generations", node_settings.setting(cfg, "General", "ubs_generation_count", "1")), 1, minimum=1),
            "variants_per_seed": safe_int(defaults.get("variants_per_seed", node_settings.setting(cfg, "General", "ubs_variants_per_seed", "10")), 10, minimum=1),
            "max_seeds": safe_int(defaults.get("max_seeds", node_settings.setting(cfg, "General", "ubs_max_seeds", "30")), 30, minimum=0),
            "generation_mode": str(defaults.get("generation_mode", node_settings.setting(cfg, "General", "ubs_generation_mode", "production"))),
            "random_seed": defaults.get("random_seed"),
            "max_workers": safe_int(node_settings.setting(cfg, "Multiterminal", "workers", "1"), 1, minimum=1, maximum=64),
            "run_robustness": node_settings.setting_bool(cfg, "General", "ubs_robust_auto", False),
            "run_final_tick": node_settings.setting_bool(cfg, "General", "ubs_final_tick_auto", False),
            "run_final_tick_6m": node_settings.setting_bool(cfg, "General", "ubs_final_tick_6m_auto", False),
            "run_regression": node_settings.setting_bool(cfg, "General", "ubs_regression_auto", False),
            "cleanup_after_run": bool(node_settings.historical_cleanup_scripts(self.config, required=False)),
        }

    def status(self) -> dict[str, Any]:
        result, task_queue, job_observed_at, job_snapshot_stale = self._read_status_snapshot()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        try:
            cfg = node_settings.read_settings(settings_path)
            db = node_snapshots.database_snapshot(node_settings.memory_path(self.config, cfg))
            launch_defaults = self._launch_defaults(cfg)
        except Exception as exc:
            db = {"available": False, "error": str(exc)}
            launch_defaults = {}
        return {
            "node": {
                "id": self.config.get("node_id"),
                "name": self.config.get("display_name") or self.config.get("node_id"),
                "broker": self.config.get("broker"),
                "account_type": self.config.get("account_type"),
                "machine": os.environ.get("COMPUTERNAME") or platform.node(),
                "user": os.environ.get("USERNAME") or os.environ.get("USER"),
                "project_dir": str(project),
            },
            "job": result,
            "job_observed_at": job_observed_at,
            "job_snapshot_stale": job_snapshot_stale,
            "task_queue": task_queue,
            "database": db,
            "launch_defaults": launch_defaults,
            "capabilities": {
                "guided_batches_v1": True,
                "guided_launch_options_v1": True,
                "worker_override": True,
                "pipeline_controls": True,
                "failed_resume": True,
                "cycles": True,
                "repair_runs": True,
                "universe_management": True,
                "universe_sync": True,
                "portfolio_views": True,
                "task_queue": True,
                "application_restart": bool(getattr(self, "application_restart_available", False)),
                "historical_cleanup": bool(node_settings.historical_cleanup_scripts(self.config, required=False)),
                "live_account_audit": True,
                "live_audit_restore_account": True,
            },
            "observed_at": utc_now(),
        }

    def universe(self) -> dict[str, Any]:
        with self.lock:
            rows, disabled, seed_enabled = node_settings._load_universe_rows(self.config)
        generation_enabled = sum(1 for row in rows if row["generation_enabled"])
        seed_only = sum(1 for row in rows if not row["generation_enabled"] and row["seeds_enabled"])
        return {"node": {"id": self.config.get("node_id"), "name": self.config.get("display_name") or self.config.get("node_id"), "broker": self.config.get("broker"), "account_type": self.config.get("account_type")}, "symbols": rows, "summary": {"total": len(rows), "generation_enabled": generation_enabled, "generation_disabled": len(rows) - generation_enabled, "seed_only": seed_only}, "observed_at": utc_now()}

    def update_universe(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = payload.get("symbols")
        if not isinstance(values, list) or not values:
            raise ValueError("symbols debe ser una lista no vacía")
        requested = {str(value).strip().upper() for value in values if str(value).strip()}
        generation, seeds = payload.get("generation_enabled"), payload.get("seeds_enabled")
        if generation is None and seeds is None:
            raise ValueError("Indica generation_enabled o seeds_enabled")
        if generation is not None and not isinstance(generation, bool):
            raise ValueError("generation_enabled debe ser booleano")
        if seeds is not None and not isinstance(seeds, bool):
            raise ValueError("seeds_enabled debe ser booleano")
        with self.lock:
            rows, disabled, seed_enabled = node_settings._load_universe_rows(self.config)
            unknown = requested - {str(row["symbol"]).upper() for row in rows}
            if unknown:
                raise ValueError(f"Símbolos desconocidos: {', '.join(sorted(unknown))}")
            if generation is True:
                disabled.difference_update(requested); seed_enabled.difference_update(requested)
            elif generation is False:
                disabled.update(requested); seed_enabled.difference_update(requested)
            if seeds is not None:
                eligible = requested & disabled
                if seeds: seed_enabled.update(eligible)
                else: seed_enabled.difference_update(eligible)
            _, policy_path = node_settings._universe_paths(self.config)
            save_json(policy_path, {"disabled": sorted(disabled), "seed_enabled_when_disabled": sorted(seed_enabled & disabled)})
        return self.universe()

    def portfolios(self, scope: str = "full_history") -> dict[str, Any]:
        portfolio_scope = "monthly" if str(scope).strip().lower() == "monthly" else "full_history"
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute(): settings_path = project / settings_path
        db_path = node_settings.memory_path(self.config, node_settings.read_settings(settings_path))
        if not db_path.is_file(): raise ValueError(f"No existe la memoria UBS: {db_path}")
        with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc", (portfolio_scope,)).fetchall() if node_settings._table_exists(conn,"portfolios") else []
        def value(row: sqlite3.Row, key: str, default: Any = None) -> Any: return row[key] if key in row.keys() else default
        portfolios = [{"id":int(value(row,"id",0) or 0),"created_at":str(value(row,"created_at","") or ""),"name":str(value(row,"name","") or ""),"portfolio_type":str(value(row,"portfolio_type",value(row,"type","")) or ""),"portfolio_scope":portfolio_scope,"target_month":int(value(row,"target_month",0) or 0) or None,"capital":float(value(row,"capital",value(row,"account_capital",0)) or 0),"total_net_profit":float(value(row,"total_net_profit",0) or 0),"actual_valley_dd":float(value(row,"actual_valley_dd",0) or 0),"target_valley_dd":float(value(row,"target_valley_dd",0) or 0),"valley_usage_pct":float(value(row,"valley_usage_pct",0) or 0),"actual_point_dd":float(value(row,"actual_point_dd",0) or 0),"target_point_dd":float(value(row,"target_point_dd",0) or 0),"point_usage_pct":float(value(row,"point_usage_pct",0) or 0),"total_lot":float(value(row,"total_lot",0) or 0),"total_units":int(value(row,"total_units",0) or 0),"active_strategies":int(value(row,"active_strategies",0) or 0),"target_strategies":int(value(row,"target_strategies",0) or 0),"stop_reason":str(value(row,"stop_reason","") or ""),"binding_constraint":str(value(row,"binding_constraint","") or "")} for row in rows]
        if portfolio_scope == "full_history":
            for portfolio, row in zip(portfolios, rows):
                try:
                    metrics = json.loads(value(row, "metrics_json", "{}") or "{}")
                    inputs = metrics.get("inputs") if isinstance(metrics, dict) else {}
                    portfolio["alias"] = normalize_portfolio_alias(
                        inputs.get("portfolio_alias") if isinstance(inputs, dict) else ""
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    portfolio["alias"] = ""
        return {"node":{"id":self.config.get("node_id"),"name":self.config.get("display_name") or self.config.get("node_id"),"broker":self.config.get("broker"),"account_type":self.config.get("account_type")},"scope":portfolio_scope,"portfolios":portfolios,"summary":{"total":len(portfolios),"strategies":sum(item["active_strategies"] for item in portfolios),"latest_id":portfolios[0]["id"] if portfolios else None},"observed_at":utc_now()}

    def portfolio_detail(self, portfolio_id: int, scope: str = "full_history") -> dict[str, Any]:
        listing=self.portfolios(scope); selected=next((item for item in listing["portfolios"] if item["id"]==portfolio_id),None)
        if selected is None: raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
        project=Path(str(self.config["project_dir"])).expanduser().resolve(); settings_path=Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute(): settings_path=project/settings_path
        db_path=node_settings.memory_path(self.config,node_settings.read_settings(settings_path))
        with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro",uri=True)) as conn:
            conn.row_factory=sqlite3.Row; row=conn.execute("select metrics_json from portfolios where id=?",(portfolio_id,)).fetchone()
            try:
                parsed=json.loads(row["metrics_json"] or "{}") if row else {}; metrics=parsed if isinstance(parsed,dict) else {}
            except json.JSONDecodeError: metrics={}
            members=[dict(item) for item in conn.execute("select * from portfolio_allocations where portfolio_id=? order by variant_key,set_id,units desc",(portfolio_id,)).fetchall()] if node_settings._table_exists(conn,"portfolio_allocations") else []
            if not members and node_settings._table_exists(conn,"portfolio_members"):
                for item in conn.execute("select * from portfolio_members where portfolio_id=? order by lot desc",(portfolio_id,)).fetchall():
                    raw=dict(item); members.append({"variant_key":raw.get("variant_key") or "","variant_label":raw.get("variant_label") or "","set_id":raw.get("set_path") or "","candidate_id":raw.get("candidate_id") or "","symbol":raw.get("symbol") or "","timeframe":raw.get("period") or "","units":int(round(float(raw.get("lot") or 0)/.01)),"lot":float(raw.get("lot") or 0),"lot_size_step":float(raw.get("lot_size_step") or .01),"net_profit_contribution":float(raw.get("combined_net_profit") or 0),"standalone_valley_dd":float(raw.get("standalone_dd") or 0),"standalone_point_dd":0.0,"set_path":raw.get("set_path") or "","margin_required":0.0,"margin_pct":0.0})
        selected["metrics"]={"inputs":metrics.get("inputs") if isinstance(metrics.get("inputs"),dict) else {},"stress_bootstrap":metrics.get("stress_bootstrap") if isinstance(metrics.get("stress_bootstrap"),dict) else {},"common_set_ids":metrics.get("common_set_ids") if isinstance(metrics.get("common_set_ids"),list) else [],"variant_order":metrics.get("variant_order") if isinstance(metrics.get("variant_order"),list) else []}
        selected["members"]=[{"variant_key":str(raw.get("variant_key") or ""),"variant_label":str(raw.get("variant_label") or ""),"set_id":str(raw.get("set_id") or ""),"set_name":Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,"candidate_id":str(raw.get("candidate_id") or ""),"symbol":str(raw.get("symbol") or ""),"timeframe":str(raw.get("timeframe") or ""),"units":int(raw.get("units") or 0),"lot":float(raw.get("lot") or 0),"lot_size_step":float(raw.get("lot_size_step") or 0),"net_profit_contribution":float(raw.get("net_profit_contribution") or 0),"standalone_valley_dd":float(raw.get("standalone_valley_dd") or 0),"standalone_point_dd":float(raw.get("standalone_point_dd") or 0),"margin_required":float(raw.get("margin_required") or 0),"margin_pct":float(raw.get("margin_pct") or 0)} for raw in members]
        return {"node":listing["node"],"scope":listing["scope"],"portfolio":selected,"observed_at":utc_now()}

    def save_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        _cfg, db_path = self._settings_and_memory()
        result = save_portfolio_payload(db_path, payload)
        if not result.get("deduplicated"):
            portfolio_id = int(result["portfolio_id"])
            self._send_telegram(
                f"portfolio-save:{result['request_id']}",
                f"Portfolio Builder guardado: #{portfolio_id}, "
                f"net {float(result.get('total_net_profit') or 0):,.2f}, "
                f"lote {float(result.get('total_lot') or 0):.2f}, "
                f"{int(result.get('active_strategies') or 0)} estrategias",
            )
            self._persist()
        return result

    def set_portfolio_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        _cfg, db_path = self._settings_and_memory()
        result = set_portfolio_alias_payload(db_path, payload)
        self._persist()
        return result

    def delete_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        scope = "monthly" if str(payload.get("scope") or "").strip().lower() == "monthly" else "full_history"
        _cfg, db_path = self._settings_and_memory()
        with contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("begin immediate")
            row = conn.execute(
                "select id from portfolios where id=? and coalesce(nullif(portfolio_scope,''),'full_history')=?",
                (portfolio_id, scope),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
            for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members", "portfolio_versions"):
                if node_settings._table_exists(conn, table):
                    conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
            deleted = conn.execute("delete from portfolios where id=?", (portfolio_id,))
            if deleted.rowcount != 1:
                conn.rollback()
                raise RuntimeError(f"No se pudo borrar el portafolio #{portfolio_id}")
            conn.commit()
        with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10)) as check:
            if check.execute("select 1 from portfolios where id=?", (portfolio_id,)).fetchone() is not None:
                raise RuntimeError(f"El portafolio #{portfolio_id} sigue presente después del commit")
        self._persist()
        return {"deleted": True, "portfolio_id": portfolio_id, "scope": scope}

    def exclude_portfolio_members(self, payload: dict[str, Any]) -> dict[str, Any]:
        _cfg, db_path = self._settings_and_memory()
        result = exclude_portfolio_members_payload(
            self.config["project_dir"],
            str(self.config.get("broker") or ""),
            db_path,
            payload,
        )
        self._persist()
        return result

    def requalify_portfolio_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Cambia el estado de una estrategia ya excluida (los tres motivos y el pool).

        Corre aqui por lo mismo que la exclusion individual: el manager solo lee
        esta memoria por una copia, y escribirla por CIFS o por un bind mount de
        Docker falla con "disk I/O error" (el modo WAL necesita un `-shm` que esos
        sistemas de ficheros no respaldan). `requalified` es la confirmacion que
        espera el manager.
        """
        _cfg, db_path = self._settings_and_memory()
        result = requalify_portfolio_member_payload(
            self.config["project_dir"],
            str(self.config.get("broker") or ""),
            db_path,
            payload,
        )
        self._persist()
        return result

    def runs(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        cfg = node_settings.read_settings(settings_path)
        path = node_settings.memory_path(self.config, cfg)
        page_limit = max(1, min(int(limit), 100))
        page_offset = max(0, int(offset))
        page = node_snapshots.completed_runs_snapshot(path, page_limit + 1, page_offset)
        has_more = len(page) > page_limit
        runs = page[:page_limit]
        return {
            "runs": runs,
            "pagination": {
                "limit": page_limit,
                "offset": page_offset,
                "has_more": has_more,
                "next_offset": page_offset + len(runs) if has_more else None,
            },
            "memory_path": str(path),
            "observed_at": utc_now(),
        }

    def log_tail(self, lines: int = 200) -> dict[str, Any]:
        job, _, _, _ = self._read_status_snapshot()
        path_text = job.get("log_path")
        if not path_text or not Path(path_text).is_file():
            return {"lines": [], "log_path": path_text}
        content = Path(path_text).read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": content[-max(1, min(lines, 2000)):], "log_path": path_text}
