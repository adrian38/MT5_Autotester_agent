from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ubs.account import DEFAULT_ACCOUNT_TYPE, DEFAULT_BROKER, normalize_account_type, normalize_broker

from .audit import write_audit_bundle
from .features import build_local_report, top_manual_keys
from .manual import default_manual_cache_path, default_manual_pdf_path, load_or_build_manual_index, select_manual_context
from .redaction import build_api_payload
from .schema import evidence_ids
from .snapshot import BASE_DIR, load_run_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-copilot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    diagnose = subparsers.add_parser("diagnose", help="Diagnose a UBS run")
    diagnose.add_argument("--broker", default=DEFAULT_BROKER)
    diagnose.add_argument("--account-type", "--account", dest="account_type", default=DEFAULT_ACCOUNT_TYPE)
    diagnose.add_argument("--run-id", type=int, required=True)
    diagnose.add_argument("--provider", choices=("local", "openai"), default="local")
    diagnose.add_argument("--model", default="gpt-5.4-mini")
    diagnose.add_argument("--reasoning-effort", default="low")
    diagnose.add_argument("--manual-pdf", default="")
    diagnose.add_argument("--include-manual-context", action=argparse.BooleanOptionalAction, default=True)
    diagnose.add_argument("--max-manual-keys", type=int, default=20)
    diagnose.add_argument("--max-evidence-rows", type=int, default=25)
    diagnose.add_argument("--out-dir", default="")
    args = parser.parse_args(argv)
    if args.command == "diagnose":
        return _diagnose(args)
    return 2


def _diagnose(args: argparse.Namespace) -> int:
    broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, broker)
    snapshot = load_run_snapshot(BASE_DIR, broker, account, int(args.run_id))
    manual_context = _load_manual_context(args, snapshot)
    local_report = build_local_report(snapshot, manual_context=manual_context)
    request_payload = None
    provider_response = None
    report = local_report
    if args.provider == "openai":
        from .providers.openai_provider import OpenAIProviderError, call_openai

        request_payload = build_api_payload(
            snapshot,
            local_report,
            manual_context,
            max_evidence_rows=max(1, int(args.max_evidence_rows or 25)),
        )
        try:
            report, provider_response = call_openai(
                request_payload,
                model=str(args.model),
                reasoning_effort=str(args.reasoning_effort or "low"),
                allowed_evidence_ids=evidence_ids(local_report),
            )
        except OpenAIProviderError as exc:
            if exc.status_code == 429 or exc.error_code == "insufficient_quota":
                report = local_report
                report["summary"] = f"{report['summary']} | OpenAI sin cuota: mostrando diagnostico local."
                provider_response = {
                    "error": str(exc),
                    "status_code": exc.status_code,
                    "error_code": exc.error_code,
                    "fallback": "local",
                }
            else:
                raise
    out_dir = Path(args.out_dir) if args.out_dir else BASE_DIR / "outputs" / "ai_copilot" / broker / account
    paths = write_audit_bundle(
        out_dir,
        report=report,
        request_payload=request_payload,
        provider_response=provider_response,
    )
    print(json.dumps({"report": str(paths["report"]), "summary": report["summary"]}, ensure_ascii=False))
    return 0


def _load_manual_context(args: argparse.Namespace, snapshot: dict) -> list[dict]:
    if not args.include_manual_context:
        return []
    manual_arg = str(args.manual_pdf or "").strip()
    manual_path = Path(manual_arg) if manual_arg else default_manual_pdf_path()
    if not manual_path or not manual_path.exists():
        return []
    cache_path = default_manual_cache_path(BASE_DIR)
    try:
        index = load_or_build_manual_index(manual_path, cache_path)
    except RuntimeError as exc:
        print(f"AVISO: manual UBS no indexado: {exc}", file=sys.stderr)
        return []
    keys = top_manual_keys(snapshot, limit=max(1, int(args.max_manual_keys or 20)))
    return select_manual_context(index, keys, max_keys=max(1, int(args.max_manual_keys or 20)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
