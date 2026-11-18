BG = "#12161c"
PANEL = "#1a2029"
PANEL_ALT = "#202733"
BORDER = "#2c3544"
TEXT = "#e7ebf2"
MUTED = "#8d98aa"
ACCENT = "#4c8dff"
GREEN = "#2fb36a"
RED = "#e5484d"
AMBER = "#d99a2b"
GRAY = "#5a6475"

TAG_COLORS = {"Success": GREEN, "Miss": AMBER, "Break": "#e8793a", "Rejected": RED, "Abandoned": "#9aa5b8"}
TOAST_COLORS = {"info": "#2f6fed", "success": "#238a54", "warning": "#b9801f", "error": "#c93a3f"}


def stylesheet() -> str:
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
    QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
    QFrame#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
    QLabel#brand {{ font-size: 20px; font-weight: 700; padding: 6px 4px 0 4px; background: transparent; }}
    QLabel#brandSub {{ color: {MUTED}; font-size: 11px; padding: 0 4px 10px 4px; background: transparent; }}
    QPushButton {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px; }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton:pressed {{ background: {BORDER}; }}
    QPushButton:disabled {{ color: {GRAY}; }}
    QPushButton#navButton {{ text-align: left; padding: 10px 12px; background: transparent; border: 1px solid transparent; }}
    QPushButton#navButton:hover {{ background: {PANEL_ALT}; border-color: {BORDER}; }}
    QPushButton#runButton {{ background: {GREEN}; color: white; font-weight: 700; text-align: left; padding: 11px 12px; border: none; }}
    QPushButton#runButton[running="true"] {{ background: {AMBER}; }}
    QPushButton#navButton:checked {{ background: {PANEL_ALT}; border-color: {ACCENT}; }}
    QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; background: transparent; }}
    QLabel#counter {{ color: {MUTED}; background: transparent; }}
    QLabel#muted {{ color: {MUTED}; background: transparent; }}
    QLabel#groupCaption {{ color: {MUTED}; font-size: 11px; font-weight: 600; background: transparent; padding-top: 4px; }}
    QLabel#dbChip {{ border-radius: 9px; padding: 3px 9px; font-size: 11px; font-weight: 600; }}
    QLabel#dbChip[ok="true"] {{ background: #173a2a; color: #6fdca2; }}
    QLabel#dbChip[ok="false"] {{ background: #4a1f22; color: #ff8d92; }}
    QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
    QFrame#panel QLabel {{ background: transparent; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QWidget#flowHost {{ background: transparent; }}
    QFrame#agentCard {{ border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }}
    QFrame#agentCard QLabel {{ background: transparent; color: white; }}
    QFrame#agentCard[state="available"] {{ background: {GREEN}; }}
    QFrame#agentCard[state="busy"] {{ background: {RED}; }}
    QFrame#agentCard[state="break"] {{ background: {AMBER}; }}
    QFrame#agentCard[state="offline"] {{ background: {GRAY}; }}
    QListView, QTableView {{ background: {PANEL}; alternate-background-color: {PANEL_ALT}; border: none;
                             gridline-color: {BORDER}; selection-background-color: #2b4a80; }}
    QHeaderView::section {{ background: {PANEL_ALT}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
                            padding: 6px 8px; font-weight: 600; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{ background: {PANEL_ALT}; border: 1px solid {BORDER};
                            border-radius: 6px; padding: 5px 8px; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ background: {PANEL}; padding: 8px 14px; border: 1px solid {BORDER}; border-bottom: none;
                    border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: {PANEL_ALT}; color: {ACCENT}; }}
    QMenu {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: #2b4a80; }}
    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    """
