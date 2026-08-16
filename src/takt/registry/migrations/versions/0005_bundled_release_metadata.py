"""Bundle and auto-import the matching Pi release (hand-rolled schema version 9)

Tracks where a release came from (`upload` vs `bundled`) and, for bundled
releases, the source commit they were built from.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing(
        "releases",
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'upload'")),
    )
    add_column_if_missing("releases", sa.Column("commit_sha", sa.Text()))


def downgrade() -> None:
    op.drop_column("releases", "commit_sha")
    op.drop_column("releases", "source")
