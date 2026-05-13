#!/bin/bash
# x402 scanner keepalive — auto-restarts if tunnel dies
# Add to crontab: @reboot bash /home/thomas/arc-onboard/x402-scanner/keepalive.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

while true; do
    if ! curl -s "http://localhost:8742/version" > /dev/null 2>&1; then
        echo "[$(date)] Restarting scanner..."
        bash "$SCRIPT_DIR/deploy.sh" restart >> "$SCRIPT_DIR/logs/keepalive.log" 2>&1
    fi
    sleep 60
done
