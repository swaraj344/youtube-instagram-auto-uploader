"""Entry point: python -m webapp  →  http://127.0.0.1:5001"""

from __future__ import annotations

import os
import webbrowser

from dotenv import load_dotenv

from webapp.app import create_app
from webapp.services import ROOT


def main() -> None:
    os.chdir(ROOT)  # relative paths (state/, channels.json) resolve to the repo
    load_dotenv()
    app = create_app()
    webbrowser.open("http://127.0.0.1:5001/")
    # 5001, not 5000 — macOS AirPlay Receiver squats on 5000.
    app.run(host="127.0.0.1", port=5001, threaded=True, debug=False)


if __name__ == "__main__":
    main()
