"""Add multi-user Registry portal (hand-rolled schema version 12)

Adds Registry accounts (`users`, `user_sessions`, `device_access`), audit
attribution columns on `audit_events`, and records which portal user
requested a job.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing, create_table_if_missing
from takt.registry.models import device_access, user_sessions, users

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing("jobs", sa.Column("requested_by_user_id", sa.Text()))
    add_column_if_missing("jobs", sa.Column("result_json", sa.Text()))
    add_column_if_missing("audit_events", sa.Column("actor_user_id", sa.Text()))
    add_column_if_missing("audit_events", sa.Column("target_user_id", sa.Text()))
    create_table_if_missing(users)
    # `user_sessions` and `device_access` reuse their final models verbatim.
    # `Table.create()` also creates the `idx_user_sessions_user` and
    # `idx_device_access_device` indexes attached to them in `models.py`.
    create_table_if_missing(user_sessions)
    create_table_if_missing(device_access)


def downgrade() -> None:
    op.drop_table("device_access")
    op.drop_table("user_sessions")
    op.drop_table("users")
    op.drop_column("audit_events", "target_user_id")
    op.drop_column("audit_events", "actor_user_id")
    op.drop_column("jobs", "result_json")
    op.drop_column("jobs", "requested_by_user_id")
