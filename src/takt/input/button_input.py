from __future__ import annotations

from typing import Protocol


class ButtonInput(Protocol):
    @property
    def available(self) -> bool: ...

    def close(self) -> None: ...

