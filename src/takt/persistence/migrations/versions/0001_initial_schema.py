"""Initial run database schema

Revision ID: 0001
Revises:
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schema_version",
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO schema_version(version) VALUES (1)")

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("stopped_at", sa.Text(), nullable=False),
        sa.Column("saved_at", sa.Text(), nullable=False),
        sa.Column("actual_time_ms", sa.Integer(), nullable=False),
        sa.Column("added_time_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_time_ms", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("actual_time_ms >= 0", name="ck_runs_actual_time_ms"),
        sa.CheckConstraint("added_time_ms >= 0", name="ck_runs_added_time_ms"),
        sa.CheckConstraint(
            "total_time_ms >= 0 AND total_time_ms = actual_time_ms + added_time_ms",
            name="ck_runs_total_time_ms",
        ),
        sa.UniqueConstraint("session_date", "run_number", name="uq_runs_session_date_run_number"),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_runs_session_date", "runs", ["session_date"])
    op.create_index(
        "idx_runs_total_time", "runs", ["total_time_ms", "actual_time_ms", "saved_at"]
    )

    op.create_table(
        "remote_command_receipts",
        sa.Column("command_id", sa.Text(), primary_key=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("remote_command_receipts")
    op.drop_index("idx_runs_total_time", table_name="runs")
    op.drop_index("idx_runs_session_date", table_name="runs")
    op.drop_table("runs")
    op.drop_table("schema_version")
