"""Collega Google Drive al pubblicatore e salva le chiavi come secret di GitHub (al posto di Dropbox).

Da lanciare UNA volta sul PC:  .venv\\Scripts\\python.exe strumenti\\collega_drive.py
Prima serve il file JSON del "client OAuth" (tipo "App desktop") scaricato da Google Cloud Console nella cartella
Download. Le chiavi vanno direttamente su GitHub: non compaiono mai a schermo né in chat.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

REPO = os.environ.get("REPO") or input("Repository GitHub (utente/nome): ").strip()
GH = r"C:\Program Files\GitHub CLI\gh.exe"
SCOPE = "https://www.googleapis.com/auth/drive"
API = "https://www.googleapis.com/drive/v3"
CARTELLE = {"GDRIVE_CARTELLA_ID": "ToTravelPubblicatore"}  # la cartella del pubblicatore in "Il mio Drive"


def salva_secret(nome, valore):
    r = subprocess.run([GH, "secret", "set", nome, "--repo", REPO], input=valore, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Errore nel salvare {nome} su GitHub: {r.stderr.strip()}")
    print(f"  ✓ {nome} salvato su GitHub")


def trova_client():
    cartella = Path.home() / "Downloads"
    candidati = sorted(cartella.glob("client_secret_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidati:
        percorso = input("Non trovo client_secret_….json in Download. Trascina qui il file e premi Invio: ").strip().strip('"')
        candidati = [Path(percorso)]
    dati = json.loads(candidati[0].read_text(encoding="utf-8"))
    if "installed" not in dati:
        sys.exit("Il file non è di tipo «App desktop». Ricrea il client OAuth scegliendo «App desktop».")
    print(f"Uso il file {candidati[0].name}")
    return dati["installed"]["client_id"], dati["installed"]["client_secret"], candidati[0]


def ricevi_codice(client_id):
    """Apre il browser sul consenso Google e aspetta il codice su 127.0.0.1 (nessun copia-incolla)."""
    ricevuto = {}

    class Gestore(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlsplit(self.path).query)
            if "code" in q or "error" in q:
                ricevuto.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Fatto. Puoi chiudere questa pagina e tornare alla finestra nera.</h2>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Gestore)
    redirect = f"http://127.0.0.1:{server.server_port}"
    verifica = secrets.token_urlsafe(64)
    sfida = base64.urlsafe_b64encode(hashlib.sha256(verifica.encode()).digest()).decode().rstrip("=")
    stato = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": stato,
        "code_challenge": sfida, "code_challenge_method": "S256"})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("\nSi apre il browser: scegli l'account Google GIUSTO (quello col Drive dei file copiati),")
    print("poi «Avanzate» → «Vai a … (non sicura)» se compare l'avviso, e infine «Continua».")
    print(f"\nSe il browser non si apre, copia questo indirizzo nel browser:\n{url}\n")
    webbrowser.open(url)
    while "code" not in ricevuto and "error" not in ricevuto:
        threading.Event().wait(0.5)
    server.shutdown()
    if ricevuto.get("state") != stato or "error" in ricevuto:
        sys.exit(f"Google non ha dato il permesso ({ricevuto.get('error', 'risposta non valida')}). Rilancia lo script.")
    return ricevuto["code"], redirect, verifica


def main():
    print("\n=== Collegamento Google Drive ===")
    client_id, client_secret, file_client = trova_client()
    codice, redirect, verifica = ricevi_codice(client_id)
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "code": codice, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifica}, timeout=30)
    if r.status_code != 200 or "refresh_token" not in r.json():
        sys.exit(f"Google ha rifiutato il codice ({r.status_code}). Rilancia lo script.")
    token = r.json()
    if SCOPE not in token.get("scope", ""):
        sys.exit("Manca il permesso completo su Drive: rilancia e spunta la casella di Google Drive nella pagina del consenso.")
    auth = {"Authorization": f"Bearer {token['access_token']}"}

    chi = requests.get(f"{API}/about", params={"fields": "user(emailAddress),storageQuota"}, headers=auth, timeout=30).json()
    email = chi["user"]["emailAddress"]
    quota = chi.get("storageQuota", {})
    print(f"\nAccount collegato: {email}")
    if quota.get("limit"):
        print(f"Spazio: {int(quota['usage']) / 1e9:.2f} GB usati su {int(quota['limit']) / 1e9:.0f} GB")
    atteso = os.environ.get("ACCOUNT_ATTESO", "").strip().lower()
    if atteso:
        if email.lower() != atteso:
            sys.exit(f"Account sbagliato (atteso {atteso}): rilancia lo script e scegli l'altro account.")
    elif input("È l'account giusto? (s/n): ").strip().lower() not in ("s", "si", "sì"):
        sys.exit("Fermo tutto: rilancia lo script e scegli l'altro account.")

    trovate = {}
    for secret, nome in CARTELLE.items():
        res = requests.get(f"{API}/files", headers=auth, timeout=30, params={
            "q": f"name = '{nome}' and 'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            "fields": "files(id,name)"}).json()
        if len(res.get("files", [])) != 1:
            sys.exit(f"In «Il mio Drive» trovo {len(res.get('files', []))} cartelle «{nome}» (ne serve esattamente una).")
        trovate[secret] = res["files"][0]["id"]
        figli = requests.get(f"{API}/files", headers=auth, timeout=30, params={
            "q": f"'{trovate[secret]}' in parents and trashed = false", "fields": "files(name)", "pageSize": 100}).json()
        nomi = sorted(f["name"] for f in figli.get("files", []))
        print(f"✓ Trovata «{nome}» con dentro: {', '.join(nomi[:12])}{' …' if len(nomi) > 12 else ''}")
        if "post" not in nomi:
            sys.exit(f"«{nome}» non contiene la cartella post: controlla la copia e rilancia.")

    print("\nSalvo le chiavi su GitHub…")
    salva_secret("GDRIVE_CLIENT_ID", client_id)
    salva_secret("GDRIVE_CLIENT_SECRET", client_secret)
    for secret, valore in trovate.items():
        salva_secret(secret, valore)
    salva_secret("GDRIVE_REFRESH_TOKEN", token["refresh_token"])  # per ultimo: da qui il pubblicatore usa Drive
    print(f"\nFatto! Da ora il pubblicatore legge e scrive su Google Drive.\n"
          f"Puoi cancellare il file {file_client.name} dalla cartella Download (le chiavi sono già su GitHub).")


if __name__ == "__main__":
    main()
