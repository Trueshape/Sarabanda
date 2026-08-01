"""
Mini server web usato SOLO per Render (piano free).

Render spegne i Web Service gratuiti dopo ~15 minuti senza richieste HTTP.
Questo modulo espone un endpoint "/" che risponde OK; un cron job esterno
(es. cron-job.org, UptimeRobot) lo chiama ogni 10 minuti per tenere il
servizio sveglio, così il bot Discord resta connesso 24/7.

Se non usi Render (es. VPS, PC sempre acceso) puoi ignorare questo file.
"""

import os
import threading

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Il bot per il quiz musicale è vivo! 🎵"


def _run():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """Avvia il server Flask in un thread separato, senza bloccare il bot Discord."""
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
