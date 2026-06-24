#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.apprio.compliance.plist"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_PATH=$(which docker)

mkdir -p "$PROJECT_DIR/logs"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.apprio.compliance</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DOCKER_PATH</string>
    <string>compose</string>
    <string>-f</string>
    <string>$PROJECT_DIR/docker-compose.yml</string>
    <string>up</string>
    <string>-d</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>StandardOutPath</key>
  <string>$PROJECT_DIR/logs/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT_DIR/logs/launchd_error.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Auto-start configured for Mac login."
echo "The platform will start automatically every time you log in."
echo "To open: http://localhost:3000/dashboard"
echo ""
echo "To remove auto-start: launchctl unload $PLIST && rm $PLIST"
