"""Salva il permesso GitHub (fine-grained token) usato dal rinnovo automatico del token Instagram.

Da lanciare sul PC:  .venv\\Scripts\\python.exe strumenti\\salva_permesso_github.py
"""
import getpass
import re
import subprocess
import sys

import requests

REPO = __import__("os").environ.get("REPO") or input("Repository GitHub (utente/nome): ").strip()
GH = r"C:\Program Files\GitHub CLI\gh.exe"


def main():
    print("\n=== Permesso GitHub per il rinnovo automatico ===")
    print("Per incollare usa il TASTO DESTRO del mouse (non Ctrl+V), una sola volta.\n")
    while True:
        grezzo = getpass.getpass("Incolla il codice github_pat_… (non si vedrà) e premi Invio: ")
        trovati = set(re.findall(r"github_pat_[A-Za-z0-9_]{50,120}", grezzo))
        if len(trovati) != 1:
            pulito = "".join(c for c in grezzo if c.isprintable())
            inizio = pulito[:11] if pulito.startswith(("github_pat_", "ghp_")) else pulito[:3]
            print(f"   ✗ Non trovo un codice github_pat_ valido. Ho ricevuto {len(grezzo)} caratteri "
                  f"({len(pulito)} visibili), che iniziano con «{inizio}…». Riprova.")
            continue
        token = trovati.pop()
        # Prova reale: il permesso deve poter leggere la chiave pubblica dei secret di questo repository.
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=30,
        )
        if r.status_code == 200:
            break
        if r.status_code == 401:
            print("   ✗ GitHub non riconosce il codice (forse copiato male o già scaduto). Riprova.")
        else:
            print(f"   ✗ Il codice funziona ma non ha il permesso giusto ({r.status_code}): "
                  "serve 'Secrets: Read and write' su questo repository.")
            sys.exit(1)

    res = subprocess.run([GH, "secret", "set", "GH_PAT_SECRETS", "--repo", REPO], input=token, text=True, capture_output=True)
    if res.returncode != 0:
        sys.exit(f"Errore nel salvataggio: {res.stderr.strip()}")
    print("\n✅ Permesso verificato e salvato su GitHub. Scrivi «fatto» a Claude.\n")


if __name__ == "__main__":
    main()
