from __future__ import annotations

from pathlib import Path
import hashlib
import re


LOTS_REPLACEMENTS = {
    "AdjustLotsizeToVariableValues": "false||false||0||true||N",
    "Risk": "0||0||0||20||N",
    "StartLots": "0.01||0.01||0.001000||0.100000||N",
}


def read_set_with_encoding(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), "utf-16"

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def write_set_text(path: Path, text: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding, newline="\n")


def force_fixed_lot_text(text: str) -> tuple[str, set[str], set[str]]:
    lines: list[str] = []
    found: set[str] = set()
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            key = line.split("=", 1)[0].strip()
            replacement = LOTS_REPLACEMENTS.get(key)
            if replacement is not None:
                line = f"{key}={replacement}"
                found.add(key)
        lines.append(line)

    missing = set(LOTS_REPLACEMENTS) - found
    return "\n".join(lines), found, missing


def safe_part(value: str, fallback: str = "UNKNOWN") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def compact_safe_part(value: str, max_length: int = 36, fallback: str = "UNKNOWN") -> str:
    cleaned = safe_part(value, fallback)
    if len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:8]
    head_length = max(8, max_length - len(digest) - 1)
    head = cleaned[:head_length].rstrip("._-") or fallback
    return f"{head}_{digest}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
