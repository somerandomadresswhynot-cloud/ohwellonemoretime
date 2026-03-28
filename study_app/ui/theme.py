DARK_QSS = """
QWidget { background:#121525; color:#dbe4ff; font-family:"Segoe UI","Inter","Arial",sans-serif; font-size:12px; }
QFrame.panel { background:#1a1f35; border:1px solid #2a3150; border-radius:6px; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QListWidget, QTreeWidget, QTableWidget {
    background:#11162b; border:1px solid #2c3558; border-radius:4px; padding:4px;
}
QPushButton { background:#26355f; border:1px solid #41558f; border-radius:4px; padding:6px 10px; }
QPushButton:hover { background:#314677; }
QPushButton#accent { background:#5a48d6; border:1px solid #7b6df0; }
QTabBar::tab { background:#1a1f35; padding:8px 12px; margin-right:2px; }
QTabBar::tab:selected { background:#2a3150; }
QHeaderView::section { background:#202848; padding:4px; border:0; }

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width:0px; height:0px; border:none; }
QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow { width:0px; height:0px; }

QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar:horizontal { background:transparent; height:10px; margin:2px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background:rgba(140,160,205,0.45);
    border-radius:5px;
    min-height:24px;
    min-width:24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background:rgba(170,190,230,0.75); }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
    background:transparent;
    border:none;
    width:0px;
    height:0px;
}
"""
