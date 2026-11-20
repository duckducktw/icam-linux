#!/bin/sh
# 安裝 iCam：建 venv、裝相依、加桌面捷徑
set -e
cd "$(dirname "$0")"

echo "== 建立 Python 環境"
python3 -m venv .venv
.venv/bin/pip -q install --upgrade pip
.venv/bin/pip -q install -r requirements.txt

echo "== 桌面捷徑"
APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"
cat > "$APPDIR/icam.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=iCam
Comment=網路監視器多路監看（Linux 原生）
Exec=$(pwd)/run.sh
Path=$(pwd)
Terminal=false
Categories=AudioVideo;Video;Network;
EOF
chmod +x run.sh
echo "完成。從應用程式選單開 iCam，或執行 $(pwd)/run.sh"
