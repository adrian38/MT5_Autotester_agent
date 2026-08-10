"""Deteccion de seeds duplicadas para la importacion de `.set`.

El hash del contenido normalizado solo detecta ficheros identicos. Como el EA se
va actualizando, la misma estrategia reaparece con claves nuevas anadidas y deja
de tener el mismo hash aunque sea funcionalmente la misma seed. Este modulo
anade una segunda pasada "equivalente": dos seeds del mismo simbolo/timeframe
son duplicadas si coinciden en *todas* las claves que comparten y el solape es
suficientemente grande.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Solape minimo exigido para considerar dos seeds equivalentes. Las seeds UBS
# tienen entre 128 y 280 claves, asi que 100 claves comunes identicas descarta
# coincidencias accidentales entre familias distintas.
MIN_SHARED_KEYS = 100
MIN_SHARED_RATIO = 0.60

DUPLICATE_EXACT = "exact"
DUPLICATE_EQUIVALENT = "equivalent"


def parse_set_params(text: str) -> dict[str, str]:
    """Extrae `clave -> valor` de un `.set`, descartando rangos de optimizacion.

    Mismas reglas que `run_tests.load_set_params`, pero sobre texto ya leido.
    """
    params: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.split("||", 1)[0].strip()
    return params


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class SeedFingerprint:
    """Identidad de una seed a efectos de deduplicacion."""

    path: Path
    content_hash: str
    symbol: str
    period: str
    params: dict[str, str] = field(compare=False)

    @classmethod
    def from_text(
        cls, path: Path, normalized_text: str, symbol: str, period: str
    ) -> "SeedFingerprint":
        return cls(
            path=path,
            content_hash=content_hash(normalized_text),
            symbol=(symbol or "UNKNOWN").upper(),
            period=(period or "UNKNOWN").upper(),
            params=parse_set_params(normalized_text),
        )


class SeedDuplicateIndex:
    """Indice incremental de seeds ya presentes en el destino."""

    def __init__(
        self,
        min_shared_keys: int = MIN_SHARED_KEYS,
        min_shared_ratio: float = MIN_SHARED_RATIO,
    ) -> None:
        self.min_shared_keys = min_shared_keys
        self.min_shared_ratio = min_shared_ratio
        self._by_hash: dict[tuple[str, str, str], SeedFingerprint] = {}
        self._by_scope: dict[tuple[str, str], list[SeedFingerprint]] = {}

    def __len__(self) -> int:
        return sum(len(group) for group in self._by_scope.values())

    @staticmethod
    def _scope(fingerprint: SeedFingerprint) -> tuple[str, str]:
        return (fingerprint.symbol, fingerprint.period)

    def add(self, fingerprint: SeedFingerprint) -> None:
        scope = self._scope(fingerprint)
        self._by_hash.setdefault((*scope, fingerprint.content_hash), fingerprint)
        self._by_scope.setdefault(scope, []).append(fingerprint)

    def find_duplicate(
        self, fingerprint: SeedFingerprint
    ) -> tuple[SeedFingerprint, str] | None:
        """Devuelve `(seed existente, motivo)` o `None` si es una seed nueva.

        La identidad de una seed es `(simbolo, timeframe, parametros)`: dos
        ficheros con el mismo contenido pero destinados a simbolos distintos
        son seeds distintas, porque el agente los ejecuta sobre simbolos
        distintos.
        """
        scope = self._scope(fingerprint)
        existing = self._by_hash.get((*scope, fingerprint.content_hash))
        if existing is not None:
            return existing, DUPLICATE_EXACT

        candidate_keys = set(fingerprint.params)
        if not candidate_keys:
            return None

        for other in self._by_scope.get(scope, ()):
            other_keys = set(other.params)
            shared = candidate_keys & other_keys
            if len(shared) < self.min_shared_keys:
                continue
            smaller = min(len(candidate_keys), len(other_keys))
            if smaller and len(shared) < self.min_shared_ratio * smaller:
                continue
            if all(fingerprint.params[key] == other.params[key] for key in shared):
                return other, DUPLICATE_EQUIVALENT
        return None


@dataclass(frozen=True)
class DuplicateGroup:
    """Una seed que se conserva y las que sobran por ser la misma."""

    keeper: SeedFingerprint
    redundant: tuple[tuple[SeedFingerprint, str], ...]

    @property
    def size(self) -> int:
        return len(self.redundant) + 1


def scan_duplicates(
    fingerprints: Iterable[SeedFingerprint],
    priority: Callable[[SeedFingerprint], Sequence] | None = None,
    min_shared_keys: int = MIN_SHARED_KEYS,
    min_shared_ratio: float = MIN_SHARED_RATIO,
) -> list[DuplicateGroup]:
    """Agrupa las seeds duplicadas de un pool ya existente.

    `priority` decide cual se conserva: se ordena de menor a mayor y la primera
    de cada grupo es la que se queda. Sin `priority` gana la ruta mas corta y,
    a igualdad, el orden alfabetico, de modo que el resultado es determinista.
    """

    def _default_priority(fingerprint: SeedFingerprint) -> Sequence:
        return (len(str(fingerprint.path)), str(fingerprint.path))

    rank = priority or _default_priority
    ordered = sorted(fingerprints, key=lambda fp: (tuple(rank(fp)), str(fp.path)))

    index = SeedDuplicateIndex(min_shared_keys, min_shared_ratio)
    groups: dict[Path, list[tuple[SeedFingerprint, str]]] = {}
    keepers: dict[Path, SeedFingerprint] = {}

    for fingerprint in ordered:
        match = index.find_duplicate(fingerprint)
        if match is None:
            index.add(fingerprint)
            continue
        keeper, reason = match
        # El match apunta a la seed indexada, que siempre es un keeper.
        keepers.setdefault(keeper.path, keeper)
        groups.setdefault(keeper.path, []).append((fingerprint, reason))

    return [
        DuplicateGroup(keeper=keepers[path], redundant=tuple(redundant))
        for path, redundant in groups.items()
    ]
