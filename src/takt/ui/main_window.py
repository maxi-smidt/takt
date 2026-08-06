from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from takt.application.run_curation_service import RunCurationService
from takt.application.system_power_service import SystemPowerService
from takt.application.timer_controller import TimerController, TimerSnapshot
from takt.buzzer import Buzzer, NullBuzzer
from takt.config import Config
from takt.domain.run import Run
from takt.domain.timer_state import TimerState
from takt.input.button_input import ButtonInput
from takt.persistence.run_repository import SQLiteRunRepository
from takt.ui.performance_chart import PerformanceChart
from takt.ui.settings_dialog import SettingsDialog
from takt.ui.theme import APP_STYLE

LOGGER = logging.getLogger(__name__)


class ClickableFrame(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    primary_press_requested = Signal()

    def __init__(
        self,
        controller: TimerController,
        repository: SQLiteRunRepository,
        config: Config,
        hardware_label: str,
        show_mock_button: bool = False,
        show_mock_buzzer: bool = False,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.repository = repository
        self.curation_service = RunCurationService(repository)
        self.system_power_service = SystemPowerService()
        self.config = config
        self.button_input: ButtonInput | None = None
        self.buzzer: Buzzer = NullBuzzer()
        self._last_state: TimerState | None = None
        self._timer_font_size = 0
        self._allow_close = False
        self._hardware_label = hardware_label
        self._show_mock_button = show_mock_button
        self._show_mock_buzzer = show_mock_buzzer
        self.setWindowTitle("TAKT · Feuerwehr-Zeitnahme")
        self.resize(1400, 820)
        self.setMinimumSize(1050, 650)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self.space_shortcut = QShortcut(QKeySequence("Space"), self)
        self.space_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.space_shortcut.activated.connect(self._handle_space_shortcut)

        self.primary_press_requested.connect(self._handle_primary_press)
        self.controller.subscribe(self.render)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)
        self._refresh_timer.timeout.connect(self.controller.refresh)
        self._refresh_timer.start()
        self._date_timer = QTimer(self)
        self._date_timer.setInterval(1_000)
        self._date_timer.timeout.connect(self._update_clock)
        self._date_timer.start()
        self._confirmation_timer = QTimer(self)
        self._confirmation_timer.setSingleShot(True)
        self._confirmation_timer.timeout.connect(self.controller.finish_confirmation)
        self._update_clock()
        self.refresh_history()
        self.render(self.controller.snapshot())

    def set_devices(self, button_input: ButtonInput, buzzer: Buzzer) -> None:
        self.button_input = button_input
        self.buzzer = buzzer

    def set_hardware_status(self, label: str, available: bool) -> None:
        self.hardware_status.setText(f"● TASTER: {label}")
        color = "#45c857" if available else "#f1a817"
        self.hardware_status.setStyleSheet(f"color: {color};")

    def show_mock_buzzer_signal(self, event: str) -> None:
        names = {
            "start": "START-SIGNAL",
            "stop": "STOPP-SIGNAL",
            "save": "GESPEICHERT",
            "discard": "VERWORFEN",
        }
        self.buzzer_status.setText(f"● SUMMER-MOCK: {names.get(event, event.upper())}")
        self.buzzer_status.setStyleSheet("color: #f1a817; font-weight: 800;")
        QApplication.beep()
        QTimer.singleShot(
            650,
            lambda: (
                self.buzzer_status.setText("○ SUMMER-MOCK: bereit"),
                self.buzzer_status.setStyleSheet("color: #91a1ad;"),
            ),
        )

    def render(self, snapshot: TimerSnapshot) -> None:
        state = snapshot.state
        state_changed = state is not self._last_state
        if state_changed:
            self._signal_state_change(self._last_state, state)
        self._last_state = state

        colors = {
            TimerState.READY: "#2aa9ff",
            TimerState.RUNNING: "#45c857",
            TimerState.STOPPED: "#f1a817",
            TimerState.SAVED_CONFIRMATION: "#45c857",
            TimerState.DISCARD_CONFIRMATION: "#ff5e64",
            TimerState.ERROR: "#ff5e64",
        }
        labels = {
            TimerState.READY: "BEREIT",
            TimerState.RUNNING: "LÄUFT",
            TimerState.STOPPED: "GESTOPPT",
            TimerState.SAVED_CONFIRMATION: "ZEIT GESPEICHERT!",
            TimerState.DISCARD_CONFIRMATION: "DIESEN LAUF VERWERFEN?",
            TimerState.ERROR: "FEHLER",
        }
        accent = colors[state]
        self.status_label.setText(labels[state])
        self.status_label.setStyleSheet(f"color: {accent};")
        self.timer_card.setStyleSheet(
            f"QFrame#card {{ background: #0d1822; border: 1px solid {accent}; "
            "border-radius: 10px; }"
        )

        shown_actual = snapshot.actual_time
        shown_added = snapshot.added_time
        shown_total = snapshot.total_time
        if state is TimerState.SAVED_CONFIRMATION and snapshot.last_saved_run is not None:
            shown_actual = snapshot.last_saved_run.actual_time
            shown_added = snapshot.last_saved_run.added_time
            shown_total = snapshot.last_saved_run.total_time

        self.timer_value.setText(shown_actual.format_stopwatch())
        self._apply_timer_font_size(state)
        self.actual_value.setText(shown_actual.format_stopwatch())
        self.added_value.setText(shown_added.format_added())
        self.total_value.setText(shown_total.format_stopwatch())

        is_basic = state in (TimerState.READY, TimerState.RUNNING)
        is_stopped = state is TimerState.STOPPED
        is_discard = state is TimerState.DISCARD_CONFIRMATION
        is_saved = state is TimerState.SAVED_CONFIRMATION
        can_subtract = is_stopped and shown_added.milliseconds > 0
        self.subtract_five_button.setEnabled(can_subtract)
        self.subtract_ten_button.setEnabled(can_subtract)
        self.timer_value.setVisible(is_basic)
        self.units_label.setVisible(is_basic)
        self.metrics_widget.setVisible(is_stopped or is_discard or is_saved)
        self.adjust_row_widget.setVisible(is_stopped)
        self.action_widget.setVisible(is_stopped)
        self.discard_action_widget.setVisible(is_discard)

        if state is TimerState.READY:
            self.hint_label.setText("Taste oder Leertaste zum Starten drücken")
        elif state is TimerState.RUNNING:
            self.hint_label.setText("Taste oder Leertaste zum Stoppen drücken")
        elif is_stopped:
            self.hint_label.setText(
                "Zuschlag: 5 / 0 hinzufügen · Strg+5 / Strg+0 abziehen · Enter speichern"
            )
        elif is_discard:
            self.hint_label.setText("Verwerfen bestätigen oder mit Esc abbrechen")
        elif is_saved:
            self.hint_label.setText("Weiter mit neuem Lauf …")
        else:
            self.hint_label.setText("")

        self.error_label.setVisible(bool(snapshot.error_message))
        self.error_label.setText(snapshot.error_message or "")
        self.settings_button.setEnabled(state is not TimerState.RUNNING)
        if state_changed:
            self._set_running_focus(state is TimerState.RUNNING)
        if is_saved and state_changed:
            self.refresh_history()
            delay_ms = int(self.config.application.saved_confirmation_seconds * 1_000)
            self._confirmation_timer.start(delay_ms)

    def refresh_history(self) -> None:
        today = self.repository.get_runs_for_date(date.today())
        best = self.repository.get_best_runs(self.config.display.best_runs_limit)
        self._fill_today_table(today)
        self._fill_best_table(best)
        self.today_count.setText(f"Läufe heute: {len(today)}")
        self.chart.set_runs(self.repository.get_recent_runs(self._chart_days))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        state = self.controller.state
        if key == Qt.Key.Key_5 and state is TimerState.STOPPED:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.controller.subtract_time(5_000)
            else:
                self.controller.add_time(5_000)
            return
        elif key == Qt.Key.Key_0 and state is TimerState.STOPPED:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.controller.subtract_time(10_000)
            else:
                self.controller.add_time(10_000)
            return
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if state is TimerState.STOPPED:
                self.controller.save()
                return
            if state is TimerState.DISCARD_CONFIRMATION:
                self.controller.confirm_discard()
                return
        elif key == Qt.Key.Key_R and state is TimerState.STOPPED:
            self.controller.request_discard()
            return
        elif key == Qt.Key.Key_Escape:
            if state is TimerState.DISCARD_CONFIRMATION:
                self.controller.cancel_discard()
                return
            if state is TimerState.STOPPED:
                self.controller.request_discard()
                return
            if self.isFullScreen():
                self.showNormal()
                return
        elif key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
            return
        elif key == Qt.Key.Key_Q and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if self._confirm_close():
            self._allow_close = True
            event.accept()
        else:
            event.ignore()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "timer_value"):
            self._apply_timer_font_size(self.controller.state)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(10)
        outer.addLayout(self._build_header())

        self.content_grid = QGridLayout()
        self.content_grid.setHorizontalSpacing(10)
        self.content_grid.setVerticalSpacing(10)
        self.content_grid.setColumnStretch(0, 42)
        self.content_grid.setColumnStretch(1, 58)
        self.content_grid.setRowStretch(0, 54)
        self.content_grid.setRowStretch(1, 46)
        self.timer_card = self._build_timer_card()
        self.today_card = self._build_today_card()
        self.best_card = self._build_best_card()
        self.chart_card = self._build_chart_card()
        self.content_grid.addWidget(self.timer_card, 0, 0)
        self.content_grid.addWidget(self.today_card, 0, 1)
        self.content_grid.addWidget(self.best_card, 1, 0)
        self.content_grid.addWidget(self.chart_card, 1, 1)
        outer.addLayout(self.content_grid, 1)
        outer.addLayout(self._build_footer())

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        emblem = QLabel("⏱")
        emblem.setStyleSheet("font-size: 26px; color: #f1a817;")
        layout.addWidget(emblem)
        name_box = QVBoxLayout()
        name_box.setSpacing(0)
        brand = QLabel("TAKT")
        brand.setObjectName("brand")
        subtitle = QLabel("Feuerwehr-Zeitnahme")
        subtitle.setObjectName("subtitle")
        name_box.addWidget(brand)
        name_box.addWidget(subtitle)
        layout.addLayout(name_box)
        layout.addStretch()
        self.date_label = QLabel()
        self.date_label.setObjectName("subtitle")
        layout.addWidget(self.date_label)
        self.settings_button = QPushButton("EINSTELLUNGEN")
        self.settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_button)
        return layout

    def _build_timer_card(self) -> QFrame:
        card = ClickableFrame()
        card.setObjectName("card")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.clicked.connect(self.primary_press_requested.emit)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(10)
        self.status_label = QLabel("BEREIT")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        self.timer_value = QLabel("00:00.00")
        self.timer_value.setObjectName("timer")
        self.timer_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.timer_value)
        self.units_label = QLabel("MM                         SS        HS")
        self.units_label.setObjectName("hint")
        self.units_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.units_label)

        self.metrics_widget = QWidget()
        metrics = QGridLayout(self.metrics_widget)
        metrics.setContentsMargins(12, 0, 12, 0)
        metrics.setHorizontalSpacing(14)
        labels = ["IST-ZEIT", "ZUSCHLAG"]
        values: list[QLabel] = []
        for column, text in enumerate(labels):
            label = QLabel(text)
            label.setObjectName("metricLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("00:00.00")
            value.setObjectName("metricValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            metrics.addWidget(label, 0, column)
            metrics.addWidget(value, 1, column)
            values.append(value)
        self.actual_value, self.added_value = values
        self.added_value.setStyleSheet("color: #f1a817;")
        total_highlight = QFrame()
        total_highlight.setObjectName("totalHighlight")
        total_layout = QVBoxLayout(total_highlight)
        total_layout.setContentsMargins(12, 8, 12, 9)
        total_layout.setSpacing(0)
        total_label = QLabel("GESAMTZEIT")
        total_label.setObjectName("totalLabel")
        total_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.total_value = QLabel("00:00.00")
        self.total_value.setObjectName("totalValue")
        self.total_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        total_layout.addWidget(total_label)
        total_layout.addWidget(self.total_value)
        metrics.addWidget(total_highlight, 2, 0, 1, 2)
        layout.addWidget(self.metrics_widget)

        self.adjust_row_widget = QWidget()
        adjust_row = QGridLayout(self.adjust_row_widget)
        adjust_row.setContentsMargins(0, 2, 0, 2)
        adjust_label = QLabel("ZUSCHLAG ANPASSEN")
        adjust_label.setObjectName("metricLabel")
        adjust_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        adjust_row.addWidget(adjust_label, 0, 0, 1, 4)
        self.subtract_ten_button = QPushButton("−10 s")
        self.subtract_ten_button.setObjectName("subtract")
        self.subtract_ten_button.clicked.connect(
            lambda: self.controller.subtract_time(10_000)
        )
        self.subtract_five_button = QPushButton("−5 s")
        self.subtract_five_button.setObjectName("subtract")
        self.subtract_five_button.clicked.connect(
            lambda: self.controller.subtract_time(5_000)
        )
        add_five = QPushButton("+5 s")
        add_five.setObjectName("adjust")
        add_five.clicked.connect(lambda: self.controller.add_time(5_000))
        add_ten = QPushButton("+10 s")
        add_ten.setObjectName("adjust")
        add_ten.clicked.connect(lambda: self.controller.add_time(10_000))
        adjust_row.addWidget(self.subtract_ten_button, 1, 0)
        adjust_row.addWidget(self.subtract_five_button, 1, 1)
        adjust_row.addWidget(add_five, 1, 2)
        adjust_row.addWidget(add_ten, 1, 3)
        layout.addWidget(self.adjust_row_widget)

        self.action_widget = QWidget()
        actions = QHBoxLayout(self.action_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        save_button = QPushButton("SPEICHERN  (Enter)")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.controller.save)
        discard_button = QPushButton("VERWERFEN  (R)")
        discard_button.setObjectName("danger")
        discard_button.clicked.connect(self.controller.request_discard)
        actions.addWidget(save_button, 2)
        actions.addWidget(discard_button, 1)
        layout.addWidget(self.action_widget)

        self.discard_action_widget = QWidget()
        discard_actions = QHBoxLayout(self.discard_action_widget)
        discard_actions.setContentsMargins(0, 0, 0, 0)
        cancel_button = QPushButton("ABBRECHEN  (Esc)")
        cancel_button.clicked.connect(self.controller.cancel_discard)
        confirm_button = QPushButton("VERWERFEN  (Enter)")
        confirm_button.setObjectName("danger")
        confirm_button.clicked.connect(self.controller.confirm_discard)
        discard_actions.addWidget(cancel_button)
        discard_actions.addWidget(confirm_button)
        layout.addWidget(self.discard_action_widget)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("hint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        return card

    def _build_today_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.addWidget(self._section_label("Heutige Läufe"))
        self.today_table = self._table(["#", "Uhrzeit", "Ist-Zeit", "Zuschlag", "Gesamtzeit"])
        layout.addWidget(self.today_table)
        self.today_count = QLabel("Läufe heute: 0")
        self.today_count.setObjectName("hint")
        layout.addWidget(self.today_count)
        return card

    def _build_best_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.addWidget(self._section_label("Bestzeiten · alle Zeiten"))
        self.best_table = self._table(["#", "Datum", "Ist-Zeit", "Zuschlag", "Gesamtzeit"])
        layout.addWidget(self.best_table)
        return card

    def _build_chart_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        header = QHBoxLayout()
        header.addWidget(self._section_label("Leistung im Zeitverlauf"))
        header.addStretch()
        configured_days = self.config.display.chart_default_days
        self._chart_days: int | None = configured_days if configured_days in (7, 30, 90) else None
        self.period_group = QButtonGroup(self)
        self.period_group.setExclusive(True)
        period_switch = QHBoxLayout()
        period_switch.setSpacing(4)
        for label, days in (("7", 7), ("30", 30), ("90", 90), ("ALLE", None)):
            button = QPushButton(label)
            button.setObjectName("period")
            button.setCheckable(True)
            button.setToolTip(f"Diagrammzeitraum: {label} Tage" if days else "Alle Läufe")
            button.setChecked(days == self._chart_days)
            button.clicked.connect(
                lambda checked=False, selected_days=days: self._set_chart_period(
                    selected_days
                )
            )
            self.period_group.addButton(button)
            period_switch.addWidget(button)
        header.addLayout(period_switch)
        layout.addLayout(header)
        self.chart = PerformanceChart()
        self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.chart)
        return card

    def _set_chart_period(self, days: int | None) -> None:
        self._chart_days = days
        self.refresh_history()

    def _open_settings(self) -> None:
        if self.controller.state is TimerState.RUNNING:
            return
        dialog = SettingsDialog(
            self.curation_service,
            self,
            toggle_fullscreen=self._toggle_fullscreen,
            is_fullscreen=self.isFullScreen,
            request_shutdown=self._request_system_shutdown,
            shutdown_available=self.system_power_service.available,
            has_unsaved_run=self.controller.state
            in (TimerState.STOPPED, TimerState.DISCARD_CONFIRMATION),
        )
        dialog.exec()
        if dialog.changed:
            self.refresh_history()

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _request_system_shutdown(self) -> str | None:
        try:
            self.system_power_service.shutdown()
        except RuntimeError as error:
            LOGGER.exception("system_shutdown_failed")
            return str(error)
        LOGGER.info("system_shutdown_requested")
        application = QApplication.instance()
        if application is not None:
            QTimer.singleShot(0, application.quit)
        return None

    def _set_running_focus(self, active: bool) -> None:
        for widget in (self.today_card, self.best_card, self.chart_card):
            widget.setVisible(not active)
        if active:
            self.content_grid.setColumnStretch(0, 1)
            self.content_grid.setColumnStretch(1, 0)
            self.content_grid.setRowStretch(0, 1)
            self.content_grid.setRowStretch(1, 0)
        else:
            self.content_grid.setColumnStretch(0, 42)
            self.content_grid.setColumnStretch(1, 58)
            self.content_grid.setRowStretch(0, 54)
            self.content_grid.setRowStretch(1, 46)

    def _apply_timer_font_size(self, state: TimerState) -> None:
        if state is TimerState.RUNNING:
            desired_size = min(250, max(150, int(self.width() * 0.14)))
        else:
            desired_size = 66
        if desired_size == self._timer_font_size:
            return
        self._timer_font_size = desired_size
        self.timer_value.setStyleSheet(f"font-size: {desired_size}px;")

    def _confirm_close(self) -> bool:
        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("TAKT beenden")
        dialog.setModal(True)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title = QLabel("TAKT wirklich beenden?")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        message = QLabel("Die Anwendung wird geschlossen. Gespeicherte Läufe bleiben erhalten.")
        message.setObjectName("subtitle")
        message.setWordWrap(True)
        layout.addWidget(message)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = QPushButton("ABBRECHEN")
        cancel_button.clicked.connect(dialog.reject)
        close_button = QPushButton("BEENDEN")
        close_button.setObjectName("danger")
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(cancel_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        self.hardware_status = QLabel(f"● TASTER: {self._hardware_label}")
        self.hardware_status.setObjectName("footer")
        self.hardware_status.setStyleSheet("color: #45c857;")
        footer.addWidget(self.hardware_status)
        self.buzzer_status = QLabel("○ SUMMER-MOCK: bereit")
        self.buzzer_status.setObjectName("footer")
        self.buzzer_status.setVisible(self._show_mock_buzzer)
        footer.addWidget(self.buzzer_status)
        footer.addStretch()
        self.mock_button = QPushButton("MOCK-TASTER DRÜCKEN")
        self.mock_button.setObjectName("mock")
        self.mock_button.setVisible(self._show_mock_button)
        self.mock_button.clicked.connect(self.primary_press_requested.emit)
        footer.addWidget(self.mock_button)
        return footer

    def _handle_primary_press(self, source: str = "mock-taster") -> None:
        self.controller.handle_primary_button_press(source)

    def _handle_space_shortcut(self) -> None:
        state = self.controller.state
        if state in (TimerState.READY, TimerState.RUNNING):
            self._handle_primary_press("tastatur")
        elif state is TimerState.SAVED_CONFIRMATION:
            self._confirmation_timer.stop()
            self.controller.finish_confirmation()
            self.controller.start("tastatur")

    def _signal_state_change(
        self,
        previous: TimerState | None,
        current: TimerState,
    ) -> None:
        if current is TimerState.RUNNING:
            self.buzzer.signal("start")
        elif current is TimerState.STOPPED:
            self.buzzer.signal("stop")
        elif current is TimerState.SAVED_CONFIRMATION:
            self.buzzer.signal("save")
        elif current is TimerState.READY and previous in (
            TimerState.STOPPED,
            TimerState.DISCARD_CONFIRMATION,
        ):
            self.buzzer.signal("discard")

    def _update_clock(self) -> None:
        now = self.controller.clock.now().astimezone()
        days = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
        self.date_label.setText(
            f"{days[now.weekday()]}, {now:%d.%m.%Y}   ·   {now:%H:%M:%S}"
        )

    def _fill_today_table(self, runs: list[Run]) -> None:
        rows = [
            (
                str(run.run_number),
                run.started_at.astimezone().strftime("%H:%M:%S"),
                run.actual_time.format_stopwatch(),
                run.added_time.format_added(),
                run.total_time.format_stopwatch(),
            )
            for run in runs
        ]
        self._set_rows(self.today_table, rows)

    def _fill_best_table(self, runs: list[Run]) -> None:
        rows = [
            (
                str(rank),
                run.started_at.astimezone().strftime("%d.%m.%Y"),
                run.actual_time.format_stopwatch(),
                run.added_time.format_added(),
                run.total_time.format_stopwatch(),
            )
            for rank, run in enumerate(runs, 1)
        ]
        self._set_rows(self.best_table, rows)

    @staticmethod
    def _set_rows(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column_index == 3:
                    item.setForeground(Qt.GlobalColor.yellow)
                table.setItem(row_index, column_index, item)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(
            table.horizontalHeader().ResizeMode.Stretch
        )
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return table

    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("sectionTitle")
        return label
