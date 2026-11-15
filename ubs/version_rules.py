"""Versioned UBS parameter contracts applied to imported seed files."""

from __future__ import annotations

from dataclasses import dataclass

from ubs.v6_parameters import UBS_VERSION, V6_PARAMETER_KEYS


@dataclass(frozen=True)
class UBSVersionRule:
    version: str
    allowed_parameter_keys: frozenset[str]


UBS_VERSION_RULES: dict[str, UBSVersionRule] = {
    UBS_VERSION: UBSVersionRule(
        version=UBS_VERSION,
        allowed_parameter_keys=frozenset(V6_PARAMETER_KEYS),
    ),
}


def _is_section_marker(value: str) -> bool:
    stripped = value.strip()
    return (
        stripped.startswith(("---", "==="))
        or (len(stripped) > 6 and stripped.count("-") > 4)
    )


def version_rule(version: str) -> UBSVersionRule:
    try:
        return UBS_VERSION_RULES[version.upper()]
    except KeyError as exc:
        raise ValueError(f"Version UBS sin reglas registradas: {version}") from exc


def filter_set_parameters_for_version(
    text: str,
    version: str = UBS_VERSION,
) -> tuple[str, tuple[str, ...]]:
    """Drop real parameter lines outside the selected UBS version contract."""
    allowed = version_rule(version).allowed_parameter_keys
    kept: list[str] = []
    removed: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.startswith(";") or "=" not in stripped:
            kept.append(line)
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if _is_section_marker(value) or key in allowed:
            kept.append(line)
            continue
        removed.append(key)
    return "".join(kept), tuple(removed)
