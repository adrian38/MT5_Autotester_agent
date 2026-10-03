from __future__ import annotations

from ubs.tester_diagnostics import execution_failure_reason


REASON_FORMATS = {
    "net_profit": ("net norm", ".0f", ""),
    "profit_factor": ("PF", ".2f", ""),
    "trades": ("trades", "d", ""),
    "drawdown_pct": ("DD", ".1f", "%"),
    "recovery_factor": ("RF", ".2f", ""),
    "positive_month_ratio": ("meses+", ".0%", ""),
    # La via de riesgo no pudo completar la comparacion contra la
    # ventana de construccion: ni pasa ni suspende.
    "risk_profit_evidence": ("falta evidencia de riesgo", "", ""),
}
DEGRADATION_LABELS = {
    "degradation_net": ("net retenido", "net_retention", "percent"),
    "degradation_profit_factor": ("edge PF retenido", "pf_edge_retention", "percent"),
    "degradation_recovery": ("recovery anual retenido", "recovery_retention", "percent"),
    "degradation_drawdown": ("inflacion DD", "dd_inflation", "ratio"),
    "degradation_trade_rate": ("ritmo trades", "trade_rate_retention", "percent"),
    "generalization_residual_profit": ("neto sin top3", "residual_profit_ratio", "percent"),
    "generalization_month_breadth": ("meses OOS+", "oos_positive_month_ratio", "percent"),
    "generalization_stability": ("estabilidad OOS", "trade_curve_stability", "decimal"),
    "generalization_stability_retention": ("estabilidad retenida", "stability_retention", "percent"),
    "generalization_bootstrap_net": (
        "P(neto>0) bootstrap",
        "bootstrap_net_positive_probability",
        "percent",
    ),
    "generalization_bootstrap_pf": ("PF p05 bootstrap", "bootstrap_pf_p05", "decimal"),
}


def _degradation_reason_part(label: str, check_name: str, value_format: str, checks: object) -> str:
    check = checks.get(check_name, {}) if isinstance(checks, dict) else {}
    value = check.get("value") if isinstance(check, dict) else None
    threshold = check.get("threshold") if isinstance(check, dict) else None
    comparison = check.get("comparison") if isinstance(check, dict) else "minimum"
    if value is None or threshold is None:
        return label
    operator = "<" if comparison == "minimum" else ">"
    if value_format == "percent":
        rendered_value = f"{float(value):.0%}"
        rendered_threshold = f"{float(threshold):.0%}"
    elif value_format == "ratio":
        rendered_value = f"{float(value):.2f}x"
        rendered_threshold = f"{float(threshold):.2f}x"
    else:
        rendered_value = f"{float(value):.2f}"
        rendered_threshold = f"{float(threshold):.2f}"
    return f"{label}: {rendered_value} {operator} {rendered_threshold}"


def _robust_reason_part(reason: object, metrics: dict, checks: object) -> str:
    degradation_format = DEGRADATION_LABELS.get(str(reason))
    if degradation_format is not None:
        return _degradation_reason_part(*degradation_format, checks)
    label, fmt, suffix = REASON_FORMATS.get(str(reason), (str(reason), "", ""))
    value = metrics.get("normalized_net_profit") if str(reason) == "net_profit" else metrics.get(reason)
    if value is None:
        return label
    try:
        return f"{label}: {value:{fmt}}{suffix}"
    except (TypeError, ValueError):
        return f"{label}: {value}"


class UBSRobustnessRowsMixin:
    """Estado del arbol y formateo de motivos de la pantalla de robustez."""

    def _capture_ubs_robust_tree_state(self) -> dict[str, object]:
        if not hasattr(self, "ubs_robust_tree"):
            return {}
        tree = self.ubs_robust_tree
        top_visible_id = ""
        for item in tree.get_children():
            if tree.bbox(item):
                top_visible_id = str(self.ubs_robust_paths.get(item, {}).get("id") or "")
                break
        selected_ids = [
            str(self.ubs_robust_paths.get(item, {}).get("id") or "")
            for item in tree.selection()
        ]
        return {
            "xview": tree.xview(),
            "yview": tree.yview(),
            "focus_id": str(self.ubs_robust_paths.get(tree.focus(), {}).get("id") or ""),
            "selected_ids": {cid for cid in selected_ids if cid},
            "top_visible_id": top_visible_id,
        }

    def _restore_ubs_robust_tree_state(self, state: dict[str, object], item_by_id: dict[str, str]) -> None:
        if not state or not hasattr(self, "ubs_robust_tree"):
            return

        def _restore() -> None:
            if not hasattr(self, "ubs_robust_tree"):
                return
            tree = self.ubs_robust_tree
            try:
                xview = state.get("xview")
                if isinstance(xview, tuple) and xview:
                    tree.xview_moveto(float(xview[0]))

                selected_items = [
                    item_by_id[cid]
                    for cid in state.get("selected_ids", set())
                    if isinstance(cid, str) and cid in item_by_id
                ]
                focus_id = state.get("focus_id")
                focus_item = item_by_id.get(focus_id) if isinstance(focus_id, str) else None
                if selected_items:
                    tree.selection_set(selected_items)
                    tree.focus(focus_item or selected_items[0])

                anchor_item = focus_item or (selected_items[0] if selected_items else None)
                if not anchor_item:
                    top_visible_id = state.get("top_visible_id")
                    anchor_item = item_by_id.get(top_visible_id) if isinstance(top_visible_id, str) else None
                if anchor_item:
                    children = list(tree.get_children())
                    if children:
                        try:
                            tree.yview_moveto(children.index(anchor_item) / max(len(children), 1))
                        except ValueError:
                            pass
                else:
                    yview = state.get("yview")
                    if isinstance(yview, tuple) and yview:
                        tree.yview_moveto(float(yview[0]))
            except Exception:
                pass

        self.ubs_robust_tree.after_idle(_restore)

    def _on_ubs_robust_tree_click(self, event) -> str | None:
        if not hasattr(self, "ubs_robust_tree"):
            return None
        item, column = self._tree_item_from_event(self.ubs_robust_tree, event)
        if not item or column != "#1":
            return None
        info = self.ubs_robust_paths.get(item, {})
        cid = info.get("id", item)
        if cid in self.ubs_robust_checked:
            self.ubs_robust_checked.remove(cid)
        else:
            self.ubs_robust_checked.add(cid)
        values = list(self.ubs_robust_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in self.ubs_robust_checked)
            self.ubs_robust_tree.item(item, values=values)
        return "break"

    def _robustness_bonus_for_status(self, status: str, positive: object, negative: object) -> float | None:
        try:
            if status == "accepted":
                return float(positive or 0.0)
            if status == "rejected":
                return float(negative or 0.0)
        except (TypeError, ValueError):
                return None
        return None

    def _format_ubs_robustness_status(self, status: str) -> str:
        if status == "no_trades":
            return "0 ops/no aceptado"
        return self._format_ubs_status(status)

    def _ubs_robust_reason(self, status: str, metrics: dict, degradation: dict | None = None) -> str:
        if status == "pending":
            return "pendiente"
        if status == "no_report":
            return "sin reporte OOS"
        if status == "parse_error":
            return "error al parsear reporte OOS"
        if status == "report_mismatch":
            return "mismatch symbol/TF OOS"
        reason = execution_failure_reason(degradation) or execution_failure_reason(metrics)
        if reason:
            return f"{reason}; no pasa robustez"
        if status == "no_trades":
            return "reporte correcto, 0 operaciones; no pasa robustez"
        reasons = metrics.get("reasons") or []
        if not reasons:
            return ""
        checks = degradation.get("checks", {}) if isinstance(degradation, dict) else {}
        return " | ".join(
            _robust_reason_part(reason, metrics, checks) for reason in reasons
        )

    def _format_ubs_degradation_value(self, degradation: dict, check_name: str, *, percentage: bool) -> str:
        checks = degradation.get("checks", {}) if isinstance(degradation, dict) else {}
        check = checks.get(check_name, {}) if isinstance(checks, dict) else {}
        value = check.get("value") if isinstance(check, dict) else None
        if value is None:
            return ""
        try:
            return f"{float(value):.0%}" if percentage else f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return ""
