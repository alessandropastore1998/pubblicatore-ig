"""Rinnova il token di Instagram (dura 60 giorni) e lo salva come secret IG_TOKEN del repository.

Gira ogni settimana su GitHub Actions. Per aggiornare il secret usa `gh` con il permesso GH_PAT_SECRETS
(fine-grained, solo questo repository, solo "Secrets: read and write").
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

import requests

from . import avvisi
from .instagram import ErroreInstagram, Instagram


def main():
    try:
        nuovo, secondi = Instagram(os.environ["IG_TOKEN"]).rinnova_token()
    except (ErroreInstagram, KeyError) as e:
        avvisi.invia(
            f"🔑❌ Rinnovo del token Instagram FALLITO: {e}\n"
            "Se non si risolve, il token scade entro 60 giorni dall'ultimo rinnovo riuscito. Vedi README → «Token»."
        )
        return 1
    print(f"::add-mask::{nuovo}")  # non far mai comparire il token nei log
    risultato = subprocess.run(
        ["gh", "secret", "set", "IG_TOKEN", "--repo", os.environ["GITHUB_REPOSITORY"]],
        input=nuovo, text=True, capture_output=True,
        env={**os.environ, "GH_TOKEN": os.environ.get("GH_PAT_SECRETS", "")},
    )
    if risultato.returncode != 0:
        avvisi.invia(
            "🔑❌ Token Instagram rinnovato ma NON salvato su GitHub "
            f"({risultato.stderr.strip()[:200]}). Controlla il secret GH_PAT_SECRETS (README → «Token»)."
        )
        return 1
    scadenza = datetime.now() + timedelta(seconds=secondi)
    print(f"Token rinnovato e salvato. Nuova scadenza: {scadenza:%d/%m/%Y}.")
    controlla_scadenza_permesso()
    return 0


def controlla_scadenza_permesso():
    """GH_PAT_SECRETS scade (di solito dopo 1 anno): avviso su Telegram nelle ultime 5 settimane, una volta a settimana."""
    try:
        r = requests.get("https://api.github.com/rate_limit", timeout=30,
                         headers={"Authorization": f"Bearer {os.environ.get('GH_PAT_SECRETS', '')}"})
        valore = r.headers.get("github-authentication-token-expiration", "")
    except requests.RequestException:
        return
    if not valore:
        print("Permesso GitHub senza scadenza (o scadenza non leggibile).")
        return
    scade = datetime.strptime(valore[:19], "%Y-%m-%d %H:%M:%S")
    giorni = (scade - datetime.now()).days
    print(f"Permesso GitHub: scade tra {giorni} giorni.")
    if giorni <= 35:
        avvisi.invia(
            f"🔑⏳ Il permesso GitHub per il rinnovo del token scade il {scade:%d/%m/%Y} (tra {giorni} giorni).\n"
            "Rifallo prima: github.com/settings/personal-access-tokens → «Regenerate token» → stessa scelta "
            "(solo questo repository, Secrets: Read and write) → salvalo con lo script del README (2 minuti)."
        )


if __name__ == "__main__":
    sys.exit(main())
