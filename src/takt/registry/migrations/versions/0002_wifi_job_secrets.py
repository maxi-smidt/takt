"""Fleet-managed Wi-Fi profiles (hand-rolled schema version 6)

Adds the `job_secrets` table used to hold an encrypted Wi-Fi password
alongside its `add_wifi_network` job until the job completes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from takt.registry.migrations._helpers import create_table_if_missing
from takt.registry.models import job_secrets

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `job_secrets` reuses the final model verbatim: its shape never changed
    # after introduction.
    create_table_if_missing(job_secrets)


def downgrade() -> None:
    op.drop_table("job_secrets")
