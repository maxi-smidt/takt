"""Preserve Raspberry Pi hostnames by default (hand-rolled schema version 10)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing(
        "deployments",
        sa.Column(
            "hostname_change_confirmed", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("deployments", "hostname_change_confirmed")
