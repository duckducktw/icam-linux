"""iCam：Linux 原生網路監視器多路監看（取代 SoCatch / iWatchDVR，不需 Wine）。"""

import importlib.util
import os
import sys


def reexec_with_bundled_qt():
    """系統 Qt 與 PySide6 內建 Qt 版本衝突時（libQt6DBus undefined symbol），
    把自己重新啟動，並讓動態連結優先找 PySide6 自帶的 Qt 函式庫。"""
    if os.environ.get("ICAM_QT_LIBS"):
        raise
    spec = importlib.util.find_spec("PySide6")
    origin = getattr(spec, "origin", None)
    if not origin:
        raise
    qdir = os.path.join(os.path.dirname(origin), "Qt", "lib")
    if not os.path.isdir(qdir):
        raise
    env = dict(os.environ)
    env["ICAM_QT_LIBS"] = "1"
    old = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = qdir + (":" + old if old else "")
    args = [sys.executable, "-m", "icam"] + sys.argv[1:]
    os.execve(sys.executable, args, env)
