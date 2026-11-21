"""應用程式圖示：優先用系統原生主題（KDE Breeze / GNOME），沒有才用內建 SVG。"""

import os

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 依序找系統主題裡的監視器圖示（Breeze: camera-video / camera-web）
THEME_NAMES = (
    "camera-video",
    "camera-web",
    "camera-surveillance",
    "video-display",
    "camera-photo",
)

FALLBACK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="4" y="14" width="40" height="28" rx="5" fill="#2f6fed"/>
  <rect x="8" y="18" width="32" height="20" rx="3" fill="#dbe7ff"/>
  <path d="M46 24l14-7v22l-14-7z" fill="#2f6fed"/>
  <rect x="18" y="46" width="12" height="4" rx="2" fill="#2f6fed"/>
  <rect x="14" y="50" width="20" height="4" rx="2" fill="#2f6fed"/>
  <circle cx="40" cy="20" r="2.5" fill="#ff5c5c"/>
</svg>"""


def _fallback_icon() -> QIcon:
    renderer = QSvgRenderer(QByteArray(FALLBACK_SVG.encode()))
    icon = QIcon()
    for size in (16, 22, 32, 48, 64, 128, 256):
        pm = QPixmap(QSize(size, size))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        icon.addPixmap(pm)
    return icon


def app_icon() -> QIcon:
    for name in THEME_NAMES:
        ic = QIcon.fromTheme(name)
        if not ic.isNull() and not ic.availableSizes() == []:
            return ic
    return _fallback_icon()


def svg_path():
    """給 .desktop 用的檔案路徑（沒有就 None）。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "assets", "icam.svg")
    return p if os.path.exists(p) else None
