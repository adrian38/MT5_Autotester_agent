from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from ubs.params_catalog import UBS_PARAM_DESCRIPTIONS


DEFAULT_MANUAL_PDF = Path(
    r"C:\Users\Adrian\Adrian\TRADING\UBS_bot+settings\UBS_bot+settings"
    r"\Ultimate_Breakout_System_Manual_V5_0\Ultimate Breakout System Manual_V5.0.pdf"
)


def default_manual_pdf_path() -> Path | None:
    return DEFAULT_MANUAL_PDF if DEFAULT_MANUAL_PDF.exists() else None


def default_manual_cache_path(base_dir: Path) -> Path:
    return base_dir / "outputs" / "ai_copilot" / "manual_index.json"


def load_or_build_manual_index(
    manual_pdf: Path,
    cache_path: Path,
    *,
    param_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    manual_pdf = Path(manual_pdf)
    cache_path = Path(cache_path)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("source_path") == str(manual_pdf)
            and cached.get("source_mtime") == manual_pdf.stat().st_mtime
        ):
            return cached
    pages = extract_pdf_pages(manual_pdf)
    index = build_manual_index_from_pages(
        pages,
        source_path=str(manual_pdf),
        source_mtime=manual_pdf.stat().st_mtime,
        param_names=param_names,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return index


def extract_pdf_pages(path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to extract the UBS manual PDF.") from exc
    reader = PdfReader(str(path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append({"page": index, "text": _compact(text)})
    return pages


def build_manual_index_from_text(
    text: str,
    *,
    source_path: str = "fixture.txt",
    param_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    return build_manual_index_from_pages(
        [{"page": 1, "text": _compact(text)}],
        source_path=source_path,
        source_mtime=0.0,
        param_names=param_names,
    )


def build_manual_index_from_pages(
    pages: list[dict[str, Any]],
    *,
    source_path: str,
    source_mtime: float,
    param_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    names = tuple(dict.fromkeys(param_names or UBS_PARAM_DESCRIPTIONS))
    entries: dict[str, dict[str, Any]] = {}
    for key in names:
        matches = _find_key_mentions(key, pages)
        if not matches:
            if key in UBS_PARAM_DESCRIPTIONS:
                entries[key] = {
                    "key": key,
                    "summary": UBS_PARAM_DESCRIPTIONS[key],
                    "pages": [],
                    "source": "catalog",
                }
            continue
        first = matches[0]
        summary = _summarize_context(key, first["context"])
        if key in UBS_PARAM_DESCRIPTIONS and UBS_PARAM_DESCRIPTIONS[key] not in summary:
            summary = f"{UBS_PARAM_DESCRIPTIONS[key]}. Manual: {summary}"
        entries[key] = {
            "key": key,
            "summary": summary[:700],
            "pages": sorted({int(match["page"]) for match in matches}),
            "source": "manual",
        }
    return {
        "schema_version": "1.0",
        "source_path": source_path,
        "source_mtime": source_mtime,
        "entries": entries,
    }


def select_manual_context(
    index: dict[str, Any],
    keys: Iterable[str],
    *,
    max_keys: int = 20,
) -> list[dict[str, Any]]:
    entries = index.get("entries") if isinstance(index, dict) else {}
    if not isinstance(entries, dict):
        return []
    selected = []
    seen: set[str] = set()
    for key in keys:
        clean = str(key or "").strip()
        if not clean or clean in seen:
            continue
        entry = entries.get(clean)
        if not isinstance(entry, dict):
            continue
        seen.add(clean)
        selected.append(
            {
                "id": f"manual:key:{clean}",
                "key": clean,
                "summary": str(entry.get("summary") or "")[:700],
                "pages": list(entry.get("pages") or [])[:8],
                "source": str(entry.get("source") or "manual"),
            }
        )
        if len(selected) >= max_keys:
            break
    return selected


def _find_key_mentions(key: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])")
    matches = []
    for page in pages:
        text = str(page.get("text") or "")
        for match in pattern.finditer(text):
            start = max(0, match.start() - 280)
            end = min(len(text), match.end() + 420)
            matches.append({"page": int(page.get("page") or 0), "context": text[start:end]})
            break
    return matches


def _summarize_context(key: str, context: str) -> str:
    text = _compact(context)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    relevant = [sentence for sentence in sentences if key in sentence]
    if relevant:
        return _compact(" ".join(relevant[:2]))
    return text[:500]


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()
