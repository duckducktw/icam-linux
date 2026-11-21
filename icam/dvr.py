"""iCatch SoC DVR 私有串流：把 /cgi-bin/net_video.cgi 的多路混合流拆成單路 H.264。

協議來自 iCatch DVR 的 CGI 介面（iWatchDVR / SoCatch 走的就是這條路）：
  GET /cgi-bin/net_video.cgi?hq=1&iframe=<mask>&pframe=<mask>&audio=0
      &complete=0&beg=-1&end=-1&ivs=0
回應 multipart/x-mixed-replace（boundary=--myboundary），每段內容為：
  0x0000  magic 0x00001234
  0x001c  chunk_count
  0x0120  第一個 sub-chunk 標頭（44 bytes），之後 off += 44 + next_step
sub-chunk 標頭：type(0=I,1=P,2=音訊) / channel / width / height / size / next_step
"""

import socket
import struct
import threading
import time

MAGIC_WORD = 0x00001234
BOUNDARY = b"--myboundary"

HDR_MAGIC = 0x00
HDR_TS1 = 0x0C
HDR_COUNT = 0x1C
HDR_SUB_OFFSET = 0x120

SUB_HEADER_SIZE = 0x2C
SUB_TYPE = 0x00
SUB_CHANNEL = 0x04
SUB_WIDTH = 0x08
SUB_HEIGHT = 0x0C
SUB_SIZE = 0x24
SUB_NEXT_STEP = 0x28

SUB_TYPE_IFRAME = 0
SUB_TYPE_PFRAME = 1
VIDEO_TYPES = (SUB_TYPE_IFRAME, SUB_TYPE_PFRAME)


def build_url(host, channel, port=80, scheme="http", hq=1, beg=-1, end=-1, complete=0):
    """單一頻道的取流網址（只要求這一路的 I/P frame）。"""
    mask = 1 << int(channel)
    return ("%s://%s:%d/cgi-bin/net_video.cgi?hq=%d&iframe=%d&pframe=%d"
            "&audio=0&complete=%d&beg=%d&end=%d&ivs=0"
            % (scheme, host, port, hq, mask, mask, complete, beg, end))


def parse_header(body):
    """回傳 dict(ts1, chunk_count)；不是合法標頭回 None。"""
    if len(body) < HDR_SUB_OFFSET:
        return None
    if struct.unpack_from("<I", body, HDR_MAGIC)[0] != MAGIC_WORD:
        return None
    return {
        "ts1": struct.unpack_from("<I", body, HDR_TS1)[0],
        "chunk_count": struct.unpack_from("<I", body, HDR_COUNT)[0],
    }


def iter_sub_chunks(body, count=None):
    """逐個 sub-chunk 產出 (type, channel, width, height, payload)。"""
    hdr = parse_header(body)
    if not hdr:
        return
    count = hdr["chunk_count"] if count is None else count
    off = HDR_SUB_OFFSET
    n = len(body)
    for _ in range(count):
        if off + SUB_HEADER_SIZE > n:
            return
        stype = struct.unpack_from("<I", body, off + SUB_TYPE)[0]
        ch = struct.unpack_from("<I", body, off + SUB_CHANNEL)[0]
        w = struct.unpack_from("<I", body, off + SUB_WIDTH)[0]
        h = struct.unpack_from("<I", body, off + SUB_HEIGHT)[0]
        size = struct.unpack_from("<I", body, off + SUB_SIZE)[0]
        step = struct.unpack_from("<I", body, off + SUB_NEXT_STEP)[0]
        body_start = off + SUB_HEADER_SIZE
        yield stype, ch, w, h, bytes(body[body_start:body_start + size])
        off = body_start + step


class MultipartSplitter:
    """切 multipart/x-mixed-replace，每段 body 丟給 callback。"""

    def __init__(self, boundary=BOUNDARY, callback=None):
        self.boundary = boundary
        self.callback = callback
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        while True:
            b0 = self.buf.find(self.boundary)
            if b0 < 0:
                if len(self.buf) > 64:
                    del self.buf[:len(self.buf) - 64]
                return
            head_end = self.buf.find(b"\r\n\r\n", b0)
            if head_end < 0:
                return
            body_start = head_end + 4
            b1 = self.buf.find(self.boundary, body_start)
            if b1 < 0:
                return
            body = bytes(self.buf[body_start:b1])
            if self.callback:
                self.callback(body)
            del self.buf[:b1]


def probe(host, port=80, user=None, password=None, scheme="http", timeout=6):
    """檢查 DVR 的 CGI 是否可用；回傳 (ok, 訊息)。

    只發一次請求、讀第一個 multipart 段就斷線，用來確認帳密與通道。
    """
    import requests
    url = build_url(host, 0, port=port, scheme=scheme)
    try:
        r = requests.get(url, auth=(user or "", password or ""), stream=True,
                         timeout=timeout)
    except requests.RequestException as e:
        return False, "連線失敗: %s" % e
    if r.status_code == 401:
        r.close()
        return False, "帳號或密碼錯誤 (401)"
    if r.status_code != 200:
        r.close()
        return False, "HTTP %s" % r.status_code
    got = []
    splitter = MultipartSplitter(callback=lambda body: got.append(body))
    deadline_bytes = 0
    try:
        for chunk in r.iter_content(chunk_size=32768):
            splitter.feed(chunk)
            deadline_bytes += len(chunk)
            if got or deadline_bytes > 4 * 1024 * 1024:
                break
    finally:
        r.close()
    if not got:
        return False, "有回應但沒收到影像（通道可能沒接攝影機）"
    hdr = parse_header(got[0])
    if not hdr:
        return False, "串流格式不符（可能不是 iCatch 主機）"
    chans = sorted({c for t, c, _w, _h, _p in iter_sub_chunks(got[0]) if t in VIDEO_TYPES})
    return True, "OK，通道: %s" % (",".join(str(c) for c in chans) or "無")


def _raw_fetch(host, port, path, user, password, deadline, max_bytes=6_000_000):
    """用裸 socket 抓一段 HTTP 串流內容，時間到就斷。

    為什麼不用 requests：沒接攝影機的通道會回 200 但一個 byte 都不送，
    requests 的 read timeout 不會觸發（實測會卡 50 秒），連 close() 也叫不醒它。
    回傳 (status_code, bytes)。
    """
    import base64

    buf = bytearray()
    t_end = time.monotonic() + deadline
    s = socket.create_connection((host, port), timeout=max(1.0, deadline))
    try:
        cred = base64.b64encode(("%s:%s" % (user or "", password or "")).encode()).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nAuthorization: Basic %s\r\n"
               "Accept: */*\r\nConnection: close\r\n\r\n" % (path, host, port, cred))
        s.sendall(req.encode())
        while time.monotonic() < t_end and len(buf) < max_bytes:
            s.settimeout(max(0.2, min(1.0, t_end - time.monotonic())))
            try:
                data = s.recv(16384)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
    finally:
        try:
            s.close()
        except OSError:
            pass
    status = 0
    if buf.startswith(b"HTTP/"):
        try:
            status = int(bytes(buf.split(b" ")[1]))
        except (IndexError, ValueError):
            status = 0
    return status, bytes(buf)


def detect_channels(profile, max_channels=16, probe_seconds=2.2, should_stop=None,
                    progress=None, workers=4):
    """自動偵測主機上有哪些通道有影像。

    並行探測（一次 workers 路），每路 probe_seconds 秒，統計張數與解析度。回傳
    [{'channel': 0, 'width': 944, 'height': 480, 'fps': 20.1, 'frames': 50}, ...]

    沒接攝影機的通道回 200 但不送任何資料 → 靠硬性截止跳過；
    一整批都空的就停（避免對 16 路全部空等的機器白等）。
    """
    from concurrent.futures import ThreadPoolExecutor

    host = profile.get("host", "")
    if not host:
        return []
    port = int(profile.get("port", 80))
    path_tpl = ("/cgi-bin/net_video.cgi?hq=1&iframe=%d&pframe=%d&audio=0"
                "&complete=0&beg=-1&end=-1&ivs=0")
    user, password = profile.get("user", ""), profile.get("password", "")

    def probe(ch):
        mask = 1 << ch
        t0 = time.monotonic()
        try:
            status, raw = _raw_fetch(host, port, path_tpl % (mask, mask), user, password,
                                     probe_seconds + 1.0)
        except OSError:
            return None
        if status != 200 or not raw:
            return None
        splitter = MultipartSplitter()
        stats = {"frames": 0, "w": 0, "h": 0}
        splitter.callback = lambda body: _count_video(body, stats)
        splitter.feed(raw)
        elapsed = max(0.5, time.monotonic() - t0)
        if not stats["frames"]:
            return None
        return {"channel": ch, "width": stats["w"], "height": stats["h"],
                "frames": stats["frames"], "fps": round(stats["frames"] / elapsed, 1)}

    found = {}
    ch = 0
    while ch < int(max_channels):
        if should_stop and should_stop():
            break
        batch = list(range(ch, min(ch + workers, int(max_channels))))
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            batch_res = list(pool.map(probe, batch))
        got = [r for r in batch_res if r]
        for r in got:
            found[r["channel"]] = r
        if progress:
            progress(min(ch + workers, int(max_channels)), got)
        if not got and found:
            break
        ch += workers
    return [found[k] for k in sorted(found)]


def _count_video(body, stats):
    for stype, _channel, w, h, _payload in iter_sub_chunks(body):
        if stype in VIDEO_TYPES:
            stats["frames"] += 1
            stats["w"], stats["h"] = w, h


def pull_bodies(profile, channel, beg=None, end=None, seg_secs=300, fps=20.0,
                should_stop=None, on_segment=None, timeout=15):
    """持續產出 multipart 段的 body。

    - beg/end 皆為 None → 即時串流（單一連線，直到 should_stop 或斷線）
    - 有 beg/end → 歷史錄影，自動**分段**（每段 seg_secs 秒）重新連線，
      避免長時間佔住 DVR 的單一連線（舊版一次拉整段會卡死）。

    on_segment(第幾段, 總段數, beg, end) 會在每段開始前呼叫。
    """
    import requests

    host = profile.get("host", "")
    port = int(profile.get("port", 80))
    scheme = profile.get("scheme", "http")
    auth = (profile.get("user", ""), profile.get("password", ""))
    if not host:
        return

    if beg is None or end is None:
        segments = [(None, None)]
    else:
        segments = []
        cur, end = int(beg), int(end)
        while cur < end:
            nxt = min(cur + int(seg_secs), end)
            segments.append((cur, nxt))
            cur = nxt
        if not segments:
            return

    for idx, (b, e) in enumerate(segments):
        if should_stop and should_stop():
            return
        if on_segment:
            on_segment(idx, len(segments), b, e)
        url = build_url(host, channel, port=port, scheme=scheme,
                        beg=b if b is not None else -1,
                        end=e if e is not None else -1)
        expected = int((e - b) * fps) if b is not None else None
        got = 0
        splitter = MultipartSplitter()
        pending = []
        splitter.callback = pending.append
        try:
            with requests.get(url, auth=auth, stream=True, timeout=timeout) as r:
                if r.status_code != 200:
                    raise RuntimeError("HTTP %s" % r.status_code)
                for chunk in r.iter_content(16384):
                    if should_stop and should_stop():
                        return
                    splitter.feed(chunk)
                    while pending:
                        body = pending.pop(0)
                        got += sum(1 for t, *_ in iter_sub_chunks(body)
                                   if t in VIDEO_TYPES)
                        yield body
                    if expected and got >= expected:
                        break
        except Exception as exc:
            if b is None:      # 即時串流：讓呼叫端重連
                raise
            # 錄影某一段抓不到（可能跨到沒錄影的時段）→ 換下一段
            if on_segment:
                on_segment(idx, len(segments), b, e, str(exc))
            continue
