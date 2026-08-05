from enum import Enum


class TimerState(Enum):
    READY = "ready"
    RUNNING = "running"
    STOPPED = "stopped"
    SAVED_CONFIRMATION = "saved_confirmation"
    DISCARD_CONFIRMATION = "discard_confirmation"
    ERROR = "error"

