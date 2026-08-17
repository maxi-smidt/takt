"""Let the Fleet manager acknowledge stuck update recovery (hand-rolled schema version 13)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVICES_COLUMNS = ["recovery_raised_at", "recovery_ack_at", "recovery_ack_by"]


def upgrade() -> None:
    for name in _DEVICES_COLUMNS:
        add_column_if_missing("devices", sa.Column(name, sa.Text()))


def downgrade() -> None:
    for name in reversed(_DEVICES_COLUMNS):
        op.drop_column("devices", name)
