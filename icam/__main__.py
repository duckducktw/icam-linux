"""iCam 進入點。

用法：
  icam                     啟動監看（讀 ~/.config/icam-linux/cameras.json）
  icam --add NAME URL      加入一路鏡頭後啟動
  icam --scan              只列印區網 ONVIF 裝置
  icam --shot PATH         無介面測試：抓一張畫面存檔後結束
"""

import sys
import time

from PySide6.QtCore import QTimer

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    from . import reexec_with_bundled_qt
    reexec_with_bundled_qt()
    raise

from . import config, onvif
from .ui import MainWindow


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = config.load()

    if "--scan" in argv:
        devs = onvif.discover()
        for d in devs:
            print("%-16s %-24s %-12s %s" % (d["ip"], d["name"], d["hardware"], d["xaddr"]))
        if not devs:
            print("沒有找到 ONVIF 裝置")
        return 0

    shot = None
    if "--shot" in argv:
        i = argv.index("--shot")
        shot = argv[i + 1] if i + 1 < len(argv) else "/tmp/icam.png"
        del argv[i:i + 2]

    if "--add" in argv:
        i = argv.index("--add")
        if i + 2 < len(argv):
            name, url = argv[i + 1], argv[i + 2]
            cfg["cameras"].append({"name": name, "url": url, "transport": "tcp"})
            config.save(cfg)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("iCam")
    win = MainWindow(cfg)
    win.show()

    if shot:
        def grab():
            win.grab().save(shot, "PNG")
            app.quit()
        QTimer.singleShot(6000, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
