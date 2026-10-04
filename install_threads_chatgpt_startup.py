"""Install the passive Threads -> ChatGPT bridge into Windows Startup.

No admin rights are required. The generated .cmd launches the collector minimized
when the current Windows user signs in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not available")

    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    target = startup / "ThreadsChatGPTBridge.cmd"

    python = Path(sys.executable)
    script = BASE_DIR / "threads_to_chatgpt.py"
    log = BASE_DIR / "threads_chatgpt_bridge.log"

    content = (
        "@echo off\r\n"
        f'cd /d "{BASE_DIR}"\r\n'
        f'start "" /min "{python}" "{script}" >> "{log}" 2>&1\r\n'
    )
    target.write_text(content, encoding="utf-8")
    print(f"Installed: {target}")
    print("The collector will start automatically at the next Windows sign-in.")
    print("To start it now, run: python threads_to_chatgpt.py")


if __name__ == "__main__":
    main()
