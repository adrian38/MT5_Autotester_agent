from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import tempfile


LOTS_REPLACEMENTS = {
    "AdjustLotsizeToVariableValues": "false||false||0||true||N",
    "Risk": "0||0||0||20||N",
    "StartLots": "0.01||0.01||0.001000||0.100000||N",
}
USE_EVERY_TICK_KEY = "UseEveryTick"


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
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as file:
            file.write(text)
        temp_path.replace(path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def set_use_every_tick_text(text: str, enabled: bool) -> str:
    value = "true" if enabled else "false"
    lines = text.splitlines()
    found = False
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, raw_value = line.split("=", 1)
        if lhs.strip() != USE_EVERY_TICK_KEY:
            continue
        found = True
        if "||" in raw_value:
            parts = raw_value.split("||")
            parts[0] = value
            lines[index] = f"{lhs}={'||'.join(parts)}"
        else:
            lines[index] = f"{lhs}={value}"
    if found:
        return "\n".join(lines)

    insert_at = 0
    while insert_at < len(lines) and (
        not lines[insert_at].strip() or lines[insert_at].lstrip().startswith(";")
    ):
        insert_at += 1
    lines.insert(insert_at, f"{USE_EVERY_TICK_KEY}={value}||false||0||true||N")
    return "\n".join(lines)


def write_set_use_every_tick(source: Path, destination: Path, enabled: bool) -> None:
    text, encoding = read_set_with_encoding(source)
    write_set_text(destination, set_use_every_tick_text(text, enabled), encoding)


def set_matches_use_every_tick_source(source: Path, destination: Path, enabled: bool) -> bool:
    if not destination.exists():
        return False
    try:
        source_text, _ = read_set_with_encoding(source)
        destination_text, _ = read_set_with_encoding(destination)
    except OSError:
        return False
    return destination_text == set_use_every_tick_text(source_text, enabled)


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
