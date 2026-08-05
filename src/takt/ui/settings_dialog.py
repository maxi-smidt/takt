from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from takt.application.run_curation_service import RunCurationService
from takt.domain.duration import Duration
from takt.domain.run import Run


class SettingsDialog(QDialog):
    """Dark settings window for carefully curating saved run data."""

    def __init__(
        self,
        curation_service: RunCurationService,
        parent: QWidget,
        toggle_fullscreen: Callable[[], None] | None = None,
        is_fullscreen: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.curation_service = curation_service
        self.toggle_fullscreen = toggle_fullscreen
        self.is_fullscreen = is_fullscreen
        self.changed = False
        self.setObjectName("settingsDialog")
        self.setWindowTitle("TAKT · Einstellungen")
        self.resize(920, 590)
        self.setMinimumSize(760, 500)
        self._build_ui()
        self._refresh_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("EINSTELLUNGEN")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Gespeicherte Laufdaten kuratieren")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        close_button = QPushButton("SCHLIESSEN")
        close_button.clicked.connect(self.accept)
        header.addWidget(close_button)
        layout.addLayout(header)

        information = QLabel(
            "Wähle einen Lauf aus, um dessen Zuschlag zu korrigieren oder einen "
            "fehlerhaften Eintrag zu löschen. Die gemessene Ist-Zeit bleibt unverändert."
        )
        information.setObjectName("settingsHint")
        information.setWordWrap(True)
        layout.addWidget(information)

        if self.toggle_fullscreen is not None:
            display_card = QFrame()
            display_card.setObjectName("settingsActionCard")
            display_layout = QHBoxLayout(display_card)
            display_layout.setContentsMargins(14, 10, 14, 10)
            display_label = QLabel("ANZEIGE")
            display_label.setObjectName("metricLabel")
            display_layout.addWidget(display_label)
            display_layout.addStretch()
            self.fullscreen_button = QPushButton()
            self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
            display_layout.addWidget(self.fullscreen_button)
            self._update_fullscreen_button()
            layout.addWidget(display_card)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("settingsTable")
        self.table.setHorizontalHeaderLabels(
            ["#", "Datum", "Uhrzeit", "Ist-Zeit", "Zuschlag", "Gesamtzeit"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        layout.addWidget(self.table, 1)

        correction_card = QFrame()
        correction_card.setObjectName("settingsActionCard")
        correction_layout = QHBoxLayout(correction_card)
        correction_layout.setContentsMargins(14, 10, 14, 10)
        correction_layout.setSpacing(8)
        correction_label = QLabel("ZUSCHLAG KORRIGIEREN")
        correction_label.setObjectName("metricLabel")
        correction_layout.addWidget(correction_label)
        correction_layout.addStretch()

        self.adjust_buttons: list[QPushButton] = []
        adjustments = (
            ("−10 s", -10_000),
            ("−5 s", -5_000),
            ("+5 s", 5_000),
            ("+10 s", 10_000),
        )
        for label, delta in adjustments:
            button = QPushButton(label)
            button.setObjectName("subtract" if delta < 0 else "adjust")
            button.clicked.connect(
                lambda checked=False, adjustment=delta: self._adjust_selected(adjustment)
            )
            correction_layout.addWidget(button)
            self.adjust_buttons.append(button)

        self.delete_button = QPushButton("LAUF LÖSCHEN")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._delete_selected)
        correction_layout.addWidget(self.delete_button)
        layout.addWidget(correction_card)

        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("settingsFeedback")
        layout.addWidget(self.feedback_label)
        self._update_action_state()

    def _refresh_table(self, selected_run_id: int | None = None) -> None:
        runs = self.curation_service.list_runs()
        self.table.setRowCount(len(runs))
        selected_row = -1
        for row_index, run in enumerate(runs):
            values = (
                str(run.run_number),
                run.started_at.astimezone().strftime("%d.%m.%Y"),
                run.started_at.astimezone().strftime("%H:%M:%S"),
                run.actual_time.format_stopwatch(),
                run.added_time.format_added(),
                run.total_time.format_stopwatch(),
            )
            for column_index, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, run.id)
                if column_index == 4:
                    item.setForeground(Qt.GlobalColor.yellow)
                self.table.setItem(row_index, column_index, item)
            if run.id == selected_run_id:
                selected_row = row_index
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif runs:
            self.table.selectRow(0)
        self._update_action_state()

    def _selected_run(self) -> Run | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        run_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(run_id, int):
            return None
        return next(
            (run for run in self.curation_service.list_runs() if run.id == run_id),
            None,
        )

    def _adjust_selected(self, delta_ms: int) -> None:
        run = self._selected_run()
        if run is None or run.id is None:
            return
        corrected_added = Duration(max(0, run.added_time.milliseconds + delta_ms))
        if corrected_added == run.added_time:
            self.feedback_label.setText("Der Zuschlag ist bereits bei +00:00.00.")
            return
        if not self._confirm_adjustment(run, corrected_added):
            return
        updated = self.curation_service.adjust_added_time(run.id, delta_ms)
        self.changed = True
        self.feedback_label.setText(
            f"Lauf {updated.run_number}: Zuschlag {updated.added_time.format_added()} · "
            f"Gesamtzeit {updated.total_time.format_stopwatch()}"
        )
        self._refresh_table(updated.id)

    def _delete_selected(self) -> None:
        run = self._selected_run()
        if run is None or run.id is None or not self._confirm_delete(run):
            return
        if self.curation_service.delete_run(run.id):
            self.changed = True
            self.feedback_label.setText(
                f"Lauf {run.run_number} vom {run.started_at.astimezone():%d.%m.%Y} wurde gelöscht."
            )
            self._refresh_table()

    def _confirm_delete(self, run: Run) -> bool:
        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Lauf löschen")
        dialog.setModal(True)
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title = QLabel("Diesen Lauf endgültig löschen?")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        details = QLabel(
            f"{run.started_at.astimezone():%d.%m.%Y, %H:%M} · Lauf {run.run_number}\n"
            f"Ist-Zeit: {run.actual_time.format_stopwatch()}\n"
            f"Zuschlag: {run.added_time.format_added()}\n"
            f"Gesamtzeit: {run.total_time.format_stopwatch()}"
        )
        details.setObjectName("subtitle")
        layout.addWidget(details)
        warning = QLabel("Dieser Vorgang kann nicht rückgängig gemacht werden.")
        warning.setObjectName("deleteWarning")
        layout.addWidget(warning)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = QPushButton("ABBRECHEN")
        cancel_button.clicked.connect(dialog.reject)
        delete_button = QPushButton("ENDGÜLTIG LÖSCHEN")
        delete_button.setObjectName("danger")
        delete_button.clicked.connect(dialog.accept)
        actions.addWidget(cancel_button)
        actions.addWidget(delete_button)
        layout.addLayout(actions)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _confirm_adjustment(self, run: Run, corrected_added: Duration) -> bool:
        corrected_total = run.actual_time + corrected_added
        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Gespeicherten Lauf ändern")
        dialog.setModal(True)
        dialog.setMinimumWidth(470)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title = QLabel("Gespeicherten Lauf wirklich ändern?")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        details = QLabel(
            f"{run.started_at.astimezone():%d.%m.%Y, %H:%M} · Lauf {run.run_number}\n\n"
            f"Ist-Zeit:       {run.actual_time.format_stopwatch()}  (unverändert)\n"
            f"Zuschlag:      {run.added_time.format_added()}  →  "
            f"{corrected_added.format_added()}\n"
            f"Gesamtzeit:  {run.total_time.format_stopwatch()}  →  "
            f"{corrected_total.format_stopwatch()}"
        )
        details.setObjectName("confirmationDetails")
        layout.addWidget(details)
        warning = QLabel("Die gespeicherte Wertung dieses Laufs wird dadurch verändert.")
        warning.setObjectName("changeWarning")
        layout.addWidget(warning)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = QPushButton("ABBRECHEN")
        cancel_button.clicked.connect(dialog.reject)
        confirm_button = QPushButton("ÄNDERUNG BESTÄTIGEN")
        confirm_button.setObjectName("primary")
        confirm_button.clicked.connect(dialog.accept)
        actions.addWidget(cancel_button)
        actions.addWidget(confirm_button)
        layout.addLayout(actions)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _update_action_state(self) -> None:
        has_selection = self.table.currentRow() >= 0
        for button in self.adjust_buttons:
            button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _toggle_fullscreen(self) -> None:
        if self.toggle_fullscreen is not None:
            self.toggle_fullscreen()
            self._update_fullscreen_button()

    def _update_fullscreen_button(self) -> None:
        if not hasattr(self, "fullscreen_button"):
            return
        is_fullscreen = self.is_fullscreen() if self.is_fullscreen is not None else False
        label = "VOLLBILD BEENDEN" if is_fullscreen else "VOLLBILD AKTIVIEREN"
        self.fullscreen_button.setText(f"{label}  (F11)")
