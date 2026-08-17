"""Make Fleet installs durable (hand-rolled schema version 8)

Adds job staging/lease-progress columns to `jobs` and the `job_events`
audit trail table.

The `idx_jobs_one_active_disruptive_operation` partial unique index that
was also introduced at this version is intentionally NOT created here: its
predicate is derived from `takt.fleet_actions.DISRUPTIVE_ACTIONS`, a Python
constant that can gain new disruptive actions independently of a schema
version bump, so `RegistryStore._rebuild_disruptive_index` recreates it
(and keeps it in sync) on every Registry startup instead.

Note: `jobs.retry_of` is added here as a plain column, without the
`REFERENCES jobs(id)` that a fresh `CREATE TABLE` would give it. That
matches the original `_ensure_column("jobs", "retry_of", "TEXT")` call this
revision replaces exactly -- on the hand-rolled schema, a database upgraded
through this step never got that foreign key either.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing, create_table_if_missing
from takt.registry.models import job_events

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOBS_COLUMNS: list[sa.Column[Any]] = [
    sa.Column("stage", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
    sa.Column("current_version", sa.Text()),
    sa.Column("target_version", sa.Text()),
    sa.Column("bytes_downloaded", sa.Integer()),
    sa.Column("bytes_total", sa.Integer()),
    sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("retry_of", sa.Text()),
]


def upgrade() -> None:
    for column in _JOBS_COLUMNS:
        add_column_if_missing("jobs", column)
    # `job_events` reuses the final model verbatim: its shape never changed
    # after introduction. `Table.create()` also creates `idx_job_events_job`,
    # the index attached to it in `models.py`.
    create_table_if_missing(job_events)


def downgrade() -> None:
    op.drop_table("job_events")
    for column in reversed(_JOBS_COLUMNS):
        op.drop_column("jobs", column.name)
