"""SQLAlchemy Core table definitions for the Fleet Registry database.

This is the source of truth for the schema; `migrations/versions/` applies
it (and its full history) as Alembic revisions. Application code still
talks to the database through raw `sqlite3` (see `storage.py` and
`accounts.py`) so these are plain Core `Table` objects rather than an ORM
mapping.

The `idx_jobs_one_active_disruptive_operation` partial unique index on
`jobs` is intentionally NOT modeled here: its predicate is derived from
`takt.fleet_actions.DISRUPTIVE_ACTIONS` and is rebuilt on every Registry
startup (see `RegistryStore._rebuild_disruptive_index`), independent of the
schema version.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()

devices = Table(
    "devices",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("hostname", Text, nullable=False),
    Column("token_hash", Text, nullable=False),
    Column("enrolled_at", Text, nullable=False),
    Column("last_seen_at", Text),
    Column("app_version", Text),
    Column("agent_version", Text),
    Column("status_json", Text, nullable=False, server_default=text("'{}'")),
    Column("last_mirror_at", Text),
    Column("mirror_sha256", Text),
    Column("mirror_size", Integer),
    Column("run_count", Integer),
    Column("revoked_at", Text),
    Column("health_checks_json", Text, nullable=False, server_default=text("'{}'")),
    Column("recovery_raised_at", Text),
    Column("recovery_ack_at", Text),
    Column("recovery_ack_by", Text),
)

enrollment_codes = Table(
    "enrollment_codes",
    metadata,
    Column("code_hash", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("used_at", Text),
    Column("label", Text, nullable=False, server_default=text("''")),
    Column("deployment_id", Text),
)

releases = Table(
    "releases",
    metadata,
    Column("id", Text, primary_key=True),
    Column("version", Text, nullable=False, unique=True),
    Column("filename", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("source", Text, nullable=False, server_default=text("'upload'")),
    Column("commit_sha", Text),
)

deployments = Table(
    "deployments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("target", Text, nullable=False),
    Column("port", Integer, nullable=False),
    Column("ssh_user", Text, nullable=False),
    Column("device_name", Text, nullable=False),
    Column("requested_hostname", Text, nullable=False),
    Column("hostname_change_confirmed", Integer, nullable=False, server_default=text("0")),
    Column("registry_url", Text, nullable=False),
    Column("allow_insecure_http", Integer, nullable=False, server_default=text("0")),
    Column("release_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("message", Text, nullable=False, server_default=text("''")),
    Column("host_key", Text),
    Column("host_key_fingerprint", Text),
    Column("device_id", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("completed_at", Text),
)

Index(
    "idx_deployments_target_status",
    deployments.c.target,
    deployments.c.port,
    deployments.c.status,
    deployments.c.created_at,
)

deployment_events = Table(
    "deployment_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "deployment_id",
        Text,
        ForeignKey("deployments.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("message", Text, nullable=False),
    sqlite_autoincrement=True,
)

Index(
    "idx_deployment_events_deployment", deployment_events.c.deployment_id, deployment_events.c.id
)

trusted_ssh_hosts = Table(
    "trusted_ssh_hosts",
    metadata,
    Column("target_key", Text, primary_key=True),
    Column("host_key", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("trusted_at", Text, nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("device_id", Text, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    Column("action", Text, nullable=False),
    Column("payload_json", Text, nullable=False, server_default=text("'{}'")),
    Column("status", Text, nullable=False),
    Column("progress", Integer, nullable=False, server_default=text("0")),
    Column("message", Text, nullable=False, server_default=text("''")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("claimed_at", Text),
    Column("completed_at", Text),
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("lease_id", Text),
    Column("lease_expires_at", Text),
    Column("lease_owner_session", Text),
    Column("stage", Text, nullable=False, server_default=text("'queued'")),
    Column("current_version", Text),
    Column("target_version", Text),
    Column("bytes_downloaded", Integer),
    Column("bytes_total", Integer),
    Column("cancel_requested", Integer, nullable=False, server_default=text("0")),
    # `retry_of` carries a self-referential FK here (matching a fresh
    # `CREATE TABLE`), but the migration that introduces it on an upgraded
    # database (0004) adds it as a plain column without one, replicating the
    # original hand-rolled `_ensure_column("jobs", "retry_of", "TEXT")` call
    # exactly. `alembic revision --autogenerate` will always propose adding
    # this foreign key back; that diff is expected and should not be applied.
    Column("retry_of", Text, ForeignKey("jobs.id")),
    Column("requested_by_user_id", Text),
    Column("result_json", Text),
)

Index("idx_jobs_device_status", jobs.c.device_id, jobs.c.status, jobs.c.created_at)

job_events = Table(
    "job_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Text, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("message", Text, nullable=False),
    sqlite_autoincrement=True,
)

Index("idx_job_events_job", job_events.c.job_id, job_events.c.id)

job_secrets = Table(
    "job_secrets",
    metadata,
    Column("job_id", Text, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("nonce", LargeBinary, nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("device_id", Text),
    Column("details_json", Text, nullable=False, server_default=text("'{}'")),
    Column("actor_user_id", Text),
    Column("target_user_id", Text),
    sqlite_autoincrement=True,
)

mirror_snapshots = Table(
    "mirror_snapshots",
    metadata,
    Column("id", Text, primary_key=True),
    Column("device_id", Text, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    Column("received_at", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("run_count", Integer, nullable=False),
    Column("relative_path", Text, nullable=False, unique=True),
    UniqueConstraint("device_id", "sha256", name="uq_mirror_snapshots_device_sha256"),
)

Index(
    "idx_mirror_snapshots_device_received",
    mirror_snapshots.c.device_id,
    mirror_snapshots.c.received_at.desc(),
)

diagnostics = Table(
    "diagnostics",
    metadata,
    Column("id", Text, primary_key=True),
    Column("device_id", Text, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    Column("job_id", Text, ForeignKey("jobs.id", ondelete="SET NULL")),
    Column("created_at", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("relative_path", Text, nullable=False, unique=True),
)

Index("idx_diagnostics_device_created", diagnostics.c.device_id, diagnostics.c.created_at.desc())

users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("username_key", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("is_admin", Integer, nullable=False, server_default=text("0")),
    Column("disabled_at", Text),
    Column("must_change_password", Integer, nullable=False, server_default=text("0")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("password_changed_at", Text, nullable=False),
)

user_sessions = Table(
    "user_sessions",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("revoked_at", Text),
)

Index("idx_user_sessions_user", user_sessions.c.user_id, user_sessions.c.expires_at)

device_access = Table(
    "device_access",
    metadata,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("device_id", Text, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
    Column("access_level", Text, nullable=False),
    Column("granted_at", Text, nullable=False),
    Column("granted_by", Text),
    CheckConstraint("access_level IN ('read', 'write')", name="ck_device_access_access_level"),
)

Index("idx_device_access_device", device_access.c.device_id, device_access.c.user_id)
