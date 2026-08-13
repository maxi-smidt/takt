from __future__ import annotations

from collections.abc import Callable


class MockButtonInput:
    """Laptop-friendly replacement for the GPIO mushroom button."""

    available = True

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release or (lambda: None)

    def press(self) -> None:
        self._on_press()
        self._on_release()

    def close(self) -> None:
        pass
