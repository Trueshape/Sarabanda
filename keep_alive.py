"""
Small web server used ONLY for Render (free plan).

Render puts free Web Services to sleep after ~15 minutes without HTTP
requests. This module exposes a "/" endpoint that responds OK; an external
cron job (e.g. cron-job.org, UptimeRobot) calls it every 10 minutes to keep
the service awake, so the Discord bot stays connected 24/7.

If you're not using Render (e.g. a VPS, an always-on PC) you can ignore this file.
"""

import os
import threading

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "The music quiz bot is alive! 🎵"


def _run():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """Starts the Flask server in a separate thread, without blocking the Discord bot."""
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
