"""Lectura del rango base inmutable de un run UBS."""
from __future__ import annotations

import json


def ubs_run_base_dates(config_json: object) -> tuple[str, str]:
    """Read the immutable base-test range stored when a UBS run was created."""

    try:
        config = json.loads(str(config_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", ""
    if not isinstance(config, dict):
        return "", ""
    execution = config.get("execution")
    args = config.get("args")
    execution = execution if isinstance(execution, dict) else {}
    args = args if isinstance(args, dict) else {}
    from_date = str(execution.get("from_date") or args.get("from_date") or "").strip()
    to_date = str(execution.get("to_date") or args.get("to_date") or "").strip()
    return from_date, to_date
