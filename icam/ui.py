"""iCam 介面：多路監看格狀畫面、ONVIF 探索、截圖、錄影。"""

import datetime as _dt
import os
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QDateTime, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
    QWidget, QComboBox, QDateTimeEdit,
)

from . import config, onvif
from .player import StreamWorker, ffmpeg_bin

HINT = "雙擊放大 · F 全螢幕 · S 截圖 · R 錄影 · Delete 移除"


class _Worker(QThread):
    """把會卡住 UI 的阻塞呼叫（ONVIF 掃描、取流、連線測試）丟到背景跑。"""

    done = Signal(object, object)   # (結果, 錯誤訊息或 None)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:      # noqa: BLE001 - 一律回報給 UI
            self.done.emit(None, str(e))


class CameraTile(QWidget):
    def __init__(self, cam, cfg, parent=None):
        super().__init__(parent)
        self.cam = cam
        self.cfg = cfg
        self.setMinimumSize(240, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(False)
        self.selected = False
        self.maximized = False
        self._locked_state = None
        self.image = None
        self.state = "待機"
        self._rec = None
        self._rec_path = None
        w, h = cfg.get("tile", [640, 360])
        self.worker = StreamWorker(cam["url"], w, h,
                                   cam.get("transport", cfg.get("transport", "tcp")),
                                   cfg.get("fps", 15), dvr_profile=cfg.get("dvr"), parent=self)
        self.worker.frameReady.connect(self._on_frame)
        self.worker.stateChanged.connect(self._on_state)

    # ---- 播放 ----
    def start(self):
        if not self.worker.isRunning():
            self.worker.start()

    def stop(self):
        self.worker.stop()
        self.stop_record()

    def _on_frame(self, img):
        self.image = img
        self.update()

    def _on_state(self, txt):
        if self._locked_state:
            return          # 結束標籤已鎖定（避免收尾訊號蓋掉「回放結束」）
        self.state = txt
        self.update()

    def lock_state(self, txt):
        self._locked_state = txt
        self.state = txt
        self.update()

    # ---- 畫面 ----
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0f12"))
        if self.image:
            pix = QPixmap.fromImage(self.image)
            scaled = pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - scaled.width()) // 2,
                         (self.height() - scaled.height()) // 2, scaled)
        else:
            p.setPen(QColor("#4a5560"))
            p.setFont(QFont(p.font().family(), 10))
            p.drawText(self.rect(), Qt.AlignCenter, self.state)
        # 名稱條
        bar = self.rect().adjusted(0, 0, 0, -(self.height() - 26))
        p.fillRect(bar, QColor(0, 0, 0, 150))
        p.setPen(QColor("#e6e6e6"))
        f = QFont(p.font().family(), 9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(bar.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, self.cam.get("name", ""))
        right = []
        if self._rec:
            right.append("● REC")
        if self.state not in ("播放中", ""):
            right.append(self.state)
        if right:
            p.setPen(QColor("#ff6b6b") if self._rec else QColor("#9aa4ae"))
            p.drawText(bar.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight, "  ".join(right))
        if self.selected:
            p.setPen(QColor("#3aa0ff"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    # ---- 事件 ----
    def mousePressEvent(self, ev):
        self.setFocus(Qt.MouseFocusReason)
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        win = self.window()
        if hasattr(win, "toggle_maximize"):
            win.toggle_maximize(self)
        ev.accept()

    # ---- 截圖 / 錄影 ----
    def snapshot(self):
        if not self.image:
            return None
        d = Path(self.cfg.get("snapshot_dir", Path.home() / "Pictures" / "icam"))
        d.mkdir(parents=True, exist_ok=True)
        name = "%s_%s.png" % (self.cam.get("name", "cam").replace("/", "_"),
                              _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        out = d / name
        self.image.save(str(out), "PNG")
        return out

    def toggle_record(self):
        if self._rec:
            self.stop_record()
            return self._rec_path
        if not ffmpeg_bin():
            return None
        d = Path(self.cfg.get("snapshot_dir", Path.home() / "Pictures" / "icam")).parent / "icam-rec"
        d.mkdir(parents=True, exist_ok=True)
        out = d / ("%s_%s.mp4" % (self.cam.get("name", "cam").replace("/", "_"),
                                  _dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
        args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.cam["url"].lower().startswith("rtsp://"):
            args += ["-rtsp_transport", "tcp"]
        args += ["-i", self.cam["url"], "-c", "copy", "-y", str(out)]
        try:
            self._rec = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._rec_path = out
        except OSError:
            self._rec = None
            self._rec_path = None
        self.update()
        return self._rec_path

    def stop_record(self):
        p, self._rec = self._rec, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()
        self.update()


class CameraDialog(QDialog):
    def __init__(self, parent=None, cam=None):
        super().__init__(parent)
        self.setWindowTitle("鏡頭設定")
        self.resize(520, 200)
        form = QFormLayout(self)
        self.name = QLineEdit((cam or {}).get("name", ""))
        self.url = QLineEdit((cam or {}).get("url", ""))
        self.url.setPlaceholderText("rtsp://帳號:密碼@IP:554/路徑")
        self.transport = QComboBox()
        self.transport.addItems(["tcp", "udp"])
        self.transport.setCurrentText((cam or {}).get("transport", "tcp"))
        form.addRow("名稱", self.name)
        form.addRow("RTSP 網址", self.url)
        form.addRow("傳輸", self.transport)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def value(self):
        return {"name": self.name.text().strip() or "鏡頭",
                "url": self.url.text().strip(),
                "transport": self.transport.currentText()}


class DiscoverDialog(QDialog):
    """掃描區網 ONVIF 裝置，輸入帳密後直接取得 RTSP 網址。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ONVIF 探索")
        self.resize(640, 430)
        self.picked = None
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.user = QLineEdit()
        self.user.setPlaceholderText("帳號")
        self.pwd = QLineEdit()
        self.pwd.setPlaceholderText("密碼")
        self.pwd.setEchoMode(QLineEdit.Password)
        row.addWidget(self.user)
        row.addWidget(self.pwd)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.btn_scan = QPushButton("重新掃描")
        self.btn_scan.clicked.connect(self.scan)
        self.btn_use = QPushButton("取流並加入")
        self.btn_use.clicked.connect(self.use_selected)
        self.btn_manual = QPushButton("手動網址")
        self.btn_manual.clicked.connect(self.manual)
        row2.addWidget(self.btn_scan)
        row2.addStretch(1)
        row2.addWidget(self.btn_manual)
        row2.addWidget(self.btn_use)
        lay.addLayout(row2)
        self.status = QLabel("")
        lay.addWidget(self.status)
        QTimer.singleShot(50, self.scan)

    def scan(self):
        self.status.setText("掃描中…（約 5 秒，可繼續操作視窗）")
        self.btn_scan.setEnabled(False)
        self.list.clear()
        self._scan = _Worker(lambda: onvif.discover(), self)
        self._scan.done.connect(self._scan_done)
        self._scan.start()

    def _scan_done(self, devs, err):
        if err:
            self.status.setText("掃描失敗: %s" % err)
            self.btn_scan.setEnabled(True)
            return
        for d in devs or []:
            item = QListWidgetItem("%s  %s  %s" % (d["ip"], d["name"], d["hardware"]))
            item.setData(Qt.UserRole, d)
            self.list.addItem(item)
        if devs:
            self.list.setCurrentRow(0)
            self.status.setText("找到 %d 台" % len(devs))
        else:
            self.status.setText("沒找到 ONVIF 裝置（有線/無線不同網段時掃不到）")
        self.btn_scan.setEnabled(True)

    def use_selected(self):
        item = self.list.currentItem()
        if not item:
            self.status.setText("先選一台")
            return
        d = item.data(Qt.UserRole)
        self.status.setText("取流中…")
        xaddr = d["xaddr"] or "http://%s/onvif/device_service" % d["ip"]
        user, pwd = self.user.text().strip(), self.pwd.text()
        self.btn_use.setEnabled(False)
        self._use = _Worker(lambda: onvif.get_stream_uri(xaddr, user, pwd), self)
        self._use.done.connect(self._use_done)
        self._use.start()

    def _use_done(self, res, err):
        self.btn_use.setEnabled(True)
        if err:
            self.status.setText("取流失敗: %s" % err)
            return
        uri, msg = res
        if not uri:
            self.status.setText("取流失敗: %s（可改用手動網址）" % msg)
            return
        self.picked = {"name": self.list.currentItem().data(Qt.UserRole)["name"] or "ONVIF",
                       "url": uri, "transport": "tcp"}
        self.accept()

    def manual(self):
        dlg = CameraDialog(self)
        if dlg.exec():
            self.picked = dlg.value()
            self.accept()


class DvrDialog(QDialog):
    """iCatch DVR（SoCatch 那台）設定：主機、帳密、自動接上所有通道。"""

    def __init__(self, parent=None, dvr=None):
        super().__init__(parent)
        self.setWindowTitle("DVR 主機（iCatch / SoCatch）")
        self.resize(460, 260)
        dvr = dvr or {}
        form = QFormLayout(self)
        self.host = QLineEdit(dvr.get("host", ""))
        self.host.setPlaceholderText("192.168.0.150")
        self.port = QLineEdit(str(dvr.get("port", 80)))
        self.user = QLineEdit(dvr.get("user", ""))
        self.pwd = QLineEdit(dvr.get("password", ""))
        self.pwd.setEchoMode(QLineEdit.Password)
        self.channels = QLineEdit(str(dvr.get("channels", 4)))
        form.addRow("主機 IP", self.host)
        form.addRow("HTTP 埠", self.port)
        form.addRow("帳號", self.user)
        form.addRow("密碼", self.pwd)
        form.addRow("通道數", self.channels)
        row = QHBoxLayout()
        self.btn_test = QPushButton("測試連線")
        self.btn_test.clicked.connect(self.test)
        row.addWidget(self.btn_test)
        form.addRow(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        form.addRow(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def profile(self):
        return {"host": self.host.text().strip(),
                "port": int(self.port.text().strip() or 80),
                "user": self.user.text().strip(),
                "password": self.pwd.text(),
                "channels": int(self.channels.text().strip() or 0)}

    def test(self):
        p = self.profile()
        self.status.setText("測試中…")
        self.btn_test.setEnabled(False)
        self._t = _Worker(lambda: onvif_dvr_probe(p), self)
        self._t.done.connect(lambda res, err: self._test_done(p, res, err))
        self._t.start()

    def _test_done(self, p, res, err):
        self.btn_test.setEnabled(True)
        if err:
            self.status.setText("失敗：%s" % err)
            return
        ok, msg = res
        if ok and p["channels"] <= 0 and "通道: " in msg:
            nums = [x for x in msg.split("通道: ", 1)[1].split(",") if x.strip().isdigit()]
            if nums:
                self.channels.setText(str(max(int(n) for n in nums) + 1))
        self.status.setText(("成功：" if ok else "失敗：") + msg)


def onvif_dvr_probe(p):
    from . import dvr as dvrmod
    return dvrmod.probe(p["host"], p.get("port", 80), p.get("user"), p.get("password"),
                        scheme=p.get("scheme", "http"))


class PlaybackWindow(QDialog):
    """回放單一通道的一段錄影（DVR 的 beg/end 參數），可拖時間軸、可匯出 mp4。"""

    def __init__(self, parent, dvr_profile, channel, beg, end, pace_fps=20):
        super().__init__(parent)
        self.setWindowTitle("回放 CH%d  %s" % (channel + 1,
                            _dt.datetime.fromtimestamp(beg).strftime("%Y-%m-%d %H:%M:%S")))
        self.resize(1000, 640)
        self.profile = dict(dvr_profile)
        self.profile["pace_fps"] = pace_fps
        self.channel = channel
        self.beg = int(beg)
        self.end = int(end)
        self.dur = max(1, self.end - self.beg)
        self.offset = 0
        self.base_offset = 0
        self.t0 = None
        self.paused = False

        lay = QVBoxLayout(self)
        self.cfg = {"cameras": [], "dvr": self.profile, "tile": [960, 540],
                    "fps": pace_fps, "snapshot_dir": str(Path.home() / "Pictures" / "icam")}
        cam = {"name": "CH%d" % (channel + 1), "url": self._url(0)}
        self.tile = CameraTile(cam, self.cfg)
        lay.addWidget(self.tile, 1)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.dur)
        self.slider.sliderReleased.connect(self.seek)
        self.lbl = QLabel("")
        self.btn_play = QPushButton("暫停")
        self.btn_play.clicked.connect(self.toggle_pause)
        self.btn_export = QPushButton("匯出這段")
        self.btn_export.clicked.connect(self.export)
        row.addWidget(self.slider, 1)
        row.addWidget(self.lbl)
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_export)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.t0 = time.time()
        self.tile.start()

    def closeEvent(self, ev):
        self.timer.stop()
        self.tile.stop()
        super().closeEvent(ev)

    def _url(self, offset):
        return "dvr://%d?beg=%d&end=%d" % (self.channel, self.beg + int(offset), self.end)

    def _label(self):
        cur = _dt.datetime.fromtimestamp(self.beg + self.offset)
        return "%s / %s" % (cur.strftime("%H:%M:%S"), _fmt_dur(self.dur))

    def tick(self):
        if not self.paused and self.t0 is not None:
            self.offset = min(self.dur, int(time.time() - self.t0) + self.base_offset)
            self.slider.setValue(self.offset)
            if self.offset >= self.dur:
                self.pause()
                self.tile.lock_state("回放結束")
        self.lbl.setText(self._label())

    def seek(self):
        self.base_offset = int(self.slider.value())
        self.restart()

    def restart(self):
        self.tile._locked_state = None
        self.tile.stop()
        self.tile.cam["url"] = self._url(self.base_offset)
        self.tile.worker.url = self.tile.cam["url"]
        self.tile.worker._stop = False
        self.offset = self.base_offset
        self.t0 = time.time()
        self.paused = False
        self.btn_play.setText("暫停")
        self.tile.start()

    def pause(self):
        self.paused = True
        self.tile.stop()
        self.btn_play.setText("播放")

    def toggle_pause(self):
        if self.paused:
            self.base_offset = self.offset
            self.restart()
        else:
            self.pause()

    def export(self):
        d = Path.home() / "Pictures" / "icam-rec"
        d.mkdir(parents=True, exist_ok=True)
        out = d / ("CH%d_%s.mp4" % (self.channel + 1,
                   _dt.datetime.fromtimestamp(self.beg).strftime("%Y%m%d_%H%M%S")))
        prog = QProgressDialog("匯出中…", "取消", 0, 100, self)
        prog.setWindowTitle("匯出錄影")
        prog.setMinimumDuration(0)
        prog.setValue(0)
        holder = {}

        def work():
            from .export import export_range

            def cb(done, total, msg):
                holder["pct"] = int(done * 100 / max(1, total))
                holder["msg"] = msg
            ok, msg = export_range(self.profile, self.channel, self.beg, self.end, out,
                                   fps=int(self.profile.get("pace_fps", 20)), progress=cb,
                                   should_stop=lambda: prog.wasCanceled())
            holder["result"] = (ok, msg)

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            if "pct" in holder:
                prog.setValue(holder["pct"])
            time.sleep(0.1)
            if prog.wasCanceled():
                break
        prog.close()
        ok, msg = holder.get("result", (False, "已取消"))
        QMessageBox.information(self, "匯出結果", ("%s\n%s" % (msg, out)) if ok else msg)


def _fmt_dur(sec):
    sec = int(sec)
    return "%02d:%02d" % (sec // 60, sec % 60)


class PlaybackDialog(QDialog):
    """挑通道、挑時間、看回放或匯出。"""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("回放")
        self.resize(440, 230)
        form = QFormLayout(self)
        self.ch = QComboBox()
        n = int((cfg.get("dvr") or {}).get("channels") or
                len([c for c in cfg.get("cameras", []) if c["url"].startswith("dvr://")]) or 4)
        for i in range(max(1, n)):
            self.ch.addItem("CH%d" % (i + 1), i)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(-600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dur = QComboBox()
        for label, secs in (("1 分鐘", 60), ("5 分鐘", 300), ("10 分鐘", 600),
                            ("30 分鐘", 1800), ("1 小時", 3600)):
            self.dur.addItem(label, secs)
        self.dur.setCurrentIndex(1)
        form.addRow("通道", self.ch)
        form.addRow("起始時間", self.when)
        form.addRow("長度", self.dur)
        row = QHBoxLayout()
        self.btn_play = QPushButton("播放")
        self.btn_play.clicked.connect(self.open_playback)
        row.addWidget(self.btn_play)
        form.addRow(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.reject)
        form.addRow(bb)

    def open_playback(self):
        beg = int(self.when.dateTime().toSecsSinceEpoch())
        end = beg + int(self.dur.currentData())
        win = PlaybackWindow(self.parent(), self.cfg.get("dvr") or {},
                             int(self.ch.currentData()), beg, end)
        win.exec()


class MainWindow(QMainWindow):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.tiles = []
        self.maximized_tile = None
        self.setWindowTitle("iCam")
        self.resize(1280, 760)

        central = QWidget()
        self.grid = QGridLayout(central)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.grid.setSpacing(6)
        self.setCentralWidget(central)

        bar = self.addToolBar("main")
        bar.setMovable(False)
        for text, slot in (("DVR 主機", self.setup_dvr), ("回放", self.open_playback),
                           ("探索", self.open_discover), ("新增", self.add_camera),
                           ("編輯", self.edit_camera), ("移除", self.remove_camera),
                           ("截圖", self.snapshot), ("錄影", self.toggle_record),
                           ("重新連線", self.reload_all)):
            act = bar.addAction(text)
            act.triggered.connect(slot)
        self.statusBar().showMessage(HINT)

        QShortcut(QKeySequence("F"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("S"), self, activated=self.snapshot)
        QShortcut(QKeySequence("R"), self, activated=self.toggle_record)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.reload_all)
        QShortcut(QKeySequence("Escape"), self, self.exit_fullscreen)
        QShortcut(QKeySequence("Delete"), self, self.remove_camera)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

        self.build_tiles()

    # ---- 版面 ----
    def build_tiles(self, restart=True):
        for t in self.tiles:
            t.stop()
            t.setParent(None)
        self.tiles = []
        for cam in self.cfg["cameras"]:
            t = CameraTile(cam, self.cfg)
            self.tiles.append(t)
        self.layout_tiles()
        if restart:
            for t in self.tiles:
                t.start()

    def layout_tiles(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(self.centralWidget())
        shown = [self.maximized_tile] if self.maximized_tile else self.tiles
        n = len(shown)
        if not n:
            return
        cols = 1 if n == 1 else (2 if n <= 4 else (3 if n <= 9 else 4))
        rows = (n + cols - 1) // cols
        for i, t in enumerate(shown):
            self.grid.addWidget(t, i // cols, i % cols)
            t.show()
        for i in range(rows):
            self.grid.setRowStretch(i, 1)
        for j in range(cols):
            self.grid.setColumnStretch(j, 1)
        for t in self.tiles:
            if t not in shown:
                t.hide()

    def current_tile(self):
        if self.maximized_tile:
            return self.maximized_tile
        for t in self.tiles:
            if t.selected:
                return t
        for t in self.tiles:
            if t.hasFocus():
                return t
        return self.tiles[0] if self.tiles else None

    def toggle_maximize(self, tile):
        self.maximized_tile = None if self.maximized_tile is tile else tile
        for t in self.tiles:
            t.selected = (t is self.maximized_tile)
        self.layout_tiles()

    # ---- 動作 ----
    def reload_all(self):
        for t in self.tiles:
            t.stop()
            t.worker._stop = False
        self.build_tiles()

    def setup_dvr(self):
        dlg = DvrDialog(self, self.cfg.get("dvr"))
        if not dlg.exec():
            return
        prof = dlg.profile()
        if not prof["host"]:
            return
        self.cfg["dvr"] = prof
        n = prof.get("channels") or 1
        existing = {c["url"] for c in self.cfg["cameras"]}
        for i in range(n):
            url = "dvr://%d" % i
            if url in existing:
                continue
            self.cfg["cameras"].append({"name": "CH%d" % (i + 1), "url": url})
        config.save(self.cfg)
        self.build_tiles()
        self.statusBar().showMessage("DVR %s 已設定，%d 路" % (prof["host"], n))

    def open_playback(self):
        if not (self.cfg.get("dvr") or {}).get("host"):
            QMessageBox.information(self, "回放", "先設定 DVR 主機")
            return
        dlg = PlaybackDialog(self, self.cfg)
        dlg.exec()

    def open_discover(self):
        dlg = DiscoverDialog(self)
        if dlg.exec() and dlg.picked:
            self.cfg["cameras"].append(dlg.picked)
            config.save(self.cfg)
            self.build_tiles()
            self.statusBar().showMessage("已加入 %s" % dlg.picked["name"])

    def add_camera(self):
        dlg = CameraDialog(self)
        if dlg.exec():
            v = dlg.value()
            if not v["url"]:
                return
            self.cfg["cameras"].append(v)
            config.save(self.cfg)
            self.build_tiles()

    def edit_camera(self):
        t = self.current_tile()
        if not t:
            return
        dlg = CameraDialog(self, t.cam)
        if dlg.exec():
            t.cam.update(dlg.value())
            config.save(self.cfg)
            self.build_tiles()

    def remove_camera(self):
        t = self.current_tile()
        if not t:
            return
        self.maximized_tile = None
        try:
            self.cfg["cameras"].remove(t.cam)
        except ValueError:
            pass
        config.save(self.cfg)
        self.build_tiles()

    def snapshot(self):
        t = self.current_tile()
        if not t:
            return
        out = t.snapshot()
        self.statusBar().showMessage("截圖：%s" % out if out else "還沒有畫面")

    def toggle_record(self):
        t = self.current_tile()
        if not t:
            return
        if t._rec:
            t.stop_record()
            self.statusBar().showMessage("錄影停止：%s" % t._rec_path)
        else:
            p = t.toggle_record()
            self.statusBar().showMessage("錄影中：%s" % p if p else "錄影失敗")

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def exit_fullscreen(self):
        if self.maximized_tile:
            self.maximized_tile = None
            for t in self.tiles:
                t.selected = False
            self.layout_tiles()
        elif self.isFullScreen():
            self.showNormal()

    def closeEvent(self, ev):
        for t in self.tiles:
            t.stop()
        super().closeEvent(ev)
