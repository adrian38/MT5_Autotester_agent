from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

try:
    import psycopg
except ImportError:  # pragma: no cover - unit tests exercise the pure helpers
    psycopg = None  # type: ignore[assignment]


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "database" / "init"
RESTORABLE_CANDIDATE_STATUSES = {
    "accepted",
    "history_ok",
    "no_history",
    "no_report",
    "report_mismatch",
}
MAX_SET_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ConfigSpec:
    key: str
    source_path: Path
    relative_path: str
    scope: str
    encrypted: bool = False
    required: bool = False
    source_mtime_ns: int | None = None
    byte_size: int | None = None


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise ValueError(f"Ruta de configuracion no segura: {value!r}")
    return path.as_posix()


def _resolve_config_path(
    root: Path,
    value: object,
    fallback: str,
    source_project_dir: object = "",
) -> tuple[Path, str]:
    text = str(value or fallback).strip() or fallback
    windows_path = PureWindowsPath(text)
    windows_root = PureWindowsPath(str(source_project_dir or ""))
    if windows_path.is_absolute() and windows_root.is_absolute():
        try:
            windows_relative = windows_path.relative_to(windows_root)
        except ValueError:
            pass
        else:
            relative = _safe_relative_path(PurePosixPath(*windows_relative.parts).as_posix())
            return root.joinpath(*PurePosixPath(relative).parts), relative
    source = Path(text).expanduser()
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    try:
        relative = source.relative_to(root).as_posix()
    except ValueError:
        relative = fallback
    return source, _safe_relative_path(relative)


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if path.is_file():
        parser.read(path, encoding="utf-8")
    return parser


def _media_type(path: Path) -> str:
    return {
        ".ini": "text/ini",
        ".json": "application/json",
        ".txt": "text/plain",
        ".env": "text/plain",
        ".set": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")


def build_manifest(
    root: Path,
    context: dict[str, str],
    manager: dict[str, Any],
    *,
    include_strategy_sets: bool = True,
) -> list[ConfigSpec]:
    """Return every small operational input needed by a node, excluding reports/runtime."""
    root = root.expanduser().resolve()
    broker = context["broker"].lower()
    account_scope = f"{context['broker']}_{context['account_type']}"
    settings_source, settings_target = _resolve_config_path(
        root, manager.get("settings_file"), "ui_settings.ini", manager.get("project_dir")
    )
    settings = _read_ini(settings_source)
    template_value = settings.get("Paths", "template_path", fallback="tester_template.ini")
    template_source, template_target = _resolve_config_path(
        root, template_value, "tester_template.ini", manager.get("project_dir")
    )

    specs = [
        ConfigSpec("manager_node", root / "manager_node.json", "manager_node.json", "node", True, True),
        ConfigSpec("environment", root / ".env", ".env", "node", True, False),
        ConfigSpec("ui_settings", settings_source, settings_target, "node", False, True),
        ConfigSpec("tester_template", template_source, template_target, "broker", False, True),
        ConfigSpec("compile_root", root / "compile_root.txt", "compile_root.txt", "node"),
        ConfigSpec("experts_root", root / "experts_root.txt", "experts_root.txt", "node"),
        ConfigSpec("experts_list", root / "experts_list.txt", "experts_list.txt", "node"),
        ConfigSpec(
            "asset_universe",
            root / "assets" / f"{broker}_assets.ini",
            f"assets/{broker}_assets.ini",
            "broker",
            required=True,
        ),
        ConfigSpec(
            "broker_normalization",
            root / "assets" / f"{broker}_normalization.json",
            f"assets/{broker}_normalization.json",
            "broker",
        ),
        ConfigSpec(
            "disabled_symbols",
            root / "outputs" / f"ubs_disabled_symbols_{account_scope}.json",
            f"outputs/ubs_disabled_symbols_{account_scope}.json",
            "account",
        ),
        ConfigSpec(
            "global_parameters",
            root / "outputs" / "ubs_global_params.json",
            "outputs/ubs_global_params.json",
            "account",
        ),
        ConfigSpec(
            "mutation_overrides",
            root / "outputs" / "ubs_mutation_overrides.json",
            "outputs/ubs_mutation_overrides.json",
            "account",
        ),
        ConfigSpec(
            "timeframe_universe",
            root / "outputs" / "ubs_timeframes.json",
            "outputs/ubs_timeframes.json",
            "broker",
        ),
    ]

    # A selected UBS .set is configuration too. Capture it when the UI points to
    # a concrete file; generated candidate/report trees remain deliberately out.
    selected_set = settings.get("Paths", "ubs_set_file", fallback="").strip()
    if selected_set:
        set_source, set_target = _resolve_config_path(
            root,
            selected_set,
            f"sets/bootstrap/{PureWindowsPath(selected_set).name}",
            manager.get("project_dir"),
        )
        specs.append(ConfigSpec("selected_ubs_set", set_source, set_target, "account"))

    if include_strategy_sets:
        specs.extend(_strategy_set_specs(root, context, manager))

    seen: set[str] = set()
    seen_paths: set[str] = set()
    result: list[ConfigSpec] = []
    for spec in specs:
        if spec.key in seen:
            raise ValueError(f"Clave de configuracion duplicada: {spec.key}")
        if spec.relative_path in seen_paths:
            continue
        seen.add(spec.key)
        seen_paths.add(spec.relative_path)
        result.append(spec)
    return result


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _sqlite_file_uri(path: Path, query: str) -> str:
    posix = path.as_posix()
    if posix.startswith("//"):
        return f"file:////{posix.lstrip('/')}?{query}"
    return f"file:{posix}?{query}"


def _strategy_set_specs(
    root: Path, context: dict[str, str], manager: dict[str, Any]
) -> list[ConfigSpec]:
    """Capture seeds and actionable .set files, never report/config output trees wholesale."""
    paths: set[Path] = set()
    broker = context["broker"]
    account = context["account_type"]
    seed_dir = root / "sets" / "ubs_ready" / broker / account
    if seed_dir.is_dir():
        paths.update(path.resolve() for path in seed_dir.rglob("*.set") if path.is_file())

    memory = root / "outputs" / f"ubs_memory_{broker}_{account}.sqlite"
    if memory.is_file():
        conn = sqlite3.connect(
            _sqlite_file_uri(memory, "mode=ro"), uri=True, timeout=30.0
        )
        try:
            conn.execute("PRAGMA query_only=ON")
            values: list[object] = []
            if _sqlite_table_exists(conn, "seed_scores"):
                values.extend(
                    row[0]
                    for row in conn.execute(
                        "SELECT seed_path FROM seed_scores WHERE coalesce(active,1)=1"
                    )
                )
            if _sqlite_table_exists(conn, "candidates"):
                placeholders = ",".join("?" for _ in RESTORABLE_CANDIDATE_STATUSES)
                values.extend(
                    row[0]
                    for row in conn.execute(
                        f"SELECT set_path FROM candidates WHERE status IN ({placeholders})",
                        tuple(sorted(RESTORABLE_CANDIDATE_STATUSES)),
                    )
                )
            for table in ("portfolio_members", "portfolio_allocations"):
                if _sqlite_table_exists(conn, table):
                    values.extend(row[0] for row in conn.execute(f"SELECT set_path FROM {table}"))
        finally:
            conn.close()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            windows_path = PureWindowsPath(text)
            windows_root = PureWindowsPath(str(manager.get("project_dir") or ""))
            if windows_path.is_absolute() and windows_root.is_absolute():
                try:
                    windows_path.relative_to(windows_root)
                except ValueError:
                    source = _map_known_project_tree(root, text)
                    if source is not None:
                        paths.add(source.resolve())
                    continue
            try:
                source, _ = _resolve_config_path(
                    root,
                    text,
                    f"sets/restored/{PureWindowsPath(text).name}",
                    manager.get("project_dir"),
                )
            except ValueError:
                source = _map_known_project_tree(root, text)
                if source is None:
                    continue
            paths.add(source.resolve())

    specs: list[ConfigSpec] = []
    for path in sorted(paths, key=lambda item: str(item).lower()):
        if path.suffix.lower() != ".set":
            continue
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            continue
        if stat.st_size > MAX_SET_BYTES:
            raise ValueError(f"Archivo .set anormalmente grande ({stat.st_size} bytes): {path}")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            # External MT5 installations are addressed by the restored terminal
            # profiles; never write an arbitrary absolute host path from a clone.
            continue
        digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()
        specs.append(
            ConfigSpec(
                f"strategy_set:{digest}",
                path,
                _safe_relative_path(relative),
                "account",
                source_mtime_ns=stat.st_mtime_ns,
                byte_size=stat.st_size,
            )
        )
    return specs


def _map_known_project_tree(root: Path, value: str) -> Path | None:
    """Map a legacy sibling-clone path to the same logical tree in this clone."""
    parts = PureWindowsPath(value).parts
    lowered = [part.lower() for part in parts]
    for anchor in ("sets", "outputs"):
        if anchor in lowered:
            index = lowered.index(anchor)
            return root.joinpath(*parts[index:])
    return None


def parse_terminal_profiles(path: Path, default_broker: str) -> list[dict[str, Any]]:
    parser = _read_ini(path)
    profiles: list[dict[str, Any]] = []
    for section in parser.sections():
        if not section.lower().startswith("terminal."):
            continue
        suffix = section.split(".", 1)[1]
        if not suffix.isdigit():
            continue
        get = parser[section].get
        profiles.append(
            {
                "profile_index": int(suffix),
                "broker": str(get("broker", default_broker)).strip().upper() or default_broker,
                "name": str(get("name", "")).strip(),
                "enabled": str(get("enabled", "true")).strip().lower() in {"1", "true", "yes", "on"},
                "mt5_path": str(get("mt5_path", "")).strip(),
                "data_dir": str(get("data_dir", "")).strip(),
                "experts_root": str(get("experts_root", "")).strip(),
                "ubs_ex5_file": str(get("ubs_ex5_file", "")).strip(),
                "portable": str(get("portable", "false")).strip().lower() in {"1", "true", "yes", "on"},
            }
        )
    return sorted(profiles, key=lambda item: item["profile_index"])


def parse_universe(path: Path) -> list[tuple[str, str, int]]:
    parser = _read_ini(path)
    rows: list[tuple[str, str, int]] = []
    for section in parser.sections():
        symbols = parser.get(section, "symbols", fallback="")
        for position, symbol in enumerate(symbols.split(","), start=1):
            value = symbol.strip()
            if value:
                rows.append((section, value, position))
    return rows


def _apply_schema_migrations(pg: Any, schema_dir: Path) -> list[int]:
    files = sorted(schema_dir.resolve().glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise ValueError(f"No hay migraciones SQL en {schema_dir}")
    exists = pg.execute("select to_regclass('public.schema_versions') is not null").fetchone()[0]
    applied = {int(row[0]) for row in pg.execute("select version from schema_versions")} if exists else set()
    installed: list[int] = []
    for path in files:
        version = int(path.name.split("_", 1)[0])
        if version in applied:
            continue
        pg.execute(path.read_text(encoding="utf-8"))
        if pg.execute("select 1 from schema_versions where version=%s", (version,)).fetchone() is None:
            raise RuntimeError(f"La migracion {path.name} no registro su version")
        installed.append(version)
    return installed


def _require_driver() -> None:
    if psycopg is None:
        raise SystemExit("Falta psycopg. Usa el servicio Docker config-sync o instala el extra [postgres].")


def _load_publish_context(root: Path, config_name: str) -> tuple[dict[str, str], dict[str, Any]]:
    config_path = Path(config_name).expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
    manager = json.loads(config_path.read_text(encoding="utf-8"))
    context = {
        "node_id": str(manager.get("node_id") or "").strip(),
        "broker": str(manager.get("broker") or "").strip().upper(),
        "account_type": str(manager.get("account_type") or "").strip().upper(),
        "display_name": str(manager.get("display_name") or "").strip(),
    }
    missing = [key for key in ("node_id", "broker", "account_type") if not context[key]]
    if missing:
        raise ValueError("Faltan campos en manager_node.json: " + ", ".join(missing))
    return context, manager


def _publish_document(
    pg: Any,
    spec: ConfigSpec,
    context: dict[str, str],
    source_project_dir: str,
    key: str,
    active: tuple[int, str, int | None, int] | None,
    next_revision: int,
) -> str:
    identity = (context["node_id"], context["broker"], context["account_type"], spec.key)
    metadata = json.dumps(
        {"source_path": str(spec.source_path), "source_project_dir": source_project_dir},
        ensure_ascii=False,
    )
    if spec.source_mtime_ns is None or spec.byte_size is None:
        stat = spec.source_path.stat()
        mtime_ns = stat.st_mtime_ns
        byte_size = stat.st_size
    else:
        mtime_ns = spec.source_mtime_ns
        byte_size = spec.byte_size
    if active and active[2] == mtime_ns and active[3] == byte_size:
        return "unchanged"
    payload = spec.source_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if active and active[1] == digest:
        pg.execute(
            """UPDATE configuration_documents SET source_mtime_ns=%s,
                      metadata_json=%s::jsonb,verified_at=now() WHERE id=%s""",
            (mtime_ns, metadata, active[0]),
        )
        return "unchanged"

    if active:
        pg.execute(
            """UPDATE configuration_documents SET active=false
               WHERE node_id=%s AND broker=%s AND account_type=%s AND config_key=%s AND active""",
            identity,
        )
    revision = next_revision
    common = (
        *identity,
        spec.scope,
        spec.relative_path,
        _media_type(spec.source_path),
        revision,
        digest,
        len(payload),
        mtime_ns,
        spec.required,
        metadata,
    )
    if spec.encrypted:
        if len(key) < 20:
            raise ValueError("CENTRAL_CONFIG_KEY debe tener al menos 20 caracteres")
        pg.execute(
            """INSERT INTO configuration_documents(
                   node_id,broker,account_type,config_key,scope,relative_path,media_type,
                   revision,plaintext_sha256,byte_size,source_mtime_ns,required,
                   metadata_json,encrypted,encrypted_content)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,true,
                      pgp_sym_encrypt_bytea(%s,%s,'cipher-algo=aes256,compress-algo=1'))""",
            (*common, payload, key),
        )
    else:
        pg.execute(
            """INSERT INTO configuration_documents(
                   node_id,broker,account_type,config_key,scope,relative_path,media_type,
                   revision,plaintext_sha256,byte_size,source_mtime_ns,required,
                   metadata_json,encrypted,content_bytes)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,false,%s)""",
            (*common, payload),
        )
    return "published"


def _publish_projections(pg: Any, manifest: Iterable[ConfigSpec], context: dict[str, str]) -> dict[str, int]:
    by_key = {spec.key: spec for spec in manifest}
    terminal_count = 0
    settings = by_key.get("ui_settings")
    if settings:
        digest = hashlib.sha256(settings.source_path.read_bytes()).hexdigest()
        profiles = parse_terminal_profiles(settings.source_path, context["broker"])
        current = tuple(pg.execute(
            """SELECT min(source_sha256),max(source_sha256),count(*)
               FROM terminal_profiles WHERE node_id=%s AND account_type=%s""",
            (context["node_id"], context["account_type"]),
        ).fetchone())
        if current != (digest, digest, len(profiles)):
            pg.execute(
                "DELETE FROM terminal_profiles WHERE node_id=%s AND account_type=%s",
                (context["node_id"], context["account_type"]),
            )
            for profile in profiles:
                pg.execute(
                    """INSERT INTO terminal_profiles(
                           node_id,broker,account_type,profile_index,name,enabled,mt5_path,
                           data_dir,experts_root,ubs_ex5_file,portable,source_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        context["node_id"], profile["broker"], context["account_type"],
                        profile["profile_index"], profile["name"], profile["enabled"],
                        profile["mt5_path"], profile["data_dir"], profile["experts_root"],
                        profile["ubs_ex5_file"], profile["portable"], digest,
                    ),
                )
        terminal_count = len(profiles)

    universe_count = 0
    universe = by_key.get("asset_universe")
    if universe:
        digest = hashlib.sha256(universe.source_path.read_bytes()).hexdigest()
        rows = parse_universe(universe.source_path)
        current = tuple(pg.execute(
            """SELECT min(source_sha256),max(source_sha256),count(*)
               FROM broker_universe_symbols
               WHERE node_id=%s AND broker=%s AND account_type=%s""",
            (context["node_id"], context["broker"], context["account_type"]),
        ).fetchone())
        if current != (digest, digest, len(rows)):
            pg.execute(
                "DELETE FROM broker_universe_symbols WHERE node_id=%s AND broker=%s AND account_type=%s",
                (context["node_id"], context["broker"], context["account_type"]),
            )
            for asset_group, symbol, position in rows:
                pg.execute(
                    """INSERT INTO broker_universe_symbols(
                           node_id,broker,account_type,asset_group,symbol,position,source_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        context["node_id"], context["broker"], context["account_type"],
                        asset_group, symbol, position, digest,
                    ),
                )
        universe_count = len(rows)
    return {"terminal_profiles": terminal_count, "universe_symbols": universe_count}


def publish(args: argparse.Namespace) -> int:
    _require_driver()
    root = Path(args.root).expanduser().resolve()
    context, manager = _load_publish_context(root, args.config)
    manifest = build_manifest(
        root,
        context,
        manager,
        include_strategy_sets=not args.configuration_only,
    )
    missing_required = [spec.relative_path for spec in manifest if spec.required and not spec.source_path.is_file()]
    if missing_required:
        raise FileNotFoundError("Faltan configuraciones obligatorias: " + ", ".join(missing_required))
    existing = [
        spec
        for spec in manifest
        if spec.byte_size is not None or spec.source_path.is_file()
    ]

    with psycopg.connect(args.dsn) as pg:
        pg.autocommit = True
        migrations = _apply_schema_migrations(pg, Path(args.schema_dir))
        pg.autocommit = False
        with pg.transaction():
            pg.execute(
                """INSERT INTO nodes(node_id,broker,account_type,display_name,source_kind)
                   VALUES(%s,%s,%s,%s,'configuration_publish')
                   ON CONFLICT(node_id) DO UPDATE SET broker=excluded.broker,
                     account_type=excluded.account_type,display_name=excluded.display_name""",
                (context["node_id"], context["broker"], context["account_type"], context["display_name"]),
            )
            source_project_dir = str(manager.get("project_dir") or root)
            active_rows = pg.execute(
                """SELECT config_key,id,plaintext_sha256,revision,source_mtime_ns,byte_size
                   FROM configuration_documents
                   WHERE node_id=%s AND broker=%s AND account_type=%s AND active""",
                (context["node_id"], context["broker"], context["account_type"]),
            ).fetchall()
            active_by_key = {
                str(row[0]): (
                    int(row[1]), str(row[2]),
                    int(row[4]) if row[4] is not None else None, int(row[5]),
                )
                for row in active_rows
            }
            revision_by_key = {
                str(row[0]): int(row[1])
                for row in pg.execute(
                    """SELECT config_key,max(revision) FROM configuration_documents
                       WHERE node_id=%s AND broker=%s AND account_type=%s
                       GROUP BY config_key""",
                    (context["node_id"], context["broker"], context["account_type"]),
                )
            }
            expected_keys = {spec.key for spec in existing}
            for stale_key, active_document in active_by_key.items():
                if args.configuration_only and stale_key.startswith("strategy_set:"):
                    continue
                if stale_key not in expected_keys:
                    pg.execute(
                        "UPDATE configuration_documents SET active=false WHERE id=%s",
                        (active_document[0],),
                    )
            states: dict[str, int] = {}
            # Thousands of small .set documents otherwise incur one network
            # round trip each between the tool container and PostgreSQL.
            with pg.pipeline():
                for spec in existing:
                    state = _publish_document(
                        pg,
                        spec,
                        context,
                        source_project_dir,
                        args.config_key,
                        (
                            None
                            if args.rotate_secrets and spec.encrypted
                            else active_by_key.get(spec.key)
                        ),
                        revision_by_key.get(spec.key, 0) + 1,
                    )
                    states[state] = states.get(state, 0) + 1
            pg.execute(
                """UPDATE configuration_documents SET verified_at=now()
                   WHERE node_id=%s AND broker=%s AND account_type=%s AND active""",
                (context["node_id"], context["broker"], context["account_type"]),
            )
            projections = _publish_projections(pg, existing, context)
    strategy_sets = sum(spec.key.startswith("strategy_set:") for spec in existing)
    print(json.dumps({
        "node": context,
        "migrations": migrations,
        "documents": states,
        "strategy_sets": strategy_sets,
        **projections,
    }, indent=2))
    return 0


def _rewrite_for_clone(
    config_key: str,
    payload: bytes,
    source_root: str,
    target_project_dir: str,
    new_node_id: str,
    host: str | None,
    port: int | None,
) -> bytes:
    if config_key == "manager_node":
        data = json.loads(payload.decode("utf-8-sig"))
        data["project_dir"] = target_project_dir
        if new_node_id:
            data["node_id"] = new_node_id
        if host is not None:
            data["host"] = host
        if port is not None:
            data["port"] = port
        return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if config_key == "ui_settings" and source_root:
        text = payload.decode("utf-8-sig")
        replacements = {
            source_root: target_project_dir,
            source_root.replace("\\", "/"): target_project_dir.replace("\\", "/"),
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.encode("utf-8")
    return payload


def restore(args: argparse.Namespace) -> int:
    _require_driver()
    root = Path(args.root).expanduser().resolve()
    source_node_id = args.source_node_id
    if not source_node_id and (root / args.config).is_file():
        source_node_id = json.loads((root / args.config).read_text(encoding="utf-8"))["node_id"]
    if not source_node_id:
        raise ValueError("Indica --source-node-id en un clon que aun no tenga manager_node.json")

    with psycopg.connect(args.dsn) as pg:
        node = pg.execute(
            "SELECT node_id,broker,account_type FROM nodes WHERE node_id=%s", (source_node_id,)
        ).fetchone()
        if node is None:
            raise KeyError(f"No existe el nodo de configuracion {source_node_id!r}")
        broker = str(args.broker or node[1]).upper()
        account_type = str(args.account_type or node[2]).upper()
        rows = pg.execute(
            """SELECT config_key,relative_path,plaintext_sha256,encrypted,
                      content_bytes,encrypted_content,metadata_json
               FROM configuration_documents
               WHERE node_id=%s AND broker=%s AND account_type=%s AND active
                 AND (NOT %s OR config_key NOT LIKE 'strategy_set:%%')
               ORDER BY config_key""",
            (source_node_id, broker, account_type, args.configuration_only),
        ).fetchall()
        if not rows:
            raise KeyError(f"No hay configuracion activa para {source_node_id}/{broker}/{account_type}")

        restored: list[str] = []
        target_project_dir = args.target_project_dir or str(root)
        for config_key, relative_path, digest, encrypted, content, encrypted_content, metadata in rows:
            if encrypted:
                if len(args.config_key) < 20:
                    raise ValueError("CENTRAL_CONFIG_KEY es obligatoria para restaurar secretos")
                content = pg.execute(
                    "SELECT pgp_sym_decrypt_bytea(%s,%s)", (encrypted_content, args.config_key)
                ).fetchone()[0]
            payload = bytes(content)
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError(f"Hash incorrecto al restaurar {config_key}")
            metadata = metadata if isinstance(metadata, dict) else json.loads(metadata or "{}")
            payload = _rewrite_for_clone(
                str(config_key), payload, str(metadata.get("source_project_dir") or ""),
                target_project_dir, args.new_node_id, args.host, args.port,
            )
            target = root.joinpath(*PurePosixPath(_safe_relative_path(relative_path)).parts)
            resolved_target = target.resolve()
            try:
                resolved_target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"La ruta restaurada escapa del clon: {target}") from exc
            if target.exists() and not args.force:
                raise FileExistsError(f"No se sobrescribe sin --force: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            restored.append(str(target.relative_to(root)))
    print(json.dumps({
        "source_node": source_node_id,
        "broker": broker,
        "account_type": account_type,
        "restored_count": len(restored),
        "restored_configuration": [
            value for value in restored if not value.lower().endswith(".set")
        ],
        "restored_strategy_sets": sum(value.lower().endswith(".set") for value in restored),
    }, indent=2))
    return 0


def list_documents(args: argparse.Namespace) -> int:
    _require_driver()
    with psycopg.connect(args.dsn) as pg:
        rows = pg.execute(
            """SELECT node_id,broker,account_type,scope,config_key,relative_path,
                      revision,byte_size,encrypted,verified_at
               FROM configuration_documents WHERE active
               ORDER BY node_id,broker,account_type,config_key"""
        ).fetchall()
    print(json.dumps([
        {
            "node_id": row[0], "broker": row[1], "account_type": row[2],
            "scope": row[3], "config_key": row[4], "relative_path": row[5],
            "revision": row[6], "byte_size": row[7], "encrypted": row[8],
            "verified_at": row[9].isoformat(),
        }
        for row in rows
    ], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publica/restaura configuracion completa de un nodo en PostgreSQL central."
    )
    parser.add_argument("--dsn", default=os.getenv("CENTRAL_DATABASE_URL", ""))
    parser.add_argument("--config-key", default=os.getenv("CENTRAL_CONFIG_KEY", ""))
    parser.add_argument("--schema-dir", default=str(SCHEMA_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser("publish", help="Publicar configuracion local")
    publish_parser.add_argument("--root", default=".")
    publish_parser.add_argument("--config", default="manager_node.json")
    publish_parser.add_argument(
        "--configuration-only",
        action="store_true",
        help="Sincroniza solo configuracion; conserva el ultimo snapshot de .set activo.",
    )
    publish_parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Crea una revision cifrada nueva de .env y manager_node.json con la clave actual.",
    )
    publish_parser.set_defaults(handler=publish)

    restore_parser = subparsers.add_parser("restore", help="Restaurar un clon desde PostgreSQL")
    restore_parser.add_argument("--root", default=".")
    restore_parser.add_argument("--config", default="manager_node.json")
    restore_parser.add_argument("--source-node-id")
    restore_parser.add_argument("--broker")
    restore_parser.add_argument("--account-type")
    restore_parser.add_argument("--new-node-id", default="")
    restore_parser.add_argument(
        "--target-project-dir",
        help="Ruta del clon vista por Windows; necesaria cuando Docker escribe mediante /app.",
    )
    restore_parser.add_argument("--host")
    restore_parser.add_argument("--port", type=int)
    restore_parser.add_argument("--force", action="store_true")
    restore_parser.add_argument(
        "--configuration-only",
        action="store_true",
        help="Restaura solo configuracion y omite el snapshot de .set.",
    )
    restore_parser.set_defaults(handler=restore)

    list_parser = subparsers.add_parser("list", help="Listar configuraciones activas sin mostrar contenido")
    list_parser.set_defaults(handler=list_documents)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.dsn:
        raise SystemExit("Falta CENTRAL_DATABASE_URL o --dsn")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
