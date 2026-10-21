"""Manda al titolare su Telegram un messaggio scritto in un file di Dropbox (/avvisi/<nome>.md della cartella dell'app).

Il testo NON passa da GitHub: nel repository pubblico si vedono solo il nome del file e l'esito.
Dopo l'invio il file viene segnato come inviato (<nome>.inviato.json) per non mandarlo due volte.
"""
import os
import re
import sys

from . import avvisi
from .dropbox_client import Dropbox, DropboxNonTrovato
from .main import ora_iso

CARTELLA = "/avvisi"


def principale():
    nome = os.environ.get("FILE_MESSAGGIO", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", nome):
        print("Nome del file non valido.")
        return 1
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    base = nome[:-3] if nome.endswith(".md") else nome
    try:
        dbx.leggi_json(f"{CARTELLA}/{base}.inviato.json")
        print("Messaggio già inviato.")
        return 0
    except (DropboxNonTrovato, ValueError):
        pass
    try:
        testo = dbx.leggi_testo(f"{CARTELLA}/{base}.md")
    except DropboxNonTrovato:
        print("File del messaggio non trovato.")
        return 1
    avvisi.invia(testo.strip())
    dbx.scrivi_json(f"{CARTELLA}/{base}.inviato.json", {"inviato_il": ora_iso()})
    print("Messaggio inviato.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
