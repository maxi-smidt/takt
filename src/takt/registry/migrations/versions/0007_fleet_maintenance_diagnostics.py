"""Add capability-driven Fleet maintenance (hand-rolled schema version 11)

Adds the `diagnostics` bundle table and the `devices.health_checks_json`
column used by the Fleet Manager's maintenance actions. On the hand-rolled
schema this landed a commit before the `SCHEMA_VERSION` bump to 11 itself
(which shipped no further schema change of its own), so this revision
carries the version-11 schema delta in full.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing, create_table_if_missing
from takt.registry.models import diagnostics

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `diagnostics` reuses the final model verbatim: its shape never changed
    # after introduction. `Table.create()` also creates
    # `idx_diagnostics_device_created`, the index attached to it in
    # `models.py`.
    create_table_if_missing(diagnostics)
    add_column_if_missing(
        "devices",
        sa.Column("health_checks_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("devices", "health_checks_json")
    op.drop_table("diagnostics")
