"""Shared helpers for running Alembic migrations against an embedded SQLite file.

Both `takt.persistence` (the Pi run database) and `takt.registry` (the Fleet
Registry database) manage their own Alembic environment under a
`migrations/` package, but need the same small amount of glue to build an
`alembic.config.Config` pointed at a runtime-chosen database file and drive
it programmatically instead of via the `alembic` CLI.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(migrations_directory: Path, database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_directory))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def upgrade_to_head(migrations_directory: Path, database_path: Path) -> None:
    command.upgrade(alembic_config(migrations_directory, database_path), "head")


def stamp(migrations_directory: Path, database_path: Path, revision: str) -> None:
    command.stamp(alembic_config(migrations_directory, database_path), revision)
