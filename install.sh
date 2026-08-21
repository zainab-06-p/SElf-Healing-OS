#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Self-Healing OS — one-shot installer
#
# This single command does everything:
#   1. Installs the Python package  (creates 'shos' terminal command)
#   2. Creates a desktop icon       (appears in Linux app launcher)
#   3. Adds ~/.local/bin to PATH    (so 'shos' works immediately)
#
# Usage:
#   chmod +x install.sh && ./install.sh
#
# After install, launch from any terminal:
#   shos                          # uses default DB path
#   shos --db /path/to/db         # custom database
# ──────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${PROJECT_DIR}/data_pipeline/data/self_healing_os.db"

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   Self-Healing OS — Installer                ║"
echo "  ║   AI-powered Linux observability dashboard   ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  Project: $PROJECT_DIR"
echo ""

# ── 1. Install Python package ──────────────────────────────
echo "[1/4] Installing Python package (creates 'shos' command) ..."
if pip3 install --break-system-packages -e "$PROJECT_DIR" 2>/dev/null; then
    echo "      Installed via pip (--break-system-packages)."
elif pip3 install --user -e "$PROJECT_DIR" 2>/dev/null; then
    echo "      Installed via pip (--user)."
elif pip3 install -e "$PROJECT_DIR" 2>/dev/null; then
    echo "      Installed via pip."
else
    echo "      ERROR: pip install failed. Try: sudo pip3 install -e $PROJECT_DIR"
    exit 1
fi
echo ""

# ── 2. Add ~/.local/bin to PATH (for this session + future terminals) ──
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"

add_to_path() {
    local file="$1"
    if ! grep -q '.local/bin' "$file" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$file"
        echo "      Added ~/.local/bin to $file"
    fi
}

echo "[2/4] Ensuring ~/.local/bin is in PATH ..."
export PATH="$LOCAL_BIN:$PATH"
add_to_path "${HOME}/.bashrc"    2>/dev/null || true
add_to_path "${HOME}/.zshrc"     2>/dev/null || true
add_to_path "${HOME}/.profile"   2>/dev/null || true
echo "      Done."
echo ""

# ── 3. Create desktop entry ────────────────────────────────
echo "[3/4] Creating desktop icon + autostart entry ..."
DESKTOP_DIR="${HOME}/.local/share/applications"
AUTOSTART_DIR="${HOME}/.config/autostart"
mkdir -p "$DESKTOP_DIR" "$AUTOSTART_DIR"

# Copy a proper icon if one exists, otherwise use a system icon
ICON_PATH="${PROJECT_DIR}/dashboard/assets/icon.png"
if [ ! -f "$ICON_PATH" ]; then
    ICON_PATH="utilities-terminal"
fi

DESKTOP_CONTENT="[Desktop Entry]
Name=Self-Healing OS
Comment=AI-powered Linux observability & autonomous healing dashboard
Exec=${LOCAL_BIN}/shos --db ${DB_PATH}
Icon=${ICON_PATH}
Terminal=false
Type=Application
Categories=Development;System;Monitor;
StartupNotify=true"

echo "$DESKTOP_CONTENT" > "${DESKTOP_DIR}/self-healing-os.desktop"
chmod +x "${DESKTOP_DIR}/self-healing-os.desktop"

# Autostart: app launches automatically at every login
echo "$DESKTOP_CONTENT
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel" > "${AUTOSTART_DIR}/self-healing-os.desktop"
chmod +x "${AUTOSTART_DIR}/self-healing-os.desktop"

echo "      Desktop icon:  ${DESKTOP_DIR}/self-healing-os.desktop"
echo "      Autostart:     ${AUTOSTART_DIR}/self-healing-os.desktop"
echo ""

# ── 4. Verify installation ─────────────────────────────────
echo "[4/4] Verifying installation ..."
if command -v shos &>/dev/null; then
    SHOS_PATH="$(command -v shos)"
    echo "      shos command: $SHOS_PATH"
else
    echo "      WARNING: 'shos' not found in PATH yet."
    echo "      Open a new terminal or run:  source ~/.bashrc"
fi

if [ -f "$DB_PATH" ]; then
    echo "      Database:     $DB_PATH (found)"
else
    echo "      Database:     $DB_PATH (NOT FOUND — run collector first)"
fi
echo ""

# ── Summary ────────────────────────────────────────────────
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   Installation Complete!                     ║"
echo "  ╠══════════════════════════════════════════════╣"
echo "  ║                                              ║"
echo "  ║   Terminal command:    shos                   ║"
echo "  ║   Custom database:     shos --db /path/to/db ║"
echo "  ║   Desktop icon:        Check your app menu    ║"
echo "  ║   Uninstall:           pip3 uninstall self-healing-os"
echo "  ║                                              ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
