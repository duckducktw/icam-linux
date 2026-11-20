"""Mock iCatch DVR — 用來測 `icam.dvr` 的拆流邏輯，不需要真的主機。

產生跟真機一樣格式的 `multipart/x-mixed-replace`：每段 body 是 0x120 標頭 +
逐個 44 bytes sub-chunk 標頭，payload 為 Annex-B H.264。

用法：
  ffmpeg -f lavfi -i testsrc2=size=640x360:rate=10:duration=4 -c:v libx264 \
         -g 10 -bf 0 -pix_fmt yuv420p -f h264 /tmp/live.h264
  python tools/mock_dvr.py /tmp/live.h264 18099 4     # 埠 18099、4 個通道
  # 設定檔：{"dvr": {"host": "127.0.0.1", "port": 18099, "user": "admin",
  #                  "password": "test", "channels": 4}, ...}
  # 帳密驗證：admin / test

注意：這個 mock 產生的畫面只是彩條測試圖，**不能當成連上真機的證據**。
"""

import base64
import struct
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

H264 = sys.argv[1] if len(sys.argv) > 1 else "live.h264"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 18099
NCH = int(sys.argv[3]) if len(sys.argv) > 3 else 4
AUTH = "Basic " + base64.b64encode(b"admin:test").decode()

HDR_SUB_OFFSET = 0x120
SUB_HEADER_SIZE = 0x2C


def split_frames(data):
    """以 start code 切 AU（type 1/5 開新 frame）。"""
    idx = []
    i = 0
    while True:
        j = data.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        if j + 3 < len(data):
            t = data[j + 3] & 0x1F
            if t in (1, 5):
                idx.append((j, t))
        i = j + 3
    frames = []
    for k, (pos, t) in enumerate(idx):
        end = idx[k + 1][0] if k + 1 < len(idx) else len(data)
        frames.append((1 if t == 5 else 0, data[pos:end]))
    return frames


def section_for(frames, nch):
    subs = b""
    for ch in range(nch):
        for stype, payload in frames:
            hdr = bytearray(SUB_HEADER_SIZE)
            struct.pack_into("<I", hdr, 0x00, stype)
            struct.pack_into("<I", hdr, 0x04, ch)
            struct.pack_into("<I", hdr, 0x08, 640)
            struct.pack_into("<I", hdr, 0x0C, 360)
            struct.pack_into("<I", hdr, 0x24, len(payload))
            struct.pack_into("<I", hdr, 0x28, len(payload))
            subs += bytes(hdr) + payload
    head = bytearray(HDR_SUB_OFFSET)
    struct.pack_into("<I", head, 0x00, 0x00001234)
    struct.pack_into("<I", head, 0x0C, int(time.time()) & 0xFFFFFFFF)
    struct.pack_into("<I", head, 0x1C, nch * len(frames))
    return bytes(head) + subs


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/cgi-bin/net_video.cgi":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != AUTH:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="DVR"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        q = parse_qs(url.query)
        mask = int(q.get("iframe", ["1"])[0])
        chans = [c for c in range(NCH) if mask & (1 << c)] or [0]
        frames = FRAMES
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=--myboundary")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                body = section_for(frames, 1) if len(chans) == 1 else section_for(frames, NCH)
                self.wfile.write(b"--myboundary\r\nContent-Type: image/jpeg\r\n\r\n")
                self.wfile.write(body)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            return


if __name__ == "__main__":
    FRAMES = split_frames(open(H264, "rb").read())
    print("mock DVR: %d frames, %d channels, port %d" % (len(FRAMES), NCH, PORT), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
