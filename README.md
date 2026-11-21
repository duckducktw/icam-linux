# iCam — Linux 原生監視器監看

取代 SoCatch / iWatchDVR / iCMS（Windows 專屬）的 Linux 原生多路監看程式。Python + Qt6，影像解碼交給 ffmpeg，**不需要 Wine**。

> English version: [README.en.md](README.en.md)

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

圖示用**系統原生主題**（KDE Breeze / GNOME 的 `camera-video`），沒有這個圖示的主題才退回內建的 `assets/icam.svg`，所以看起來會跟你桌面其他程式一致。

需要系統有 `ffmpeg`（`sudo apt install ffmpeg`）。

## 用法

第一次開起來會看到引導畫面，三種加入方式挑一種：

1. **連線 DVR 主機** — 填 IP／帳密後按「連線並偵測通道」，程式會**自動偵測**每路有沒有影像、解析度與 fps，把訊號極弱的自動取消勾選，確認後「加入畫面」。
2. **掃描網路攝影機** — 掃區網 ONVIF 裝置，輸入帳密自動取得 RTSP 網址。
3. **手動輸入 RTSP 網址** — 已經知道串流網址時使用。

工具列（都有圖示與滑鼠提示）：DVR 主機、回放、掃描網路攝影機、新增網址、重新連線、截圖、錄影。

**在任何一格上按右鍵**就有這一格的操作：放大／縮小、重新連線、看這一格的錄影（回放）、截圖、開始／停止錄影、改名、從畫面移除。名稱改成「門口」「車庫」之後會存進設定檔。

格狀自動排列（1 / 4 / 9 / 16 格），狀態列右側顯示目前路數與 DVR 位址。

快速鍵：`F` 全螢幕、`S` 截圖、`R` 錄影、`Delete` 移除、雙擊放大單路、`Esc` 退出放大、`Ctrl+Q` 離開。

截圖存 `~/Pictures/icam/`，錄影（直接 copy，不重編碼）存 `~/Pictures/icam-rec/`。

## 設定檔

`~/.config/icam-linux/cameras.json`（權限 600）。**帳密放另一個檔案** `~/.config/icam-linux/dvr.env`（權限 600），程式會優先讀它，存檔時也不會把密碼寫回 JSON：

```sh
# ~/.config/icam-linux/dvr.env
DVR_HOST=192.168.0.150
DVR_PORT=80
DVR_USER=<帳號>
DVR_PASS=<密碼>
```

```json
{
  "dvr": {"host": "192.168.0.150", "port": 80, "channels": 4},
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

## 回放與匯出（實測）

同一支 CGI 換 `beg`／`end` 就是歷史錄影，兩個都是 **Unix 秒**：

- `beg=-1&end=-1` → 即時
- `beg=<epoch>&end=<epoch>` → 該時段的錄影（會從 beg 開始播）

工具列的 **回放** 可選通道／起始時間／長度：時間軸可拖（拖放＝從該時間點重新拉流）、暫停、**匯出這段**（全速下載不配速，直接 copy 成 mp4）。

注意：DVR 只有每段開頭給一次時戳，所以配速靠張數（這台約 20fps）；不同主機的實際錄影 fps 可能不同，可在設定檔 `dvr.pace_fps` 調。

## 已修正的問題（前後對照）

| 問題 | 修正前 | 修正後 | 驗證 |
|---|---|---|---|
| 長區間回放／匯出會卡死 | 一個請求硬拉整段，超過幾分鐘就卡住或斷線 | 自動分段（預設每段 5 分鐘，`dvr.seg_secs` 可調）重新連線接續 | 180 秒區間切成 6 段，匯出 3600 張、mp4 時長 180.000 秒；回放跨 3 次段界仍連續播放 |
| 回放播完無限重連 | 段落播完 stream 結束 → 顯示「重連中」然後一直重試 | 段落結束就停，顯示「回放結束」；整段沒錄影顯示「這段沒有錄影」 | 單路回放跑到結尾後狀態為「回放結束」，不再重連 |
| 沒訊號的通道看起來像程式壞了 | 一直顯示「連線中／播放中」，畫面只有 VIDEO LOSS 藍底 | 持續量測真實封包張數，12 秒後低於 1.5 fps 就標「無訊號（這路只有 X fps）」，之後即使重連也標「播放中（訊號極弱）」 | 實測 CH2（DVR 回 VIDEO LOSS）13.0 秒後標示，其餘三路不受影響 |
| 開 ONVIF 探索／按測試連線時整個介面凍住 | 掃描 5 秒、取流 6 秒期間 UI 完全不回應 | 掃描、取流、連線測試都丟到背景執行緒，結果回來才更新 | 掃描期間視窗仍可拖拉、狀態列持續更新 |
| 密碼被複製進 `cameras.json` | 第一次存檔就把 `dvr.password` 寫進 JSON | 偵測到 `dvr.env` 有密碼就不寫入 JSON | `cameras.json` 內容確認不含 `password` 欄位 |
| 第一張畫面出不來（慢的通道） | ffmpeg 等 500 KB 才開始輸出 | 降到 40 KB／`analyzeduration 0` | 正常三路 2.6 秒出畫面 |
| 通道數要自己數（新增） | 手動填「通道數」4，填錯就多出黑格 | 「連線並偵測通道」自動逐路探測，列出解析度／fps，訊號極弱自動取消勾選 | 對真機偵測 6.5 秒得到 4 路（ch0-3），CH2 只有 0.9 fps 自動不勾 |
| 介面操作不好懂（新增） | 只有文字工具列；空白畫面沒有引導；每格的操作藏在工具列 | 引導首頁（三種加入方式）、工具列原生圖示＋提示、每格右鍵選單（放大／改名／回放／截圖／錄影／移除）、狀態列顯示路數與 DVR | `docs/ui-welcome.png`、`docs/ui-dvr-detect.png`、`docs/ui-live.png` |
| 掃描到一半關掉視窗會閃退 | 背景執行緒掛在對話框底下，關窗時被一起刪掉 → `QThread: Destroyed while thread is still running` → 直接 core dumped | 背景工作改由模組托管（不掛父物件），關窗時先斷回呼再送取消旗標；掃描／偵測／取流都支援中途取消 | 舊版跑同一腳本 `Aborted (core dumped)`；新版同流程 exit 0，關窗後還能再開一次掃描，主視窗 3 路正常 |

## 已知限制

- **沒有聲音**：DVR 的音訊子區塊（type 2）直接忽略，只做影像。
- **一次只能設一組 DVR 主機**：多台要改設定檔。
- **回放一次一路**：多路同時回放還沒做（同時拉多段會吃掉主機的上傳頻寬）。
- 錄影 fps 靠設定推定（預設 20）；主機實際錄影張數不同時，匯出的 mp4 時間長度會跟著差，可在 `dvr.pace_fps` 調。
- DVR 串流沒有逐張時戳，即時節奏忽快忽慢是 DVR 本身的 burst 特性，不是解碼延遲。

## 關於截圖（重要）

本專案 repo 內**不含**任何截圖。開發／交付時出現過的兩種圖要分清楚：

- **測試圖**：四格是一模一樣的彩條測試畫面（`testsrc2` 產生的），那是我自建模擬主機（`icam` 的 mock DVR，只為了驗證拆流與解碼邏輯）跑出來的，**不是真實監視器畫面，也不是本程式連上真機的證據**。
- **實機圖**：畫面是實際攝影機內容（車庫、騎樓、路口），右上角有 DVR 自己燒的時間戳，這種才是連上真機（iCatch / iWatchDVR 主機）的證據。

真機實測數據（一台 4 路 iCatch 主機）：CH1/CH3/CH4 944×480 約 20 fps、CH2 該路回 `VIDEO LOSS`（約 0.9 fps）。

`docs/` 內的三張圖：

| 檔案 | 內容 | 性質 |
|---|---|---|
| `docs/ui-welcome.png` | 空白引導首頁 | 介面截圖（沒有影像，不涉及真機） |
| `docs/ui-dvr-detect.png` | DVR 精靈自動偵測通道的結果 | 介面截圖，數字來自**真機**探測 |
| `docs/ui-live.png` | 三路即時畫面 | **真機**畫面（畫面左上角有 DVR 燒的 2026/11/21 15:31 時間戳） |
