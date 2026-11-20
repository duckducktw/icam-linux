"""單路影像解碼：用 ffmpeg 子程序把影像解成 rgb24，丟給 UI 畫。

支援三種來源：
  rtsp:// http:// 檔案路徑   ── ffmpeg 直接讀
  dvr://<通道>              ── iCatch DVR 的私有 CGI 串流，先自行拆流再餵 ffmpeg
"""

import os
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from . import dvr as dvrmod
from .mediautil import ffmpeg_bin


class StreamWorker(QThread):
    """持續解碼一路；斷線自動重連。"""

    frameReady = Signal(QImage)
    stateChanged = Signal(str)

    def __init__(self, url, width=640, height=360, transport="tcp", fps=15,
                 dvr_profile=None, parent=None):
        super().__init__(parent)
        self.url = url
        self.width = width
        self.height = height
        self.transport = transport
        self.fps = fps
        self.dvr_profile = dvr_profile or {}
        self._stop = False
        self._proc = None
        self._feeder = None
        self._frames_sent = 0
        self._expected = 0
        self._low_warned = False
        self._finish_label = None

    # ---- 來源型別 ----
    @property
    def is_dvr(self):
        return self.url.lower().startswith("dvr://")

    def dvr_channel(self):
        tail = self.url.split("://", 1)[1]
        return int(tail.split("?")[0].split("/")[0] or 0)

    def dvr_range(self):
        """回放網址 dvr://0?beg=<epoch>&end=<epoch> → (beg, end) 或 (None, None)。"""
        if "?" not in self.url:
            return None, None
        from urllib.parse import parse_qs
        q = parse_qs(self.url.split("?", 1)[1])
        try:
            beg = int(q.get("beg", [""])[0])
            end = int(q.get("end", [""])[0])
        except ValueError:
            return None, None
        return beg, end


    # ---- ffmpeg 參數 ----
    def _vf(self):
        return ("scale=w=%d:h=%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d"
                % (self.width, self.height, self.width, self.height, self.fps))

    def _args(self):
        args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.is_dvr:
            # 自行拆好的 Annex-B H.264 從 stdin 進來；DVR 沒給時戳，用系統時鐘補
            args += ["-fflags", "+genpts+igndts", "-use_wallclock_as_timestamps", "1",
                     "-probesize", "40000", "-analyzeduration", "0",
                     "-f", "h264", "-i", "pipe:0"]
        else:
            local = not self.url.lower().startswith(
                ("rtsp://", "http://", "https://", "rtmp://"))
            if local:
                args += ["-re", "-stream_loop", "-1"]
            else:
                args += ["-rtsp_transport", self.transport, "-rtsp_flags", "prefer_tcp",
                         "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay",
                         "-probesize", "500000", "-analyzeduration", "500000"]
            args += ["-i", self.url]
        args += ["-an", "-vf", self._vf(), "-pix_fmt", "rgb24",
                 "-f", "rawvideo", "pipe:1"]
        return args

    # ---- 主迴圈 ----
    def run(self):
        if not ffmpeg_bin():
            self.stateChanged.emit("缺少 ffmpeg")
            return
        need = self.width * self.height * 3
        while not self._stop:
            self.stateChanged.emit("連線中")
            try:
                self._proc = subprocess.Popen(
                    self._args(),
                    stdin=subprocess.PIPE if self.is_dvr else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=need * 2)
            except OSError as e:
                self.stateChanged.emit("啟動失敗: %s" % e)
                return
            if self.is_dvr:
                self._feeder = threading.Thread(target=self._feed_dvr, daemon=True)
                self._feeder.start()
            buf = bytearray()
            got_any = False
            t_first = None
            while not self._stop:
                chunk = self._proc.stdout.read(need - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= need:
                    img = QImage(bytes(buf[:need]), self.width, self.height,
                                 self.width * 3, QImage.Format_RGB888).copy()
                    buf.clear()
                    if not got_any:
                        got_any = True
                        t_first = time.time()
                        self.stateChanged.emit("播放中（訊號極弱）" if self._low_warned
                                               else "播放中")
                    self.frameReady.emit(img)
            self._kill()
            if self._stop:
                break
            self.stateChanged.emit("重連中")
            for _ in range(20):
                if self._stop:
                    break
                time.sleep(0.1)
        self.stateChanged.emit(self._finish_label or "已停止")

    # ---- DVR 拆流 → ffmpeg stdin ----
    def _feed_dvr(self):
        p = self.dvr_profile
        ch = self.dvr_channel()
        beg, end = self.dvr_range()
        pace = float(p.get("pace_fps", 0) or 0) if beg is not None else 0.0
        proc = self._proc
        if not p.get("host") or not proc or not proc.stdin:
            return
        if beg is not None and end is not None and pace > 0:
            self._expected = int((end - beg) * pace)
        seg_secs = int(p.get("seg_secs", 300) or 300)
        wrote = [False]
        sent = [0]
        t_start = [None]
        real = [0]
        real_t0 = [time.monotonic()]
        low_warn = [False]

        def feeding():
            for body in dvrmod.pull_bodies(
                    p, ch, beg, end, seg_secs=seg_secs, fps=pace or 20.0,
                    should_stop=lambda: self._stop or proc.poll() is not None):
                for stype, channel, _w, _h, payload in dvrmod.iter_sub_chunks(body):
                    if channel != ch or stype not in dvrmod.VIDEO_TYPES or not payload:
                        continue
                    try:
                        proc.stdin.write(payload)
                        proc.stdin.flush()
                        wrote[0] = True
                    except (BrokenPipeError, ValueError, OSError):
                        return
                    real[0] += 1
                    if not low_warn[0] and beg is None:
                        el = time.monotonic() - real_t0[0]
                        if el > 12 and real[0] / el < 1.5:
                            low_warn[0] = True
                            self._low_warned = True
                            self.stateChanged.emit("無訊號（這路只有 %.1f fps）"
                                                   % (real[0] / el))
                    if pace <= 0:
                        continue
                    # 回放：DVR 送得比實際時間快，靠張數配速到 pace_fps
                    now = time.monotonic()
                    if t_start[0] is None:
                        t_start[0] = now
                    sent[0] += 1
                    self._frames_sent = sent[0]
                    target = t_start[0] + sent[0] / pace
                    if target > now:
                        time.sleep(target - now)

        try:
            feeding()
        except Exception as e:
            if not self._stop:
                self.stateChanged.emit("DVR 斷線: %s" % e)
        finally:
            if beg is not None:
                # 回放段落播完（或整段沒錄影）→ 先定好結束標籤，再關 stdin 讓 ffmpeg 收尾
                self._finish_label = "回放結束" if wrote[0] else "這段沒有錄影"
            try:
                proc.stdin.close()
            except Exception:
                pass
            if beg is not None:
                self._stop = True
            elif not wrote[0] and not self._stop:
                self.stateChanged.emit("DVR 沒畫面")

    def _kill(self):
        p, self._proc = self._proc, None
        if p:
            try:
                if p.stdin:
                    p.stdin.close()
            except Exception:
                pass
            if p.poll() is None:
                try:
                    p.kill()
                    p.wait(timeout=3)
                except Exception:
                    pass

    def stop(self):
        self._stop = True
        self._kill()
        self.wait(3000)
