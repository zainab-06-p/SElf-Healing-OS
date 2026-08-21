"""Entry point for the Self-Healing OS dashboard.

This thin launcher adds step3/src to sys.path so the existing app modules
(theme.py, app.py) can be imported without restructuring the project.
"""
import sys
import os
import subprocess


def _ensure_desktop_entry():
    """Create a .desktop file in the user's app menu if one doesn't exist."""
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    desktop_file = os.path.join(desktop_dir, "self-healing-os.desktop")
    if os.path.exists(desktop_file):
        return

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(project_root, "step1", "data", "self_healing_os.db")
    shos_bin = os.path.expanduser("~/.local/bin/shos")

    # Only create if shos command exists (i.e. pip install was done)
    if not os.path.exists(shos_bin):
        return

    try:
        os.makedirs(desktop_dir, exist_ok=True)
        with open(desktop_file, "w") as f:
            f.write(f"""[Desktop Entry]
Name=Self-Healing OS
Comment=AI-powered Linux observability & autonomous healing dashboard
Exec={shos_bin} --db {db_path}
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=Development;System;Monitor;
StartupNotify=true
""")
        os.chmod(desktop_file, 0o755)
    except OSError:
        pass  # Non-critical — app still works without desktop icon


def main():
    """CLI entry point.  Accepts --db to override the SQLite database path."""
    # Auto-create desktop icon on first run
    _ensure_desktop_entry()

    # Resolve the project root (one level up from this file)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    step3_src = os.path.join(project_root, "step3", "src")

    if step3_src not in sys.path:
        sys.path.insert(0, step3_src)

    from app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
