"""Rinnova il token di Instagram (dura 60 giorni) e lo salva come secret IG_TOKEN del repository.

Gira ogni settimana su GitHub Actions. Per aggiornare il secret usa `gh` con il permesso GH_PAT_SECRETS
(fine-grained, solo questo repository, solo "Secrets: read and write").
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
