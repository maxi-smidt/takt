from __future__ import annotations

from pathlib import Path


def require_static_assets(root: Path, entrypoint: str, build_command: str) -> None:
    """Fail early with an actionable message when a built UI is unavailable."""

    entrypoint_path = root / entrypoint
    assets_path = root / "assets"
    if (
        not entrypoint_path.is_file()
        or not assets_path.is_dir()
        or not any(path.is_file() for path in assets_path.iterdir())
    ):
        raise RuntimeError(
            f"Built frontend assets are missing at {root}. "
            f"Run {build_command} before starting the server."
        )
