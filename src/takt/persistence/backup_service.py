from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from takt.persistence.run_repository import SQLiteRunRepository

LOGGER = logging.getLogger(__name__)


def create_daily_backup(
    repository: SQLiteRunRepository,
    backup_directory: Path,
    retention_days: int,
) -> Path:
    target = backup_directory / f"takt-{date.today().isoformat()}.db"
    if not target.exists():
        repository.backup_to(target)
        LOGGER.info("daily_backup_created path=%s", target)
    backups = sorted(backup_directory.glob("takt-*.db"), reverse=True)
    for stale in backups[retention_days:]:
        stale.unlink(missing_ok=True)
        LOGGER.info("old_backup_removed path=%s", stale)
    return target
