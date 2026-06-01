import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from mt5_env import env_value


def _resolve(token: str | None, chat_id: str | None) -> tuple[str, str] | None:
    token = token or env_value("TELEGRAM_BOT_TOKEN") or ""
    chat_id = chat_id or env_value("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        return None
    if token.lower().startswith("bot"):
        token = token[3:]
    return token, chat_id


def send_message(text: str, token: str | None = None, chat_id: str | None = None) -> str | None:
    creds = _resolve(token, chat_id)
    if not creds:
        return "Telegram no configurado (.env sin TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID)"
    tok, cid = creds
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            status = getattr(response, "status", 200)
            if 200 <= status < 300:
                return None
            return f"Telegram respondio con codigo {status}"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return f"Telegram HTTPError {exc.code} {exc.reason}: {body}".strip()
    except (urllib.error.URLError, OSError) as exc:
        return f"Telegram error de red: {exc}"


def send_async(
    text: str,
    token: str | None = None,
    chat_id: str | None = None,
    on_result: Callable[[str | None], None] | None = None,
) -> None:
    def runner() -> None:
        error = send_message(text, token, chat_id)
        if on_result is not None:
            try:
                on_result(error)
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
