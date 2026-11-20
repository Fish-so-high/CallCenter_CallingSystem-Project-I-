# --- Surfaces ---
BG = "#ebeef1"           
PANEL = "#ffffff"       
PANEL_ALT = "#f4f7fb"   
BORDER = "#d8e0ea"      

# --- Text ---
TEXT = "#19191a"      
TEXT_SECONDARY = "#4b5563" 
MUTED = "#7c889b"       

# --- Accents & Semantics ---
ACCENT = "#4c8dff"     
ACCENT_HOVER = "#3578eb"
ACCENT_SOFT = "#e6f0ff" 

GREEN = "#22a05b"       # Thành công / Online 
RED = "#e5484d"         # Lỗi / Busy 
AMBER = "#d99a2b"       # Cảnh báo / Ringing
GRAY = "#5a6475"        # Trung tính / Offline

TAG_COLORS = {"Success": GREEN, "Miss": AMBER, "Break": "#e8793a", "Rejected": RED, "Abandoned": "#9aa5b8"}
TOAST_COLORS = {"info": "#2f6fed", "success": "#238a54", "warning": "#b9801f", "error": "#c93a3f"}


def stylesheet() -> str:
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
    QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}
    
    /* --- Sidebar & Brand --- */
    QFrame#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
    QLabel#brand {{ font-size: 20px; font-weight: 700; color: {ACCENT}; padding: 6px 4px 0 4px; background: transparent; }}
    QLabel#brandSub {{ color: {TEXT_SECONDARY}; font-size: 11px; padding: 0 4px 10px 4px; background: transparent; }}
    
    /* --- Buttons --- */
    QPushButton {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px; font-weight: 500; }}
    QPushButton:hover {{ background: {PANEL_ALT}; border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
    QPushButton:pressed {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
    QPushButton:disabled {{ background: {PANEL_ALT}; color: {MUTED}; border-color: {BORDER}; }}
    
    /* Nav Buttons tận dụng ACCENT_SOFT và ACCENT_HOVER */
    QPushButton#navButton {{ text-align: left; padding: 10px 12px; color: {TEXT_SECONDARY}; background: transparent; border: 1px solid transparent; }}
    QPushButton#navButton:hover {{ background: {PANEL_ALT}; color: {TEXT}; border-color: {BORDER}; }}
    QPushButton#navButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; border-color: {ACCENT}; font-weight: 600; }}
    
    QPushButton#runButton {{ background: {GREEN}; color: white; font-weight: 700; text-align: left; padding: 11px 12px; border: none; }}
    QPushButton#runButton:hover {{ background: #1c8a4e; }}
    QPushButton#runButton[running="true"] {{ background: {AMBER}; }}
    QPushButton#runButton[running="true"]:hover {{ background: #c28722; }}
    
    /* --- Typography & Badges --- */
    QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; color: {TEXT}; background: transparent; }}
    QLabel#counter {{ color: {TEXT_SECONDARY}; font-weight: 600; background: transparent; }}
    QLabel#muted {{ color: {MUTED}; background: transparent; }}
    QLabel#groupCaption {{ color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 700; background: transparent; padding-top: 4px; }}
    
    /* Chuyển dbChip sang nền pastel sáng cho hợp Light Mode */
    QLabel#dbChip {{ border-radius: 9px; padding: 3px 9px; font-size: 11px; font-weight: 600; }}
    QLabel#dbChip[ok="true"] {{ background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }}
    QLabel#dbChip[ok="false"] {{ background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }}
    
    /* --- Panels & Cards --- */
    QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
    QFrame#panel QLabel {{ background: transparent; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QWidget#flowHost {{ background: transparent; }}
    
    QFrame#agentCard {{ border-radius: 10px; border: 1px solid rgba(0, 0, 0, 0.08); }}
    QFrame#agentCard QLabel {{ background: transparent; color: white; }}
    QFrame#agentCard[state="available"] {{ background: {GREEN}; }}
    QFrame#agentCard[state="busy"] {{ background: {RED}; }}
    QFrame#agentCard[state="break"] {{ background: {AMBER}; }}
    QFrame#agentCard[state="offline"] {{ background: {GRAY}; }}
    
    /* --- Tables & Lists --- */
    QListView, QTableView {{ background: {PANEL}; alternate-background-color: {PANEL_ALT}; color: {TEXT}; border: none;
                             gridline-color: {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_HOVER}; }}
    QHeaderView::section {{ background: {PANEL_ALT}; color: {TEXT_SECONDARY}; border: none; border-bottom: 1px solid {BORDER};
                            padding: 6px 8px; font-weight: 700; }}
    
    /* --- Inputs --- */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
                            border-radius: 6px; padding: 5px 8px; }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QDateEdit:hover {{ border-color: {MUTED}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{ background: {PANEL}; border: 1.5px solid {ACCENT}; }}
    
    /* --- Tabs & Menus --- */
    QTabWidget::pane {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ background: {PANEL_ALT}; color: {TEXT_SECONDARY}; padding: 8px 14px; border: 1px solid {BORDER}; border-bottom: none;
                    border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }}
    QTabBar::tab:hover {{ color: {TEXT}; background: {ACCENT_SOFT}; }}
    QTabBar::tab:selected {{ background: {PANEL}; color: {ACCENT_HOVER}; font-weight: 600; border-bottom: 2px solid {ACCENT}; }}
    
    QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; font-weight: 600; }}
    
    /* --- Splitter & Scrollbar --- */
    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    """
