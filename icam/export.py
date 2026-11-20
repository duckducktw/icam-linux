"""從 DVR 主機匯出指定時段的錄影成 mp4（備份用，全速下載不配速）。

長區間會自動分段（預設每段 5 分鐘）重新連線，避免長時間佔住 DVR 的單一連線。
"""

import subprocess
import time

from . import dvr as dvrmod
from .mediautil import ffmpeg_bin


def export_range(profile, channel, beg, end, out_path, fps=20, progress=None,
                 timeout=None, should_stop=None, seg_secs=300):
    """把 DVR 上 [beg, end] 這段錄影存成 mp4。

    progress(done_frames, total_frames, 說明) 會被週期呼叫；
    回傳 (成功?, 訊息)。
    """
    if not profile.get("host"):
        return False, "沒有 DVR 主機設定"
    dur = max(1, int(end) - int(beg))
    total = max(1, int(dur * fps))
    if not ffmpeg_bin():
        return False, "缺少 ffmpeg"
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "h264", "-r", str(fps), "-i", "pipe:0",
         "-c", "copy", "-movflags", "+faststart", "-y", str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = 0
    stop_at = time.time() + (timeout if timeout else dur * 4 + 120)
    aborted = False

    def cancelled():
        nonlocal aborted
        if should_stop and should_stop():
            aborted = True
        return aborted or time.time() > stop_at

    def on_segment(idx, nseg, b, e, err=None):
        if progress and not err:
            progress(min(frames, total), total,
                     "第 %d/%d 段 %s" % (idx + 1, nseg,
                                        time.strftime("%H:%M:%S", time.localtime(b))))

    try:
        for body in dvrmod.pull_bodies(profile, channel, beg, end, seg_secs=seg_secs,
                                       fps=fps, should_stop=cancelled,
                                       on_segment=on_segment):
            for stype, ch, _w, _h, payload in dvrmod.iter_sub_chunks(body):
                if ch != channel or stype not in dvrmod.VIDEO_TYPES or not payload:
                    continue
                try:
                    proc.stdin.write(payload)
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    aborted = True
                    break
                frames += 1
                if progress and frames % 20 == 0:
                    progress(min(frames, total), total, "已收 %d 張" % frames)
            if aborted:
                break
    except Exception as e:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
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
        progress(min(frames, total), total, "完成")
    if frames == 0:
        return False, "那段時間沒有錄影"
    if aborted:
        return False, "已中止（已存的 %d 張仍在檔案裡）" % frames
    return True, "匯出 %d 張（約 %d 秒）" % (frames, frames // max(1, fps))
