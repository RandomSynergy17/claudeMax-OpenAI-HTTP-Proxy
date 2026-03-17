#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — Automated installer for claudeMax-OpenAI-HTTP-Proxy
#
# Sets up the Python virtual environment, installs dependencies, configures
# the systemd user service, and starts the proxy.
#
# Usage:
#   chmod +x install.sh
#   ./install.sh [--port PORT] [--host HOST] [--install-dir DIR]
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Defaults ──
PORT="${PORT:-4000}"
HOST="${HOST:-0.0.0.0}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/claude-proxy}"
VENV_DIR="${VENV_DIR:-$HOME/claude-proxy-venv}"
SERVICE_NAME="claude-proxy"

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --venv-dir) VENV_DIR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--port PORT] [--host HOST] [--install-dir DIR] [--venv-dir DIR]"
            echo ""
            echo "Options:"
            echo "  --port PORT        Port to listen on (default: 4000)"
            echo "  --host HOST        Host to bind to (default: 0.0.0.0)"
            echo "  --install-dir DIR  Where to install server.py (default: ~/claude-proxy)"
            echo "  --venv-dir DIR     Where to create the venv (default: ~/claude-proxy-venv)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  claudeMax-OpenAI-HTTP-Proxy Installer                  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Preflight checks ──
echo "[1/6] Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 10 ]]; then
    echo "ERROR: Python 3.10+ is required. Found Python $PYTHON_VERSION." >&2
    exit 1
fi
echo "  Python $PYTHON_VERSION ✓"

if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI is not installed." >&2
    echo "  Install it from: https://docs.anthropic.com/en/docs/claude-code" >&2
    exit 1
fi
CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
echo "  Claude Code CLI $CLAUDE_VERSION ✓"

# ── Install files ──
echo ""
echo "[2/6] Installing server files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/server.py"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
echo "  Copied server.py and requirements.txt ✓"

# ── Create virtual environment ──
echo ""
echo "[3/6] Setting up Python virtual environment at $VENV_DIR..."
if [[ -d "$VENV_DIR" ]]; then
    echo "  Existing venv found, updating..."
else
    python3 -m venv "$VENV_DIR"
    echo "  Created venv ✓"
fi

source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$INSTALL_DIR/requirements.txt"
echo "  Dependencies installed ✓"

# ── Configure systemd service ──
echo ""
echo "[4/6] Configuring systemd user service..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

CLAUDE_BIN_DIR="$(dirname "$(command -v claude)")"

cat > "$SYSTEMD_DIR/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=Claude Code OpenAI-compatible API Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/server.py --port ${PORT} --host ${HOST}
Environment=PATH=${CLAUDE_BIN_DIR}:/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

echo "  Service file written ✓"

# ── Enable and start ──
echo ""
echo "[5/6] Enabling and starting service..."
systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service" --quiet
systemctl --user restart "${SERVICE_NAME}.service"
echo "  Service started ✓"

# ── Enable linger ──
echo ""
echo "[6/6] Enabling linger for boot persistence..."
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
    echo "  Linger enabled ✓"
else
    echo "  loginctl not found — skipping linger (service won't auto-start at boot)"
fi

# ── Done ──
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Installation complete!                                  ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  API endpoint: http://${HOST}:${PORT}/v1              ║"
echo "║  Health check: http://localhost:${PORT}/health            ║"
echo "║                                                          ║"
echo "║  Service management:                                     ║"
echo "║    systemctl --user status  ${SERVICE_NAME}              ║"
echo "║    systemctl --user restart ${SERVICE_NAME}              ║"
echo "║    systemctl --user stop    ${SERVICE_NAME}              ║"
echo "║    journalctl --user -u ${SERVICE_NAME} -f               ║"
echo "║                                                          ║"
echo "╚══════════════════════════════════════════════════════════╝"
