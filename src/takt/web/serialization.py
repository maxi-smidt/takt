from __future__ import annotations

from datetime import date

from takt.application.timer_controller import TimerSnapshot
from takt.domain.run import Run
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository

STATE_LABELS = {
    TimerState.READY: "BEREIT",
    TimerState.RUNNING: "LÄUFT",
    TimerState.STOPPED: "GESTOPPT",
    TimerState.SAVED_CONFIRMATION: "ZEIT GESPEICHERT!",
    TimerState.DISCARD_CONFIRMATION: "DIESEN LAUF VERWERFEN?",
    TimerState.ERROR: "FEHLER",
}


def serialize_run(run: Run) -> dict[str, object]:
    local_started = run.started_at.astimezone()
    return {
        "id": run.id,
        "number": run.run_number,
        "date": local_started.strftime("%d.%m.%Y"),
        "date_short": local_started.strftime("%d.%m."),
        "time": local_started.strftime("%H:%M:%S"),
        "timestamp": run.started_at.isoformat(),
        "actual_ms": run.actual_time.milliseconds,
        "actual": run.actual_time.format_stopwatch(),
        "added_ms": run.added_time.milliseconds,
        "added": run.added_time.format_added(),
        "total_ms": run.total_time.milliseconds,
        "total": run.total_time.format_stopwatch(),
    }


def serialize_snapshot(
    snapshot: TimerSnapshot,
    *,
    hardware_label: str,
    hardware_available: bool,
    history_revision: int,
    signal_revision: int,
    last_signal: str | None,
) -> dict[str, object]:
    actual = snapshot.actual_time
    added = snapshot.added_time
    total = snapshot.total_time
    if snapshot.state is TimerState.SAVED_CONFIRMATION and snapshot.last_saved_run:
        actual = snapshot.last_saved_run.actual_time
        added = snapshot.last_saved_run.added_time
        total = snapshot.last_saved_run.total_time
    return {
        "state": snapshot.state.value,
        "state_label": STATE_LABELS[snapshot.state],
        "actual_ms": actual.milliseconds,
        "actual": actual.format_stopwatch(),
        "added_ms": added.milliseconds,
        "added": added.format_added(),
        "total_ms": total.milliseconds,
        "total": total.format_stopwatch(),
        "error": snapshot.error_message,
        "hardware": {
            "label": hardware_label,
            "available": hardware_available,
        },
        "history_revision": history_revision,
        "signal_revision": signal_revision,
        "signal": last_signal,
    }


def serialize_history(
    repository: SQLiteRunRepository,
    *,
    chart_days: int | None,
    best_limit: int,
) -> dict[str, object]:
    today = repository.get_runs_for_date(date.today())
    best = repository.get_best_runs(best_limit)
    chart = repository.get_recent_runs(chart_days)
    all_runs = repository.get_all_runs()
    return {
        "today": [serialize_run(run) for run in today],
        "today_count": len(today),
        "best": [{**serialize_run(run), "rank": rank} for rank, run in enumerate(best, start=1)],
        "chart": [serialize_run(run) for run in chart],
        "all": [serialize_run(run) for run in all_runs],
        "chart_days": chart_days,
    }
