"""Baseline schema (hand-rolled schema version 5)

This is the oldest registry schema reconstructable from source history: the
squashed initial commit already carried ``SCHEMA_VERSION = 5``, so versions
1-4 predate this repository's history and cannot be replayed. Every table
and index here matches the ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX
IF NOT EXISTS`` statements that were already unconditionally present at that
point, and every step is guarded so this revision is a no-op against a
database that already has these objects (e.g. a real pre-Alembic Registry
database being adopted into Alembic for the first time).

Revision ID: 0001
Revises:
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import create_table_if_missing
from takt.registry.models import mirror_snapshots

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # All ad-hoc tables below share one `MetaData` so that `jobs.device_id`'s
    # foreign key can resolve `devices` by name at DDL-compile time.
    metadata = sa.MetaData()
    devices = sa.Table(
        "devices",
        metadata,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("enrolled_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text()),
        sa.Column("app_version", sa.Text()),
        sa.Column("agent_version", sa.Text()),
        sa.Column("status_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_mirror_at", sa.Text()),
        sa.Column("mirror_sha256", sa.Text()),
        sa.Column("mirror_size", sa.Integer()),
        sa.Column("run_count", sa.Integer()),
        sa.Column("revoked_at", sa.Text()),
    )
    create_table_if_missing(devices)
    create_table_if_missing(
        sa.Table(
            "enrollment_codes",
            metadata,
            sa.Column("code_hash", sa.Text(), primary_key=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("used_at", sa.Text()),
            sa.Column("label", sa.Text(), nullable=False, server_default=sa.text("''")),
        )
    )
    create_table_if_missing(
        sa.Table(
            "releases",
            metadata,
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("version", sa.Text(), nullable=False, unique=True),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
    )
    create_table_if_missing(
        sa.Table(
            "jobs",
            metadata,
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "device_id",
                sa.Text(),
                sa.ForeignKey("devices.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("action", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("claimed_at", sa.Text()),
            sa.Column("completed_at", sa.Text()),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("lease_id", sa.Text()),
            sa.Column("lease_expires_at", sa.Text()),
            sa.Column("lease_owner_session", sa.Text()),
        )
    )
    op.create_index(
        "idx_jobs_device_status", "jobs", ["device_id", "status", "created_at"], if_not_exists=True
    )
    create_table_if_missing(
        sa.Table(
            "audit_events",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("event", sa.Text(), nullable=False),
            sa.Column("device_id", sa.Text()),
            sa.Column("details_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
            sqlite_autoincrement=True,
        )
    )
    # `mirror_snapshots` reuses the final model verbatim: its shape never
    # changed after introduction. `Table.create()` also creates the
    # `idx_mirror_snapshots_device_received` index attached to it in
    # `models.py`, so no separate `create_index` call is needed here.
    create_table_if_missing(mirror_snapshots)


def downgrade() -> None:
    op.drop_table("mirror_snapshots")
    op.drop_table("audit_events")
    op.drop_index("idx_jobs_device_status", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("releases")
    op.drop_table("enrollment_codes")
    op.drop_table("devices")
