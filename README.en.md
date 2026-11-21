# iCam — native Linux viewer for CCTV recorders

A native Linux multi-channel viewer that replaces Windows-only clients such as **SoCatch / iWatchDVR / iCMS**. Python + Qt6, decoding delegated to `ffmpeg`. **No Wine, no compatibility layer.**

> 中文版：[README.md](README.md)

It was written for an iCatch-based DVR (the kind shipped with `iWatchDVR.exe`), but it also speaks ONVIF/RTSP, so it works with ordinary IP cameras.

## Supported sources

| Source | URL form | Notes |
|---|---|---|
| iCatch DVR (the SoCatch type) | `dvr://<channel>` | Talks to the DVR's `/cgi-bin/net_video.cgi` and demuxes its proprietary multi-channel stream |
| ONVIF / RTSP camera | `rtsp://user:pass@IP:554/path` | Plain network cameras |
| Anything ffmpeg can read | file path / `http://` | Useful for testing |

## Install

```sh
./install.sh          # creates a venv, installs PySide6, adds a desktop entry
./run.sh              # start
```

The app icon comes from the **native icon theme** (KDE Breeze / GNOME `camera-video`); themes without it fall back to the bundled `assets/icam.svg`, so it looks like the rest of your desktop.

Requires `ffmpeg` on the system (`sudo apt install ffmpeg`).

If your distro's Qt is newer/older than the one bundled with PySide6, the app re-executes itself with the bundled Qt libraries (`LD_LIBRARY_PATH`) automatically — that is what the `reexec_with_bundled_qt()` guard in `icam/__init__.py` is for.

## Usage

On first start you get a welcome screen with three ways to add cameras:

1. **Connect a DVR host** — enter IP / credentials, press "connect and detect channels". The app **probes every channel itself**, reports resolution and fps per channel, automatically unticks channels with a very weak signal, and adds the ticked ones to the grid.
2. **Scan the network** — ONVIF discovery on the LAN, then credentials, then the RTSP URL is resolved via `GetStreamUri`.
3. **Enter an RTSP URL manually** — when you already know the stream address.

Toolbar (icons plus tooltips): DVR host, playback, scan network cameras, add URL, reconnect, snapshot, record.

**Right-click any tile** for that tile's actions: maximise / restore, reconnect, watch its recording (playback), snapshot, start/stop recording, rename, remove. Renamed tiles ("front door", "garage") are saved to the config.

Grid auto-arranges for 1 / 4 / 9 / 16 tiles; the status bar shows the tile count and the DVR address.

Shortcuts: `F` fullscreen, `S` snapshot, `R` record, `Delete` remove, double-click to maximise one tile, `Esc` to leave maximise, `Ctrl+Q` quit.

Snapshots go to `~/Pictures/icam/`; recordings (stream copy, no re-encode) go to `~/Pictures/icam-rec/`.

## Configuration

`~/.config/icam-linux/cameras.json` (mode 600). **Credentials live in a separate file**, `~/.config/icam-linux/dvr.env` (mode 600). The app reads it first and never writes the password back into the JSON:

```sh
# ~/.config/icam-linux/dvr.env
DVR_HOST=192.168.0.150
DVR_PORT=80
DVR_USER=<user>
DVR_PASS=<password>
```

```json
{
  "dvr": {"host": "192.168.0.150", "port": 80, "channels": 4},
  "cameras": [
    {"name": "CH1", "url": "dvr://0"},
    {"name": "front door", "url": "rtsp://user:pass@192.168.0.146:554/stream1"}
  ],
  "tile": [640, 360],
  "fps": 15,
  "transport": "tcp",
  "snapshot_dir": "/home/user/Pictures/icam"
}
```

Extra DVR knobs: `dvr.pace_fps` (recorded frames per second used for playback pacing and export timestamps, default 20) and `dvr.seg_secs` (playback/export segment length, default 300 s).

## iCatch DVR stream format (reverse-engineered)

```
GET /cgi-bin/net_video.cgi?hq=1&iframe=<bitmask>&pframe=<bitmask>&audio=0&complete=0&beg=-1&end=-1&ivs=0
```

The DVR answers with `multipart/x-mixed-replace` (boundary `--myboundary`). Each multipart body is:

- `0x0000` magic `0x00001234`, `0x001c` sub-chunk count, first sub-chunk header at `0x0120`
- sub-chunk header, 44 bytes: `type` (0 = I-frame, 1 = P-frame, 2 = audio) / `channel` / `width` / `height` / `0x24` payload size / `0x28` `next_step`
- advance with `offset += 44 + next_step`

`iframe` / `pframe` are bitmasks (`1 << channel`). Requesting a single channel gives that channel its full frame rate; asking for all of them in one request splits the encoder budget. Implementation: `icam/dvr.py`.

Nothing on the market demuxes this format (go2rtc and friends reject it), which is exactly why the Windows client exists. This app does the demuxing itself and pipes plain Annex-B H.264 into `ffmpeg`.

## Playback and export (measured on real hardware)

The same CGI endpoint serves recorded video when you pass `beg` / `end` — both are **Unix seconds**:

- `beg=-1&end=-1` → live
- `beg=<epoch>&end=<epoch>` → recording for that window, starting at `beg`

The toolbar's **回放** (Playback) dialog lets you pick channel / start time / duration. The timeline is draggable (dragging re-opens the stream at that point), there is pause, and **匯出這段** (Export) downloads the range at full speed and stream-copies it into an mp4.

Long ranges are fetched **in segments** (default 5 minutes each) with a fresh connection per segment, because the DVR will not reliably hold one long request. Playback paces frames by count, since the DVR only timestamps each section once, not every frame.

## Fixed issues (before / after)

| Issue | Before | After | Verification |
|---|---|---|---|
| Long playback / export stalled | One request for the whole range; a few minutes in, the stream stalled or dropped | Automatic segmentation (default 5 min, `dvr.seg_secs`) with reconnect and seamless continuation | 180 s range split into 6 segments → 3600 frames, mp4 duration exactly 180.000 s; playback continued across 3 segment boundaries at 1× |
| Playback looped forever at the end | End of range → stream ends → "reconnecting" and retry endlessly | Stops at the end: "回放結束"; a range with no recording shows "這段沒有錄影" | Single-channel playback reaches the end and stops, no reconnect |
| A dead channel looked like a broken app | Stuck on "connecting / playing" while showing only the DVR's blue VIDEO LOSS screen | Real packet rate is measured; below 1.5 fps after 12 s it labels "無訊號（這路只有 X fps）", and keeps "playing (very weak signal)" after reconnects | Measured on the real DVR: CH2 (VIDEO LOSS) labelled at 13.0 s, the other three channels unaffected |
| ONVIF discovery / connection test froze the UI | 5 s scan and 6 s stream-resolution blocked the whole window | Scan, stream resolution and connection test all run on worker threads; the UI updates when results arrive | Window stays interactive during a scan |
| Password copied into `cameras.json` | First save wrote `dvr.password` into the JSON | If `dvr.env` holds the password it is stripped before writing | `cameras.json` verified to have no `password` field |
| Slow first frame on weak channels | ffmpeg waited for 500 KB before emitting output | Lowered to 40 KB / `analyzeduration 0` | Healthy channels show their first frame in 2.6 s |
| Channel count was typed by hand (new) | You had to enter "4 channels"; a wrong number produced dead tiles | "Connect and detect channels" probes every channel, lists resolution / fps, and unticks the ones with a very weak signal | Against the real recorder: 6.5 s to find 4 channels (ch0-3); CH2 at 0.9 fps was left unticked automatically |
| UI was hard to discover (new) | Text-only toolbar, no guidance on an empty window, per-tile actions hidden in the toolbar | Welcome screen with three entry points, themed toolbar icons with tooltips, right-click menu per tile (maximise / rename / playback / snapshot / record / remove), status bar with tile count and DVR address | Right-click menu has 7 items; status bar reads "4 路畫面 · DVR 192.168.0.150" |

## Known limitations

- **No audio.** Audio sub-chunks (type 2) are ignored; video only.
- **One DVR host at a time.** Multiple hosts need a config edit.
- **Playback is single-channel.** Simultaneous multi-channel playback would eat the DVR's upstream bandwidth.
- **Recorded frame rate is assumed**, not detected (`dvr.pace_fps`, default 20). If the DVR records at a different rate, exported mp4 durations shift accordingly.
- The stream carries no per-frame timestamps, so live pacing follows the DVR's own bursty delivery; jitter there is the device, not the decoder.

## About screenshots (please read)

The three files under `docs/` are all **real UI captures** and are labelled here so nobody has to guess:

| File | Content | Nature |
|---|---|---|
| `docs/ui-welcome.png` | Empty-state welcome screen | UI capture, no camera imagery involved |
| `docs/ui-dvr-detect.png` | DVR wizard after auto-detecting channels | UI capture; the numbers come from a **real recorder** probe |
| `docs/ui-live.png` | Three live channels | **Real-device** footage — the DVR's own timestamp (2026/11/21 15:31) is burned into the top-left corner |

Everything else described below is about images produced during development, which are **not** in the repo. Two very different kinds came out of it, and they must not be confused:

- **Test images** — four identical colour-bar test patterns (generated by ffmpeg's `testsrc2`). These come from a **mock DVR I wrote myself** (`mock_dvr.py`), which exists only to exercise the demuxer and decoder against a known-good byte stream. They are **not camera footage, and not evidence that this app ever talked to a real recorder.**
- **Real-device images** — actual camera scenes (a garage, an arcade, a road junction) with the DVR's own burned-in timestamp in the corner. Only these are evidence of a successful connection to real hardware.

Measured against one real 4-channel iCatch recorder: CH1 / CH3 / CH4 at 944×480 ≈ 20 fps; CH2 returned `VIDEO LOSS` at ≈ 0.7 fps because that channel has no working camera attached. That is a device-side condition, not a decoder problem — the app now labels it instead of pretending everything is fine.
