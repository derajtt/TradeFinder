#!/usr/bin/env bash
# Keep the localhost site permanently live on macOS: auto-start at login,
# auto-restart on crash, via user-level launchd agents (no admin required).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NODE_BIN="$(command -v node || echo "$HOME/opt/node22/bin/node")"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$REPO/logs"

make_plist() {
cat > "$AGENTS/com.premarkethunter.$1.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.premarkethunter.$1</string>
  <key>WorkingDirectory</key><string>$2</string>
  <key>ProgramArguments</key><array>$3</array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$(dirname "$NODE_BIN"):/usr/bin:/bin</string>
    <key>NODE_ENV</key><string>production</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$REPO/logs/$1.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/$1.log</string>
</dict>
</plist>
PLIST
}

make_plist backend "$REPO/backend" "<string>$REPO/.venv/bin/python</string><string>-m</string><string>uvicorn</string><string>app.main:app</string><string>--host</string><string>0.0.0.0</string><string>--port</string><string>8000</string>"
make_plist frontend "$REPO/frontend" "<string>$NODE_BIN</string><string>$REPO/frontend/node_modules/next/dist/bin/next</string><string>start</string><string>-p</string><string>3002</string>"

for svc in backend frontend; do
  launchctl unload "$AGENTS/com.premarkethunter.$svc.plist" 2>/dev/null || true
  launchctl load "$AGENTS/com.premarkethunter.$svc.plist"
done
echo "✓ launchd agents installed — dashboard stays live at http://localhost:3002"
echo "  stop:  launchctl unload ~/Library/LaunchAgents/com.premarkethunter.{backend,frontend}.plist"
