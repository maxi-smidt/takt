"""One-click Raspberry Pi fleet deployment (hand-rolled schema version 7)

Adds `deployments`, `deployment_events`, and `trusted_ssh_hosts`, and links
enrollment codes to the deployment that issued them. `deployments` is
created in its version-7 shape here; `hostname_change_confirmed` was added
later in revision 0006.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from takt.registry.migrations._helpers import add_column_if_missing, create_table_if_missing
from takt.registry.models import deployment_events, trusted_ssh_hosts

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing("enrollment_codes", sa.Column("deployment_id", sa.Text()))
    create_table_if_missing(
        sa.Table(
            "deployments",
            sa.MetaData(),
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("target", sa.Text(), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("ssh_user", sa.Text(), nullable=False),
            sa.Column("device_name", sa.Text(), nullable=False),
            sa.Column("requested_hostname", sa.Text(), nullable=False),
            sa.Column("registry_url", sa.Text(), nullable=False),
            sa.Column(
                "allow_insecure_http", sa.Integer(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("release_id", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("stage", sa.Text(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("host_key", sa.Text()),
            sa.Column("host_key_fingerprint", sa.Text()),
            sa.Column("device_id", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.Text()),
        )
    )
    op.create_index(
        "idx_deployments_target_status",
        "deployments",
        ["target", "port", "status", "created_at"],
        if_not_exists=True,
    )
    # `deployment_events` and `trusted_ssh_hosts` reuse their final models
    # verbatim: neither table's shape changed after introduction.
    # `Table.create()` also creates `idx_deployment_events_deployment`, the
    # index attached to `deployment_events` in `models.py`.
    create_table_if_missing(deployment_events)
    create_table_if_missing(trusted_ssh_hosts)


def downgrade() -> None:
    op.drop_table("trusted_ssh_hosts")
    op.drop_table("deployment_events")
    op.drop_index("idx_deployments_target_status", table_name="deployments")
    op.drop_table("deployments")
    op.drop_column("enrollment_codes", "deployment_id")
