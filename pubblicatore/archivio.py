"""Archivio dei file del pubblicatore: Google Drive (dal 19/11/2026) oppure Dropbox (prima).

Tutto il resto del codice usa percorsi come "/post/2026-10-15-assisi/post.json", relativi alla cartella del
pubblicatore. Su Google Drive quella cartella è indicata dal secret GDRIVE_CARTELLA_ID; su Dropbox era la cartella
dell'app. Si sceglie da sola: se ci sono i secret GDRIVE_* si usa Google Drive, altrimenti Dropbox.
Per tornare a Dropbox basta cancellare il secret GDRIVE_REFRESH_TOKEN (i secret DROPBOX_* restano).

Su Google Drive niente si cancella davvero: "cancella" sposta nel cestino (Drive lo svuota da solo dopo 30 giorni).
"""
import json
import os
import time
from pathlib import Path

import requests

from .dropbox_client import Dropbox, DropboxNonTrovato

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CARTELLA_MIME = "application/vnd.google-apps.folder"
# Link diretto al file (serve a Instagram e al montaggio). Si può cambiare senza toccare il codice con la
# variabile GDRIVE_LINK, dove {id} è l'identificativo del file.
LINK = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"

NonTrovato = DropboxNonTrovato  # stesso errore per entrambi gli archivi: il resto del codice non cambia


def apri():
    """L'archivio giusto in base ai secret presenti."""
    if os.environ.get("GDRIVE_REFRESH_TOKEN"):
        return GoogleDrive(os.environ["GDRIVE_CLIENT_ID"], os.environ["GDRIVE_CLIENT_SECRET"],
                           os.environ["GDRIVE_REFRESH_TOKEN"], os.environ["GDRIVE_CARTELLA_ID"])
    return Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])


def _q(nome):
    return nome.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDrive:
    nome = "Google Drive"

    def __init__(self, client_id, client_secret, refresh_token, cartella_id):
        r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                                           "client_id": client_id, "client_secret": client_secret}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Google Drive: accesso negato ({r.status_code}). Controlla i secret GDRIVE_*.")
        self._auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self._radice = cartella_id
        self._id = {"": cartella_id}       # percorso → id (solo cartelle e file già visti in questo giro)

    # ─────────── chiamate con ripetizione su errori temporanei ───────────
    def _chiama(self, metodo, url, **kw):
        kw.setdefault("timeout", 60)
        headers = dict(self._auth, **kw.pop("headers", {}))
        for tentativo in range(5):
            r = requests.request(metodo, url, headers=headers, **kw)
            if r.status_code in (429, 500, 502, 503, 504) or (r.status_code == 403 and "rateLimit" in r.text):
                time.sleep(2 ** tentativo)
                continue
            return r
        return r

    def _json(self, metodo, url, **kw):
        r = self._chiama(metodo, url, **kw)
        if r.status_code == 404:
            raise NonTrovato(url)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"Google Drive {metodo}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    # ─────────── percorsi ───────────
    @staticmethod
    def _norm(percorso):
        return "/".join(p for p in str(percorso).split("/") if p)

    def _figlio(self, padre, nome):
        res = self._json("GET", API, params={
            "q": f"'{padre}' in parents and name = '{_q(nome)}' and trashed = false",
            "fields": "files(id,mimeType)", "orderBy": "modifiedTime desc", "pageSize": 10})
        return res["files"][0] if res.get("files") else None

    def _trova(self, percorso, crea_cartelle=False):
        """id del file o della cartella; NonTrovato se manca (con crea_cartelle crea le cartelle mancanti)."""
        percorso = self._norm(percorso)
        if percorso in self._id:
            return self._id[percorso]
        padre_path, _, nome = percorso.rpartition("/")
        padre = self._trova(padre_path, crea_cartelle)
        voce = self._figlio(padre, nome)
        if voce is None:
            if not crea_cartelle:
                raise NonTrovato("/" + percorso)
            voce = self._json("POST", API, params={"fields": "id"},
                              json={"name": nome, "mimeType": CARTELLA_MIME, "parents": [padre]})
        self._id[percorso] = voce["id"]
        return voce["id"]

    def esiste(self, percorso):
        try:
            self._trova(percorso)
            return True
        except NonTrovato:
            return False

    # ─────────── lettura ───────────
    def elenca(self, percorso):
        """Come Dropbox: voci con "name", ".tag" ("folder"/"file"), "size", "server_modified"."""
        padre = self._trova(percorso)
        voci, pagina = [], None
        while True:
            params = {"q": f"'{padre}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)"}
            if pagina:
                params["pageToken"] = pagina
            res = self._json("GET", API, params=params)
            for f in res.get("files", []):
                cartella = f["mimeType"] == CARTELLA_MIME
                voci.append({"name": f["name"], ".tag": "folder" if cartella else "file", "id": f["id"],
                             "size": int(f.get("size") or 0), "server_modified": f.get("modifiedTime")})
                self._id.setdefault(self._norm(f"{percorso}/{f['name']}"), f["id"])
            pagina = res.get("nextPageToken")
            if not pagina:
                return voci

    def scarica(self, percorso):
        r = self._chiama("GET", f"{API}/{self._trova(percorso)}", params={"alt": "media"}, timeout=300)
        if r.status_code == 404:
            raise NonTrovato(percorso)
        if r.status_code != 200:
            raise RuntimeError(f"Google Drive download {percorso}: {r.status_code} {r.text[:300]}")
        return r.content

    def leggi_testo(self, percorso):
        return self.scarica(percorso).decode("utf-8-sig")

    def leggi_json(self, percorso):
        return json.loads(self.leggi_testo(percorso))

    # ─────────── scrittura ───────────
    def _carica(self, percorso, dati, mime):
        """Crea o sovrascrive il file (stesso id: i link già dati restano validi). Caricamento a pezzi unico."""
        percorso = self._norm(percorso)
        padre_path, _, nome = percorso.rpartition("/")
        try:
            fid = self._trova(percorso)
            r = self._chiama("PATCH", f"{UPLOAD}/{fid}", params={"uploadType": "resumable"},
                             headers={"X-Upload-Content-Type": mime}, json={})
        except NonTrovato:
            padre = self._trova(padre_path, crea_cartelle=True)
            r = self._chiama("POST", UPLOAD, params={"uploadType": "resumable", "fields": "id"},
                             headers={"X-Upload-Content-Type": mime}, json={"name": nome, "parents": [padre]})
        if r.status_code != 200 or "Location" not in r.headers:
            raise RuntimeError(f"Google Drive: caricamento non riuscito ({r.status_code}) {r.text[:200]}")
        r = self._chiama("PUT", r.headers["Location"], data=dati, headers={"Content-Type": mime}, timeout=600)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Google Drive: caricamento non riuscito ({r.status_code}) {r.text[:200]}")
        self._id[percorso] = r.json()["id"]

    def scrivi_testo(self, percorso, testo):
        self._carica(percorso, testo.encode("utf-8"), "text/plain")

    def scrivi_json(self, percorso, dati):
        self._carica(percorso, (json.dumps(dati, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), "application/json")

    def carica_file(self, percorso, file_locale):
        nome = str(file_locale).lower()
        mime = "video/mp4" if nome.endswith(".mp4") else "image/jpeg" if nome.endswith((".jpg", ".jpeg")) \
            else "audio/mpeg" if nome.endswith(".mp3") else "application/octet-stream"
        self._carica(percorso, Path(file_locale).read_bytes(), mime)

    def link_pubblico(self, percorso):
        """Link diretto che Instagram può scaricare: il file diventa leggibile da chi ha il link."""
        fid = self._trova(percorso)
        r = self._chiama("POST", f"{API}/{fid}/permissions", json={"role": "reader", "type": "anyone"})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Google Drive: link non creato ({r.status_code}) {r.text[:200]}")
        return os.environ.get("GDRIVE_LINK", LINK).format(id=fid)

    def cancella(self, percorso):
        """Sposta nel cestino di Drive (recuperabile per 30 giorni). False se non c'era."""
        try:
            fid = self._trova(percorso)
        except NonTrovato:
            return False
        self._json("PATCH", f"{API}/{fid}", json={"trashed": True})
        percorso = self._norm(percorso)
        for k in [k for k in self._id if k == percorso or k.startswith(percorso + "/")]:
            del self._id[k]
        return True
