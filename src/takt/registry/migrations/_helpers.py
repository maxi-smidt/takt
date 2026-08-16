"""Idempotent DDL helpers shared by the Fleet Registry's Alembic revisions.

The Registry database has been upgraded in place since before Alembic was
adopted: every historical revision here must tolerate being run against a
database that already has some (or all) of its target state, exactly like
the hand-rolled `CREATE TABLE IF NOT EXISTS` / `_ensure_column` checks it
replaces. These helpers keep that guard logic in one place instead of
repeating an inspector check in every revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


def create_table_if_missing(table: sa.Table) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table.name not in inspector.get_table_names():
        table.create(bind)


def add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {row["name"] for row in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)
