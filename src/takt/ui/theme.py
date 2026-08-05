APP_STYLE = """
* {
    font-family: "Inter", "Arial", sans-serif;
    color: #e8edf2;
}
QMainWindow, QWidget#root {
    background: #07111a;
}
QDialog, QMessageBox, QMenu {
    background: #0d1822;
    color: #e8edf2;
}
QDialog#confirmationDialog {
    border: 1px solid #31495a;
}
QDialog#settingsDialog {
    background: #07111a;
}
QFrame#settingsActionCard {
    background: #101d27;
    border: 1px solid #2b4050;
    border-radius: 8px;
}
QLabel#dialogTitle {
    color: #ffffff;
    font-size: 18px;
    font-weight: 800;
}
QLabel#settingsHint {
    color: #adc0cc;
    background: #102531;
    border: 1px solid #28495c;
    border-radius: 6px;
    padding: 9px 12px;
}
QLabel#settingsFeedback {
    color: #64d879;
    min-height: 20px;
}
QLabel#deleteWarning {
    color: #ff8b8f;
    font-weight: 700;
}
QLabel#changeWarning {
    color: #f1b84b;
    font-weight: 700;
}
QLabel#confirmationDetails {
    color: #dce7ed;
    background: #101d27;
    border: 1px solid #2b4050;
    border-radius: 6px;
    padding: 12px;
    font-family: "Menlo", "SF Mono", monospace;
}
QFrame#card {
    background: #0d1822;
    border: 1px solid #223442;
    border-radius: 10px;
}
QLabel#brand {
    font-size: 21px;
    font-weight: 800;
    color: #ffffff;
}
QLabel#subtitle {
    font-size: 13px;
    color: #8fa3b3;
}
QLabel#sectionTitle {
    color: #55bfff;
    font-size: 14px;
    font-weight: 700;
    text-transform: uppercase;
}
QLabel#timer {
    font-family: "Menlo", "SF Mono", monospace;
    font-size: 66px;
    font-weight: 700;
    color: #f4f6f8;
}
QLabel#status {
    font-size: 19px;
    font-weight: 800;
}
QLabel#metricLabel {
    color: #90a2b0;
    font-size: 11px;
    font-weight: 700;
}
QLabel#metricValue {
    font-family: "Menlo", "SF Mono", monospace;
    font-size: 27px;
    font-weight: 700;
}
QFrame#totalHighlight {
    background: #132632;
    border: 1px solid #58778b;
    border-radius: 8px;
}
QLabel#totalLabel {
    color: #b8c6cf;
    font-size: 12px;
    font-weight: 800;
}
QLabel#totalValue {
    font-family: "Menlo", "SF Mono", monospace;
    color: #ffffff;
    font-size: 44px;
    font-weight: 800;
}
QLabel#hint, QLabel#footer {
    color: #91a1ad;
    font-size: 12px;
}
QLabel#error {
    background: #3a1718;
    color: #ff8b8f;
    border: 1px solid #7c3034;
    border-radius: 5px;
    padding: 7px;
}
QPushButton {
    min-height: 38px;
    padding: 0 14px;
    border: 1px solid #385063;
    border-radius: 6px;
    background: #152430;
    font-weight: 700;
}
QPushButton:hover {
    background: #1d3342;
    border-color: #5e8199;
}
QPushButton:pressed {
    background: #0b151d;
}
QPushButton#primary {
    background: #226b32;
    border-color: #3b9b50;
}
QPushButton#primary:hover {
    background: #2b813d;
}
QPushButton#danger {
    color: #ff9a9e;
    border-color: #86393d;
}
QPushButton#adjust {
    color: #ffb517;
    border-color: #ad7410;
    font-size: 15px;
}
QPushButton#subtract {
    color: #9cc8e5;
    border-color: #43677d;
    font-size: 15px;
}
QPushButton#subtract:disabled {
    color: #4d6472;
    border-color: #273b48;
    background: #101a22;
}
QPushButton#mock {
    min-height: 48px;
    background: #0e5e91;
    border-color: #2ba9ef;
}
QPushButton#period {
    min-width: 42px;
    min-height: 30px;
    padding: 0 8px;
    color: #91a5b4;
    background: #101d27;
    border: 1px solid #2b4050;
    border-radius: 5px;
    font-size: 11px;
}
QPushButton#period:hover {
    color: #dce8ef;
    background: #172a37;
    border-color: #43677d;
}
QPushButton#period:checked {
    color: #ffffff;
    background: #17699a;
    border-color: #39aee9;
}
QHeaderView::section {
    background: #101e29;
    color: #8fa3b3;
    padding: 7px 5px;
    border: 0;
    border-bottom: 1px solid #2a3c49;
    font-size: 11px;
    font-weight: 700;
}
QTableWidget {
    background: transparent;
    alternate-background-color: #101e29;
    border: 0;
    gridline-color: transparent;
    selection-background-color: #284429;
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 5px;
    border-bottom: 1px solid #182a36;
}
QMenu {
    border: 1px solid #385063;
    padding: 5px;
}
QMenu::item {
    background: transparent;
    border-radius: 4px;
    padding: 7px 24px 7px 10px;
}
QMenu::item:selected {
    background: #17699a;
}
QAbstractItemView {
    background: #101d27;
    alternate-background-color: #142632;
    color: #e8edf2;
    border: 1px solid #385063;
    selection-background-color: #17699a;
    selection-color: #ffffff;
}
QToolTip {
    background: #152430;
    color: #ffffff;
    border: 1px solid #4e687a;
    padding: 6px;
}
"""
