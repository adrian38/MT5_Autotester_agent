"""Cuenta y servidor a los que esta conectado cada terminal MT5.

El pool multiterminal reparte el mismo trabajo entre varias instalaciones, y
cada una usa la sesion que tenga abierta. Un broker puede ofrecer el mismo
catalogo con ortografias distintas segun el servidor —AXI escribe `EURUSD.sa`
en `Axi-US51-Live`, `EURUSD` en `Axi-US50-Live` y `EURUSD.s` en
`Axi-US50-Demo`—, asi que un terminal apuntado al servidor equivocado acepta
trabajo y falla en todos los jobs con `symbol X not exist`, sin que nada lo
distinga de un fallo tecnico.

El journal diario del propio terminal es la unica fuente de primera mano que no
exige abrir MT5: cada autorizacion deja `'<login>': authorized on <servidor>`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_AUTHORIZED = re.compile(
    r"'(?P<login>\d+)':\s*authorized on\s+(?P<server>[^\s]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TerminalSession:
    """Ultima sesion que el terminal dejo escrita en su journal."""

    login: str
    server: str
    log_path: Path


def _terminal_log_text(path: Path) -> str:
    """Lee un journal de MT5, que el terminal escribe en UTF-16LE."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    for encoding in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # Un journal UTF-8 leido como UTF-16 no falla, solo sale ilegible; la
        # marca de autorizacion decide cual de las dos lecturas vale.
        if _AUTHORIZED.search(text) or "Terminal" in text:
            return text
    return ""


def terminal_log_files(data_dir: Path | None) -> list[Path]:
    """Journals del terminal, del mas reciente al mas antiguo."""
    if data_dir is None:
        return []
    logs_dir = Path(data_dir) / "logs"
    try:
        files = [path for path in logs_dir.glob("*.log") if path.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda path: path.name, reverse=True)


def terminal_last_session(
    data_dir: Path | None, *, max_logs: int = 5,
) -> TerminalSession | None:
    """Ultima cuenta/servidor que el terminal autorizo, o None si no consta.

    Devolver None significa "no se sabe", nunca "no coincide": un terminal
    recien instalado o con los logs rotados no debe quedarse fuera del pool.
    """
    for log_path in terminal_log_files(data_dir)[:max_logs]:
        text = _terminal_log_text(log_path)
        if not text:
            continue
        matches = list(_AUTHORIZED.finditer(text))
        if not matches:
            continue
        last = matches[-1]
        return TerminalSession(
            login=last.group("login"),
            server=last.group("server").strip().strip(",;"),
            log_path=log_path,
        )
    return None


def servers_seen_in_log(data_dir: Path | None, *, max_logs: int = 1) -> tuple[str, ...]:
    """Servidores distintos que aparecen en los journals mas recientes.

    Un terminal que rota entre cuentas de varios servidores no tiene una
    identidad estable, y conviene decirlo en el aviso en vez de juzgarlo por la
    ultima linea.
    """
    servers: dict[str, None] = {}
    for log_path in terminal_log_files(data_dir)[:max_logs]:
        for match in _AUTHORIZED.finditer(_terminal_log_text(log_path)):
            servers[match.group("server").strip().strip(",;")] = None
    return tuple(servers)


def same_server(left: object, right: object) -> bool:
    """Compara nombres de servidor MT5 ignorando mayusculas y espacios."""
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()
