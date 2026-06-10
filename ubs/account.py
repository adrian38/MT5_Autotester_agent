from __future__ import annotations

from pathlib import Path


ACCOUNT_TYPES = ("ECN", "PRO")
DEFAULT_ACCOUNT_TYPE = "ECN"


def normalize_account_type(value: object) -> str:
    account = str(value or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return account if account in ACCOUNT_TYPES else DEFAULT_ACCOUNT_TYPE


def account_memory_path(base_dir: Path, account_type: object) -> Path:
    account = normalize_account_type(account_type)
    return base_dir / "outputs" / f"ubs_memory_{account}.sqlite"


def account_output_dir(base_dir: Path, account_type: object) -> Path:
    account = normalize_account_type(account_type)
    return base_dir / "outputs" / "ubs_agent" / account


def account_seed_dir(base_dir: Path, account_type: object) -> Path:
    account = normalize_account_type(account_type)
    return base_dir / "sets" / "ubs_ready" / account
