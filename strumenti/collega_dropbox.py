"""Collega l'app Dropbox al pubblicatore e salva le chiavi come secret di GitHub.

Da lanciare UNA volta sul PC:  .venv\\Scripts\\python.exe strumenti\\collega_dropbox.py
Le chiavi si scrivono qui (nascoste) e vanno direttamente su GitHub: non compaiono mai a schermo né in chat.
"""
import getpass
import re
import subprocess
import sys
import webbrowser
from urllib.parse import urlencode

import requests

REPO = __import__("os").environ.get("REPO") or input("Repository GitHub (utente/nome): ").strip()
GH = r"C:\Program Files\GitHub CLI\gh.exe"


def salva_secret(nome, valore):
    r = subprocess.run([GH, "secret", "set", nome, "--repo", REPO], input=valore, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Errore nel salvare {nome} su GitHub: {r.stderr.strip()}")
    print(f"  ✓ {nome} salvato su GitHub")


def pulisci(testo):
    """Toglie caratteri invisibili/strani (es. Ctrl+V nella finestra nascosta) e un eventuale doppio incolla."""
    testo = "".join(c for c in testo if c.isascii() and (c.isalnum() or c in "-_"))
    meta = len(testo) // 2
    if len(testo) % 2 == 0 and meta and testo[:meta] == testo[meta:]:
        testo = testo[:meta]
    return testo


def chiedi_chiave(domanda, nascosta):
    """App key e App secret di Dropbox sono 15 caratteri (lettere minuscole e numeri)."""
    while True:
        grezzo = getpass.getpass(domanda) if nascosta else input(domanda)
        pezzi = set(re.findall(r"[a-z0-9]{15}", grezzo))
        if len(pezzi) == 1 and len(pulisci(grezzo)) <= 32:
            return pezzi.pop()
        print("   ✗ Non sembra giusta. Riprova incollando UNA volta con il tasto destro del mouse.")


def main():
    print("\n=== Collegamento Dropbox ===")
    print("Per incollare usa il TASTO DESTRO del mouse (non Ctrl+V), una sola volta.\n")
    # La App key non è segreta (compare anche negli indirizzi di Dropbox): la mostriamo per poterla controllare.
    app_key = chiedi_chiave("1) Incolla la App key e premi Invio: ", nascosta=False)
    app_secret = chiedi_chiave("2) Incolla la App secret (non si vedrà) e premi Invio: ", nascosta=True)

    url = "https://www.dropbox.com/oauth2/authorize?" + urlencode(
        {"client_id": app_key, "response_type": "code", "token_access_type": "offline"}
    )
    print("\n3) Si apre il browser: clicca «Continua» e poi «Consenti».")
    print("   Se il browser non si apre, copia questo indirizzo nel browser:\n   " + url + "\n")
    webbrowser.open(url)
    codice = pulisci(getpass.getpass("4) Copia il codice che ti mostra Dropbox, incollalo qui (non si vedrà) e premi Invio: "))

    r = requests.post(
        "https://api.dropboxapi.com/oauth2/token",
        data={"code": codice, "grant_type": "authorization_code"},
        auth=(app_key, app_secret),
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Dropbox ha rifiutato il codice ({r.status_code}). Rilancia lo script e usa un codice nuovo.")
    refresh = r.json().get("refresh_token")
    if not refresh:
        sys.exit("Dropbox non ha restituito il refresh token: rilancia lo script.")

    print("\nSalvo le chiavi su GitHub…")
    salva_secret("DROPBOX_APP_KEY", app_key)
    salva_secret("DROPBOX_APP_SECRET", app_secret)
    salva_secret("DROPBOX_REFRESH_TOKEN", refresh)
    print("\n✅ Fatto! Puoi chiudere questa finestra e scrivere «fatto» a Claude.\n")


if __name__ == "__main__":
    main()
