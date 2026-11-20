"""與 GUI 無關的小工具（讓非 GUI 的程式也能重用，不必載入 Qt）。"""

import os
import shutil


def ffmpeg_bin():
    for cand in ("ffmpeg", "/usr/bin/ffmpeg"):
        p = shutil.which(cand) if "/" not in cand else (cand if os.path.exists(cand) else None)
        if p:
            return p
    return None
