from __future__ import annotations


class Audit:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(conn, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"pragma table_info({table})")}


def scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def format_count_map(rows) -> str:
    if not rows:
        return "-"
    return ", ".join(f"{row['status']}={row['n']}" for row in rows)


def print_heading(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))
