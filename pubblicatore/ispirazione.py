"""Esempi di Reel mandati dal titolare su Telegram: li salva per l'Osservatore Reel.

Il titolare scrive al bot:  /esempio https://www.instagram.com/reel/XXXX/ mi piace il gancio nel primo secondo
Il messaggio finisce in /ispirazione/esempi-telegram.md della cartella del pubblicatore (l'app Dropbox vede solo
quella); l'Osservatore lo legge insieme agli esempi scritti a mano.
Si leggono SOLO i messaggi della chat del titolare (TELEGRAM_CHAT_ID); tutto il resto si ignora.
Gira insieme al ponte DM: se qualcosa va storto non ferma niente.
"""
import os
import re
import sys
from datetime import datetime

import requests

from . import avvisi
from .dropbox_client import Dropbox, DropboxNonTrovato
from .main import ROMA

FILE = "/ispirazione/esempi-telegram.md"
STATO = "/ispirazione/telegram-ultimo.json"
RE_COMANDO = re.compile(r"^/esempio(?:@\w+)?\s+(https?://\S+)\s*(.*)$", re.S | re.I)
INTESTAZIONE = ("# Esempi di Reel mandati su Telegram (comando /esempio)\n"
                "Formato: data · link · perché gli piace. Li legge l'Osservatore Reel (solo schemi, mai copiare).\n\n")


def principale():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("Telegram non configurato.")
        return 0
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    try:
        ultimo = int(dbx.leggi_json(STATO).get("update_id", 0))
    except (DropboxNonTrovato, ValueError):
        ultimo = 0
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                     params={"offset": ultimo + 1, "timeout": 0, "allowed_updates": '["message"]'}, timeout=30)
    if r.status_code != 200:
        print(f"Telegram getUpdates: {r.status_code}")
        return 0
    aggiornamenti = r.json().get("result", [])
    nuovi = []
    for u in aggiornamenti:
        ultimo = max(ultimo, u["update_id"])
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != chat:
            continue
        trovato = RE_COMANDO.match((m.get("text") or "").strip())
        if trovato:
            quando = datetime.fromtimestamp(m.get("date", 0), ROMA).strftime("%Y-%m-%d %H:%M")
            perche = " ".join(trovato.group(2).split())[:300] or "(nessun motivo scritto)"
            nuovi.append(f"- {quando} · {trovato.group(1)} · {perche}\n")
    if nuovi:
        try:
            testo = dbx.leggi_testo(FILE)
        except DropboxNonTrovato:
            testo = INTESTAZIONE
        dbx.scrivi_testo(FILE, testo + "".join(nuovi))
        avvisi.invia(f"💡 Salvato{'i' if len(nuovi) > 1 else ''} {len(nuovi)} esempi{'' if len(nuovi) > 1 else 'o'} "
                     "di Reel per l'Osservatore. Grazie!")
    if aggiornamenti:
        dbx.scrivi_json(STATO, {"update_id": ultimo})
    print(f"Messaggi letti: {len(aggiornamenti)}; esempi salvati: {len(nuovi)}.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
