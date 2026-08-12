from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from takt.application.audio_service import AudioService
from takt.application.run_curation_service import RunCurationService
from takt.application.system_power_service import SystemPowerService
from takt.application.timer_controller import TimerController, TimerSnapshot
from takt.buzzer import Buzzer
from takt.config import Config
from takt.domain.duration import Duration
from takt.domain.timer_state import TimerState
from takt.persistence.run_repository import SQLiteRunRepository
from takt.web.serialization import serialize_history, serialize_snapshot

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingConfirmation:
    operation: str
    run_id: int | None
    delta_ms: int
    expires_at: float


@dataclass(slots=True)
class MaintenanceLease:
    token: str
    request_id: str
    owner: str
    reason: str
    acquired_at: float
    expires_at: float


class MaintenanceUnavailable(RuntimeError):
    """Raised when TAKT cannot safely enter maintenance mode."""


class MaintenanceLeaseMismatch(PermissionError):
    """Raised when a caller tries to release another maintenance lease."""


class WebRuntime:
    """Single authoritative runtime shared by GPIO and all browser clients."""

    def __init__(
        self,
        controller: TimerController,
        repository: SQLiteRunRepository,
        config: Config,
        buzzer: Buzzer,
        power_service: SystemPowerService,
        audio_service: AudioService | None = None,
        *,
        hardware_label: str,
        hardware_available: bool,
        show_mock_button: bool,
        show_mock_buzzer: bool,
        maintenance_marker: Path | None = None,
    ) -> None:
        self.controller = controller
        self.repository = repository
        self.config = config
        self.buzzer = buzzer
        self.power_service = power_service
        self.audio_service = audio_service or AudioService(config.audio)
        self.audio_service.on_devices_changed = self._schedule_system_broadcast
        self.curation = RunCurationService(repository)
        self.hardware_label = hardware_label
        self.hardware_available = hardware_available
        self.show_mock_button = show_mock_button
        self.show_mock_buzzer = show_mock_buzzer
        self.maintenance_marker = maintenance_marker
        self.history_revision = 0
        self.signal_revision = 0
        self.last_signal: str | None = None
        self._last_state: TimerState | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._confirmations: dict[str, PendingConfirmation] = {}
        self._confirmation_deadline: float | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._sound_task: asyncio.Task[None] | None = None
        self._start_phase: str | None = None
        self._start_deadline: float | None = None
        self._start_error: str | None = None
        self._maintenance_lease: MaintenanceLease | None = None
        self._closed = False
        self.controller.subscribe(self._on_snapshot)

    def start(self) -> None:
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._on_snapshot(self.controller.snapshot())

    async def close(self) -> None:
        self._closed = True
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
        if self._start_task is not None:
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        if self._sound_task is not None:
            self._sound_task.cancel()
            await asyncio.gather(self._sound_task, return_exceptions=True)
        await self.audio_service.close()
        for client in tuple(self._clients):
            await client.close(code=1001, message=b"TAKT server shutdown")

    def set_hardware_status(self, label: str, available: bool) -> None:
        self.hardware_label = label
        self.hardware_available = available
        self._schedule_state_broadcast()

    def primary_press(self, source: str = "web") -> bool:
        if self._start_task is not None and not self._start_task.done():
            return False
        if self._maintenance_active() and self.controller.state in (
            TimerState.READY,
            TimerState.SAVED_CONFIRMATION,
        ):
            return False
        if self.controller.state is TimerState.SAVED_CONFIRMATION:
            self.controller.finish_confirmation()
            return self._start_or_sequence(source)
        if self.controller.state is TimerState.READY:
            return self._start_or_sequence(source)
        return self.controller.handle_primary_button_press(source)

    def _start_or_sequence(self, source: str) -> bool:
        if self._maintenance_active():
            return False
        self._start_error = None
        if not self.audio_service.enabled:
            return self.controller.start(source)
        self._start_phase = "preparing"
        self._start_deadline = None
        self._start_task = asyncio.create_task(self._run_start_sequence(source))
        self._schedule_state_broadcast()
        return True

    def dispatch_action(self, action: str) -> bool:
        actions = {
            "primary": lambda: self.primary_press("web"),
            "add_5": lambda: self.controller.add_time(5_000),
            "add_10": lambda: self.controller.add_time(10_000),
            "subtract_5": lambda: self.controller.subtract_time(5_000),
            "subtract_10": lambda: self.controller.subtract_time(10_000),
            "save": lambda: self.controller.save() is not None,
            "request_discard": self.controller.request_discard,
            "cancel_discard": self.controller.cancel_discard,
            "confirm_discard": self.controller.confirm_discard,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError("Unbekannte Aktion.")
        changed = bool(handler())
        if action == "save" and changed:
            self.history_revision += 1
            self._schedule_history_broadcast()
        return changed

    def acquire_maintenance(
        self,
        *,
        request_id: str,
        owner: str,
        reason: str = "",
        ttl_seconds: int = 30,
    ) -> dict[str, object]:
        if not 5 <= ttl_seconds <= 120:
            raise ValueError("Maintenance lease TTL must be between 5 and 120 seconds.")
        now = time.monotonic()
        self._expire_maintenance(now)
        current = self._maintenance_lease
        if self._persistent_maintenance_active():
            raise MaintenanceUnavailable("TAKT is reserved for an in-progress system update.")
        if current is not None:
            if current.request_id == request_id and current.owner == owner:
                current.expires_at = max(current.expires_at, now + ttl_seconds)
                return {
                    "acquired": True,
                    "reused": True,
                    "lease_token": current.token,
                    "maintenance": self._maintenance_payload(now),
                }
            raise MaintenanceUnavailable("TAKT is already reserved for maintenance.")
        if self.controller.state is not TimerState.READY:
            raise MaintenanceUnavailable(
                f"TAKT is {self.controller.state.value}; maintenance requires ready state."
            )
        if self._start_task is not None and not self._start_task.done():
            raise MaintenanceUnavailable("A timer start sequence is already active.")
        self._maintenance_lease = MaintenanceLease(
            token=secrets.token_urlsafe(32),
            request_id=request_id,
            owner=owner,
            reason=reason,
            acquired_at=now,
            expires_at=now + ttl_seconds,
        )
        LOGGER.info(
            "maintenance_acquired request_id=%s owner=%s ttl_seconds=%s",
            request_id,
            owner,
            ttl_seconds,
        )
        self._schedule_state_broadcast()
        return {
            "acquired": True,
            "reused": False,
            "lease_token": self._maintenance_lease.token,
            "maintenance": self._maintenance_payload(now),
        }

    def release_maintenance(self, lease_token: str) -> bool:
        self._expire_maintenance()
        current = self._maintenance_lease
        if current is None:
            return False
        if not secrets.compare_digest(current.token, lease_token):
            raise MaintenanceLeaseMismatch("Maintenance lease token does not match.")
        LOGGER.info(
            "maintenance_released request_id=%s owner=%s",
            current.request_id,
            current.owner,
        )
        self._maintenance_lease = None
        self._schedule_state_broadcast()
        return True

    def maintenance_status(self) -> dict[str, object]:
        now = time.monotonic()
        self._expire_maintenance(now)
        return self._maintenance_payload(now)

    def state_payload(self) -> dict[str, object]:
        payload = serialize_snapshot(
            self.controller.snapshot(),
            hardware_label=self.hardware_label,
            hardware_available=self.hardware_available,
            history_revision=self.history_revision,
            signal_revision=self.signal_revision,
            last_signal=self.last_signal,
        )
        remaining_ms = 0
        if self._start_deadline is not None:
            remaining_ms = max(
                0,
                round((self._start_deadline - asyncio.get_running_loop().time()) * 1000),
            )
        payload["start_sequence"] = {
            "active": self._start_task is not None and not self._start_task.done(),
            "phase": self._start_phase,
            "remaining_ms": remaining_ms,
            "error": self._start_error,
        }
        payload["sound_playing"] = self._sound_task is not None and not self._sound_task.done()
        payload["maintenance"] = self.maintenance_status()
        return payload

    def history_payload(self, chart_days: int | None) -> dict[str, object]:
        return serialize_history(
            self.repository,
            chart_days=chart_days,
            best_limit=self.config.display.best_runs_limit,
        )

    def system_payload(self) -> dict[str, object]:
        return {
            "shutdown_available": self.power_service.available,
            "model": self.power_service.model,
            "mock_button": self.show_mock_button,
            "mock_buzzer": self.show_mock_buzzer,
            "audio": self.audio_service.payload(),
        }

    async def scan_audio_devices(self) -> dict[str, object]:
        await self.audio_service.scan_bluetooth()
        return self.system_payload()

    async def connect_audio_device(self, address: str) -> dict[str, object]:
        await self.audio_service.connect_bluetooth(address)
        return self.system_payload()

    async def forget_audio_device(self, address: str) -> dict[str, object]:
        await self.audio_service.forget_bluetooth(address)
        return self.system_payload()

    async def update_audio_settings(
        self,
        *,
        enabled: bool,
        output: str,
        delay_milliseconds: int,
        device_address: str | None,
        device_name: str | None,
    ) -> dict[str, object]:
        self.audio_service.update_settings(
            enabled=enabled,
            output=output,
            delay_milliseconds=delay_milliseconds,
            device_address=device_address,
            device_name=device_name,
        )
        return self.system_payload()

    async def test_audio(self) -> dict[str, object]:
        await self.audio_service.test_sound()
        return self.system_payload()

    def prepare_confirmation(
        self,
        operation: str,
        *,
        run_id: int | None = None,
        delta_ms: int = 0,
    ) -> dict[str, object]:
        if operation == "shutdown":
            if not self.power_service.available:
                raise ValueError("Herunterfahren ist nur auf einem Raspberry Pi verfügbar.")
            confirmation = PendingConfirmation(operation, None, 0, time.monotonic() + 30)
            details = {
                "title": "Raspberry Pi wirklich herunterfahren?",
                "message": (
                    "TAKT wird beendet und Raspberry Pi OS wird geordnet "
                    "heruntergefahren. Strom erst danach trennen."
                ),
                "warning": (
                    "Der aktuell gestoppte Lauf wurde noch nicht gespeichert."
                    if self.controller.state
                    in (TimerState.STOPPED, TimerState.DISCARD_CONFIRMATION)
                    else None
                ),
                "confirm_label": "JETZT HERUNTERFAHREN",
            }
        else:
            if run_id is None:
                raise ValueError("Es wurde kein Lauf ausgewählt.")
            run = self.repository.get_run(run_id)
            if run is None:
                raise ValueError("Der ausgewählte Lauf existiert nicht mehr.")
            if operation == "adjust":
                corrected_ms = max(0, run.added_time.milliseconds + delta_ms)
                if corrected_ms == run.added_time.milliseconds:
                    raise ValueError("Der Zuschlag ist bereits bei +00:00.00.")
                corrected = Duration(corrected_ms)
                confirmation = PendingConfirmation(
                    operation, run_id, delta_ms, time.monotonic() + 30
                )
                details = {
                    "title": "Gespeicherten Lauf wirklich ändern?",
                    "message": (
                        f"{run.started_at.astimezone():%d.%m.%Y, %H:%M} · Lauf {run.run_number}"
                    ),
                    "lines": [
                        f"Ist-Zeit: {run.actual_time.format_stopwatch()} (unverändert)",
                        (f"Zuschlag: {run.added_time.format_added()} → {corrected.format_added()}"),
                        (
                            f"Gesamtzeit: {run.total_time.format_stopwatch()} → "
                            f"{(run.actual_time + corrected).format_stopwatch()}"
                        ),
                    ],
                    "warning": "Die gespeicherte Wertung wird dadurch verändert.",
                    "confirm_label": "ÄNDERUNG BESTÄTIGEN",
                }
            elif operation == "delete":
                confirmation = PendingConfirmation(operation, run_id, 0, time.monotonic() + 30)
                details = {
                    "title": "Diesen Lauf endgültig löschen?",
                    "message": (
                        f"{run.started_at.astimezone():%d.%m.%Y, %H:%M} · Lauf {run.run_number}"
                    ),
                    "lines": [
                        f"Ist-Zeit: {run.actual_time.format_stopwatch()}",
                        f"Zuschlag: {run.added_time.format_added()}",
                        f"Gesamtzeit: {run.total_time.format_stopwatch()}",
                    ],
                    "warning": "Dieser Vorgang kann nicht rückgängig gemacht werden.",
                    "confirm_label": "ENDGÜLTIG LÖSCHEN",
                }
            else:
                raise ValueError("Unbekannte Bestätigung.")
        token = secrets.token_urlsafe(24)
        self._confirmations[token] = confirmation
        self._remove_expired_confirmations()
        return {"confirmation_id": token, **details}

    async def confirm(self, token: str) -> dict[str, object]:
        pending = self._confirmations.pop(token, None)
        if pending is None or pending.expires_at < time.monotonic():
            raise ValueError("Die Bestätigung ist abgelaufen. Bitte erneut versuchen.")
        if pending.operation == "adjust" and pending.run_id is not None:
            run = self.curation.adjust_added_time(pending.run_id, pending.delta_ms)
            message = (
                f"Lauf {run.run_number}: Zuschlag {run.added_time.format_added()} · "
                f"Gesamtzeit {run.total_time.format_stopwatch()}"
            )
        elif pending.operation == "delete" and pending.run_id is not None:
            run = self.repository.get_run(pending.run_id)
            if run is None or not self.curation.delete_run(pending.run_id):
                raise ValueError("Der Lauf existiert nicht mehr.")
            message = f"Lauf {run.run_number} wurde gelöscht."
        elif pending.operation == "shutdown":
            self.power_service.shutdown()
            message = "Der Raspberry Pi wird heruntergefahren."
        else:
            raise ValueError("Ungültige Bestätigung.")
        if pending.operation != "shutdown":
            self.history_revision += 1
            self._schedule_history_broadcast()
            self._schedule_state_broadcast()
        return {"ok": True, "message": message}

    def add_client(self, client: web.WebSocketResponse) -> None:
        self._clients.add(client)

    def remove_client(self, client: web.WebSocketResponse) -> None:
        self._clients.discard(client)

    def _on_snapshot(self, snapshot: TimerSnapshot) -> None:
        state = snapshot.state
        if state is TimerState.STOPPED:
            self._stop_start_sound()
        if state is not self._last_state:
            event = self._signal_for_transition(self._last_state, state)
            self._last_state = state
            if event:
                self.last_signal = event
                self.signal_revision += 1
                self.buzzer.signal(event)
            if state is TimerState.SAVED_CONFIRMATION:
                self._confirmation_deadline = (
                    time.monotonic() + self.config.application.saved_confirmation_seconds
                )
            elif state is not TimerState.SAVED_CONFIRMATION:
                self._confirmation_deadline = None
        self._schedule_state_broadcast()

    async def _refresh_loop(self) -> None:
        while not self._closed:
            if self._expire_maintenance():
                self._schedule_state_broadcast()
            if self.controller.state is TimerState.RUNNING:
                self.controller.refresh()
                await asyncio.sleep(0.033)
            else:
                if self._start_task is not None and not self._start_task.done():
                    self._schedule_state_broadcast()
                if (
                    self.controller.state is TimerState.SAVED_CONFIRMATION
                    and self._confirmation_deadline is not None
                    and time.monotonic() >= self._confirmation_deadline
                ):
                    self.controller.finish_confirmation()
                await asyncio.sleep(0.1)

    async def _run_start_sequence(self, source: str) -> None:
        started_wait: asyncio.Task[bool] | None = None
        try:
            loop = asyncio.get_running_loop()
            playback_started = asyncio.Event()
            sound_task = asyncio.create_task(
                self.audio_service.play_start_sound(playback_started.set)
            )
            self._sound_task = sound_task
            sound_task.add_done_callback(self._on_sound_finished)
            started_wait = asyncio.create_task(playback_started.wait())
            done, _ = await asyncio.wait(
                (sound_task, started_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if sound_task in done and not playback_started.is_set():
                await sound_task
                raise RuntimeError("Das Startsignal wurde nicht gestartet.")
            await started_wait
            self._start_phase = "waiting"
            self._start_deadline = loop.time() + self.audio_service.delay_seconds
            self._schedule_state_broadcast()
            while loop.time() < self._start_deadline:
                if sound_task.done():
                    error = sound_task.exception()
                    if error is not None:
                        raise error
                remaining = self._start_deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.025, remaining))
            if (
                self.controller.state is TimerState.READY
                and not self._closed
                and not self._maintenance_active()
            ):
                self.controller.start(source)
        except asyncio.CancelledError:
            self._stop_start_sound()
            raise
        except Exception as error:
            LOGGER.exception("start_sequence_failed")
            self._start_error = str(error)
            self._stop_start_sound()
        finally:
            if started_wait is not None and not started_wait.done():
                started_wait.cancel()
            self._start_phase = None
            self._start_deadline = None
            self._start_task = None
            self._schedule_state_broadcast()

    def _stop_start_sound(self) -> None:
        task = self._sound_task
        if task is not None and not task.done():
            task.cancel()
        self._sound_task = None

    def _maintenance_active(self) -> bool:
        self._expire_maintenance()
        return self._maintenance_lease is not None or self._persistent_maintenance_active()

    def _persistent_maintenance_active(self) -> bool:
        marker = self.maintenance_marker
        if marker is None or not marker.is_file():
            return False
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if float(payload.get("expires_at", 0)) <= time.time():
                marker.unlink(missing_ok=True)
                LOGGER.warning("persistent_maintenance_expired marker=%s", marker)
                return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                age = max(0.0, time.time() - marker.stat().st_mtime)
            except OSError:
                return True
            if age > 30 * 60:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    return True
                LOGGER.error(
                    "persistent_maintenance_invalid_marker_expired marker=%s age_seconds=%.0f",
                    marker,
                    age,
                )
                return False
            LOGGER.error(
                "persistent_maintenance_invalid_marker marker=%s age_seconds=%.0f",
                marker,
                age,
            )
            return True
        return True

    def _expire_maintenance(self, now: float | None = None) -> bool:
        current = self._maintenance_lease
        if current is None:
            return False
        current_time = time.monotonic() if now is None else now
        if current.expires_at > current_time:
            return False
        LOGGER.warning(
            "maintenance_expired request_id=%s owner=%s",
            current.request_id,
            current.owner,
        )
        self._maintenance_lease = None
        return True

    def _maintenance_payload(self, now: float) -> dict[str, object]:
        current = self._maintenance_lease
        start_sequence_active = self._start_task is not None and not self._start_task.done()
        persistent = self._persistent_maintenance_active()
        return {
            "held": current is not None or persistent,
            "can_acquire": (
                current is None
                and not persistent
                and self.controller.state is TimerState.READY
                and not start_sequence_active
            ),
            "timer_state": self.controller.state.value,
            "start_sequence_active": start_sequence_active,
            "request_id": current.request_id
            if current
            else "system-update"
            if persistent
            else None,
            "owner": current.owner if current else "takt-agent" if persistent else None,
            "reason": (
                current.reason
                if current
                else "TAKT update is being verified"
                if persistent
                else None
            ),
            "expires_in_seconds": (max(0, round(current.expires_at - now, 3)) if current else None),
        }

    def _on_sound_finished(self, task: asyncio.Task[None]) -> None:
        if self._sound_task is task:
            self._sound_task = None
        if task.cancelled():
            self._schedule_state_broadcast()
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "start_sound_ended_with_error",
                exc_info=(type(error), error, error.__traceback__),
            )
            self._start_error = str(error)
        self._schedule_state_broadcast()

    def _schedule_state_broadcast(self) -> None:
        if not self._clients or self._closed:
            return
        asyncio.create_task(self._broadcast({"type": "state", "data": self.state_payload()}))

    def _schedule_system_broadcast(self) -> None:
        if not self._clients or self._closed:
            return
        asyncio.create_task(self._broadcast({"type": "system", "data": self.system_payload()}))

    def _schedule_history_broadcast(self) -> None:
        if not self._clients or self._closed:
            return
        asyncio.create_task(
            self._broadcast(
                {
                    "type": "history_changed",
                    "revision": self.history_revision,
                }
            )
        )

    async def _broadcast(self, payload: dict[str, object]) -> None:
        stale: list[web.WebSocketResponse] = []
        for client in tuple(self._clients):
            try:
                await client.send_json(payload)
            except (ConnectionError, RuntimeError):
                stale.append(client)
        for client in stale:
            self._clients.discard(client)

    def _remove_expired_confirmations(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, confirmation in self._confirmations.items()
            if confirmation.expires_at < now
        ]
        for token in expired:
            self._confirmations.pop(token, None)

    @staticmethod
    def _signal_for_transition(
        previous: TimerState | None,
        current: TimerState,
    ) -> str | None:
        if current is TimerState.RUNNING:
            return "start"
        if current is TimerState.STOPPED:
            return "stop"
        if current is TimerState.SAVED_CONFIRMATION:
            return "save"
        if current is TimerState.READY and previous in (
            TimerState.STOPPED,
            TimerState.DISCARD_CONFIRMATION,
        ):
            return "discard"
        return None


def parse_chart_days(value: str | None, default: int) -> int | None:
    if value in (None, ""):
        return default
    if value == "all":
        return None
    try:
        days = int(value)
    except ValueError as error:
        raise web.HTTPBadRequest(text="Ungültiger Diagrammzeitraum.") from error
    if days not in (7, 30, 90):
        raise web.HTTPBadRequest(text="Ungültiger Diagrammzeitraum.")
    return days


async def json_body(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="JSON-Inhaltstyp erforderlich.")
    try:
        body = await request.json()
    except Exception as error:
        raise web.HTTPBadRequest(text="Ungültige Anfrage.") from error
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Ungültige Anfrage.")
    return body
