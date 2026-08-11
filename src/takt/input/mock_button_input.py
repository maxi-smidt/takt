from __future__ import annotations

from collections.abc import Callable


class MockButtonInput:
    """Laptop-friendly replacement for the GPIO mushroom button."""

    available = True

    def __init__(self, on_press: Callable[[], None]) -> None:
        self._on_press = on_press

    def press(self) -> None:
        self._on_press()

    def close(self) -> None:
        pass
