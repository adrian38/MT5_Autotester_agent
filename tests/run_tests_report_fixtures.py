"""Registrador de mensajes compartido por los tests de reportes de run_tests."""


class ListLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)
