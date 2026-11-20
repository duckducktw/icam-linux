"""從 DVR 主機匯出指定時段的錄影成 mp4（備份用，全速下載不配速）。"""

import subprocess
import time

import requests

from . import dvr as dvrmod
from .mediautil import ffmpeg_bin


def export_range(profile, channel, beg, end, out_path, fps=20, progress=None,
                 timeout=None, should_stop=None):
    """把 DVR 上 [beg, end] 這段錄影存成 mp4。

    progress(done_frames, total_frames, 說明) 會被週期呼叫；
    回傳 (成功?, 訊息)。
    """
    host = profile.get("host", "")
    port = int(profile.get("port", 80))
    if not host:
        return False, "沒有 DVR 主機設定"
    dur = max(1, int(end) - int(beg))
    total = max(1, int(dur * fps))
    url = dvrmod.build_url(host, channel, port=port, beg=int(beg), end=int(end),
                           scheme=profile.get("scheme", "http"))
    if not ffmpeg_bin():
        return False, "缺少 ffmpeg"
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "h264", "-r", str(fps), "-i", "pipe:0",
         "-c", "copy", "-movflags", "+faststart", "-y", str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    splitter = dvrmod.MultipartSplitter()
    frames = [0]
    stop_at = time.time() + (timeout if timeout else dur * 4 + 60)

    def on_section(body):
        for stype, ch, _w, _h, payload in dvrmod.iter_sub_chunks(body):
            if ch != channel or stype not in dvrmod.VIDEO_TYPES or not payload:
                continue
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return
            frames[0] += 1

    splitter.callback = on_section
    try:
        with requests.get(url, auth=(profile.get("user", ""), profile.get("password", "")),
                          stream=True, timeout=15) as r:
            if r.status_code != 200:
                return False, "DVR 拒絕：HTTP %s" % r.status_code
            last = 0
            for chunk in r.iter_content(16384):
                if should_stop and should_stop():
                    break
                splitter.feed(chunk)
                if frames[0] - last >= 20:
                    last = frames[0]
                    if progress:
                        progress(min(frames[0], total), total, "已收 %d 張" % frames[0])
                if frames[0] >= total or time.time() > stop_at:
                    break
    except Exception as e:
        try:
            proc.stdin.close()
        except Exception:
            pass
        return False, "下載中斷: %s" % e
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    if progress:
        progress(frames[0], total, "完成")
    if frames[0] == 0:
        return False, "那段時間沒有錄影"
    return True, "匯出 %d 張（約 %d 秒）" % (frames[0], frames[0] // max(1, fps))
