import threading
import ssl
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable

from mt5_env import env_value


SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"


def _resolve(token: str | None, chat_id: str | None) -> tuple[str, str] | None:
    token = token or env_value("TELEGRAM_BOT_TOKEN") or ""
    chat_id = chat_id or env_value("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        return None
    if token.lower().startswith("bot"):
        token = token[3:]
    return token, chat_id


def _trust_allows_server_auth(trust: object) -> bool:
    return trust is True or SERVER_AUTH_OID in {str(item) for item in (trust or ())}


def _windows_ca_pem() -> str:
    if not hasattr(ssl, "enum_certificates"):
        return ""
    pems: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            certs = ssl.enum_certificates(store)  # type: ignore[attr-defined]
        except OSError:
            continue
        for cert_bytes, encoding, trust in certs:
            if encoding != "x509_asn" or not _trust_allows_server_auth(trust):
                continue
            try:
                pems.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
            except (ValueError, TypeError):
                continue
    return "".join(dict.fromkeys(pems))


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    ca_bundle = env_value("TELEGRAM_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
    if ca_bundle:
        path = Path(ca_bundle).expanduser()
        if path.exists():
            context.load_verify_locations(cafile=str(path))
    windows_pem = _windows_ca_pem()
    if windows_pem:
        context.load_verify_locations(cadata=windows_pem)
    return context


def _insecure_ssl_allowed() -> bool:
    value = (env_value("TELEGRAM_ALLOW_INSECURE_SSL") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "si", "sí"}


def send_message(text: str, token: str | None = None, chat_id: str | None = None) -> str | None:
    creds = _resolve(token, chat_id)
    if not creds:
        return "Telegram no configurado (.env sin TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID)"
    tok, cid = creds
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        context = ssl._create_unverified_context() if _insecure_ssl_allowed() else _ssl_context()
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
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
