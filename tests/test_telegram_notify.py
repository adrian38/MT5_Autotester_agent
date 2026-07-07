import unittest
from unittest.mock import Mock, patch

import telegram_notify


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class TelegramNotifyTests(unittest.TestCase):
    def tearDown(self) -> None:
        telegram_notify._ssl_context.cache_clear()

    def test_send_message_uses_ssl_context(self) -> None:
        context = object()
        with patch.object(telegram_notify, "_insecure_ssl_allowed", return_value=False):
            with patch.object(telegram_notify, "_ssl_context", return_value=context):
                with patch.object(telegram_notify.urllib.request, "urlopen", return_value=_FakeResponse()) as urlopen:
                    error = telegram_notify.send_message("hello", token="bot123:abc", chat_id="42")

        self.assertIsNone(error)
        self.assertIs(urlopen.call_args.kwargs["context"], context)

    def test_send_message_can_use_explicit_insecure_ssl_context(self) -> None:
        context = object()
        with patch.object(telegram_notify, "_insecure_ssl_allowed", return_value=True):
            with patch.object(telegram_notify.ssl, "_create_unverified_context", return_value=context):
                with patch.object(telegram_notify.urllib.request, "urlopen", return_value=_FakeResponse()) as urlopen:
                    error = telegram_notify.send_message("hello", token="bot123:abc", chat_id="42")

        self.assertIsNone(error)
        self.assertIs(urlopen.call_args.kwargs["context"], context)

    def test_windows_ca_pem_filters_server_auth_certificates(self) -> None:
        with patch.object(telegram_notify.ssl, "enum_certificates", Mock()) as enum_certificates:
            enum_certificates.side_effect = [
                [
                    (b"root", "x509_asn", True),
                    (b"ignored", "pkcs_7_asn", True),
                    (b"client", "x509_asn", ("1.3.6.1.5.5.7.3.2",)),
                ],
                [(b"ca", "x509_asn", (telegram_notify.SERVER_AUTH_OID,))],
            ]
            with patch.object(telegram_notify.ssl, "DER_cert_to_PEM_cert", side_effect=lambda data: data.decode() + "\n"):
                pem = telegram_notify._windows_ca_pem()

        self.assertEqual(pem, "root\nca\n")


if __name__ == "__main__":
    unittest.main()
