#!/bin/bash
# ============================================================
# x402 Scanner Auto-Deploy
# Starts server + tunnel, stays alive via systemd or screen
# Usage: ./deploy.sh [start|stop|status|url]
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
PORT=8742
TUNNEL_PORT=80
PID_FILE="/tmp/x402-server.pid"
TUNNEL_PID_FILE="/tmp/x402-tunnel.pid"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

# ─── Helpers ────────────────────────────────────────────

is_running() {
    [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

get_url() {
    # Extract tunnel URL from log
    grep -oP 'https://[a-z0-9]+\.lhr\.life' "$LOG_DIR/tunnel.log" 2>/dev/null | tail -1
}

# ─── Start ───────────────────────────────────────────────

start_server() {
    if is_running "$PID_FILE"; then
        echo "⚠️  Server already running (PID $(cat $PID_FILE))"
        return
    fi
    echo "🚀 Starting FastAPI server on :$PORT..."
    cd "$SCRIPT_DIR"
    "$VENV/bin/python" server.py --port "$PORT" \
        > "$LOG_DIR/server.log" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 3
    if is_running "$PID_FILE"; then
        echo "✅ Server started (PID $(cat $PID_FILE))"
    else
        echo "❌ Server failed to start. Check $LOG_DIR/server.log"
        return 1
    fi
}

start_tunnel() {
    if is_running "$TUNNEL_PID_FILE"; then
        echo "⚠️  Tunnel already running (PID $(cat $TUNNEL_PID_FILE))"
        return
    fi
    echo "🌐 Starting SSH tunnel to localhost.run..."
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -o ExitOnForwardFailure=yes \
        -R "$TUNNEL_PORT:localhost:$PORT" \
        nokey@localhost.run \
        > "$LOG_DIR/tunnel.log" 2>&1 &
    echo $! > "$TUNNEL_PID_FILE"
    
    # Wait for URL to appear
    for i in $(seq 1 15); do
        sleep 2
        URL=$(get_url)
        if [ -n "$URL" ]; then
            echo "✅ Tunnel active: $URL"
            echo "$URL" > /tmp/x402-url.txt
            return 0
        fi
    done
    echo "❌ Tunnel failed to connect. Check $LOG_DIR/tunnel.log"
    return 1
}

# ─── Stop ────────────────────────────────────────────────

stop() {
    for pid_file in "$PID_FILE" "$TUNNEL_PID_FILE"; do
        if is_running "$pid_file"; then
            PID=$(cat "$pid_file")
            kill "$PID" 2>/dev/null
            echo "🛑 Stopped PID $PID"
            rm -f "$pid_file"
        fi
    done
}

# ─── Status ──────────────────────────────────────────────

status() {
    echo "═══════════════════════════════════"
    echo "  x402 Scanner Status"
    echo "═══════════════════════════════════"
    
    if is_running "$PID_FILE"; then
        echo "Server:  ✅ Running (PID $(cat $PID_FILE))"
        if curl -s "http://localhost:$PORT/version" > /dev/null 2>&1; then
            VER=$(curl -s "http://localhost:$PORT/version")
            echo "         $VER"
        fi
    else
        echo "Server:  ❌ Stopped"
    fi
    
    if is_running "$TUNNEL_PID_FILE"; then
        URL=$(get_url)
        echo "Tunnel:  ✅ Active"
        echo "URL:     $URL"
    else
        echo "Tunnel:  ❌ Stopped"
    fi
    
    echo "═══════════════════════════════════"
}

# ─── Install as systemd service ──────────────────────────

install_service() {
    SERVICE_FILE="/etc/systemd/system/x402-scanner.service"
    if [ -f "$SERVICE_FILE" ]; then
        echo "⚠️  Service already installed"
        return
    fi
    
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=x402 Multi-Factor Scanner
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/deploy.sh start
ExecStop=$SCRIPT_DIR/deploy.sh stop
Restart=on-failure
RestartSec=10
StandardOutput=append:$LOG_DIR/service.log
StandardError=append:$LOG_DIR/service.log

[Install]
WantedBy=multi-user.target
EOF
    
    sudo systemctl daemon-reload
    sudo systemctl enable x402-scanner
    echo "✅ Installed systemd service: x402-scanner"
    echo "   sudo systemctl start x402-scanner"
}

# ─── Main ────────────────────────────────────────────────

case "${1:-}" in
    start)
        start_server && start_tunnel
        status
        ;;
    stop)
        stop
        echo "✅ All processes stopped"
        ;;
    restart)
        stop
        sleep 2
        start_server && start_tunnel
        status
        ;;
    status)
        status
        ;;
    url)
        URL=$(get_url)
        if [ -n "$URL" ]; then
            echo "$URL"
        else
            echo "No active tunnel URL" >&2
            exit 1
        fi
        ;;
    install)
        install_service
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|url|install}"
        echo ""
        echo "  start    - Start server + tunnel"
        echo "  stop     - Stop all processes"
        echo "  restart  - Restart everything"
        echo "  status   - Show running status"
        echo "  url      - Print current tunnel URL"
        echo "  install  - Install as systemd service (auto-start on boot)"
        exit 1
        ;;
esac
