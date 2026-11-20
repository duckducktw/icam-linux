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

import struct

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
