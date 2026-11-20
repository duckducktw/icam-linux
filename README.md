# iCam — Linux 原生監視器監看

取代 SoCatch / iWatchDVR / iCMS（Windows 專屬）的 Linux 原生多路監看程式。Python + Qt6，影像解碼交給 ffmpeg，**不需要 Wine**。

## 三種來源都吃

| 來源 | 網址格式 | 說明 |
|---|---|---|
| iCatch DVR（SoCatch 那台） | `dvr://<通道>` | 走 DVR 的 `/cgi-bin/net_video.cgi`，自行拆解多路混合流 |
| ONVIF / RTSP 攝影機 | `rtsp://帳號:密碼@IP:554/路徑` | 一般網路攝影機 |
| 任何 ffmpeg 吃得下的東西 | 檔案路徑 / `http://` | 測試用 |

## 安裝

```sh
./install.sh          # 建 venv、裝 PySide6、加桌面捷徑
./run.sh              # 啟動
```

需要系統有 `ffmpeg`（`sudo apt install ffmpeg`）。

## 用法

開起來後從工具列：

- **DVR 主機**：填 IP／帳密／通道數 → 測試連線 → 自動把 CH1..CHn 加進畫面
- **探索**：掃區網 ONVIF 裝置，輸入帳密自動取得 RTSP 網址
- **新增／編輯**：手動貼 RTSP 網址
- 格狀自動排列（1 / 4 / 9 / 16 格）

快速鍵：`F` 全螢幕、`S` 截圖、`R` 錄影、`Delete` 移除、雙擊放大單路、`Esc` 退出放大、`Ctrl+Q` 離開。

截圖存 `~/Pictures/icam/`，錄影（直接 copy，不重編碼）存 `~/Pictures/icam-rec/`。

## 設定檔

`~/.config/icam-linux/cameras.json`，權限 600，內含 DVR 帳密：

```json
{
  "dvr": {"host": "192.168.0.150", "port": 80, "user": "admin", "password": "...", "channels": 4},
  "cameras": [
    {"name": "CH1", "url": "dvr://0"},
    {"name": "門口", "url": "rtsp://user:pass@192.168.0.146:554/stream1"}
  ],
  "tile": [640, 360],
  "fps": 15,
  "transport": "tcp",
  "snapshot_dir": "/home/user/Pictures/icam"
}
```

## iCatch DVR 串流格式（逆向結果）

`GET /cgi-bin/net_video.cgi?hq=1&iframe=<bitmask>&pframe=<bitmask>&audio=0&complete=0&beg=-1&end=-1&ivs=0`
回 `multipart/x-mixed-replace`（`--myboundary`），每段 body：

- `0x0000` magic `0x00001234`、`0x001c` 子區塊數、`0x0120` 起為第一個子區塊標頭
- 子區塊標頭 44 bytes：`type`(0=I,1=P,2=音訊) / `channel` / `width` / `height` / `0x24` size / `0x28` next_step
- 之後 `off += 44 + next_step`

`iframe` / `pframe` 是位元遮罩（`1<<通道`），只要求單一通道可拿到該路最高 fps。實作見 `icam/dvr.py`。

## 已知限制

- 只做**即時監看 + 截圖 + 錄影**。DVR 主機上的歷史錄影回放還沒做（需要再確認那台的播放 CGI 參數）。
- DVR 串流沒有時戳，用系統時鐘補；畫面節奏若忽快忽慢是 DVR 本身的 burst 特性。
