"""Accesso minimo a Dropbox (app con accesso a una sola cartella: /Apps/<nome app>/).

I percorsi qui sono relativi alla cartella dell'app: "/post/2026-10-15-assisi/post.json".
"""
import json

import requests

TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
RPC = "https://api.dropboxapi.com/2/"
CONTENT = "https://content.dropboxapi.com/2/"


class DropboxNonTrovato(Exception):
    pass


class Dropbox:
    def __init__(self, app_key, app_secret, refresh_token):
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(app_key, app_secret),
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Dropbox: accesso negato ({r.status_code}). Controlla i secret DROPBOX_*.")
        self._auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def _rpc(self, endpoint, payload):
        r = requests.post(RPC + endpoint, headers=self._auth, json=payload, timeout=30)
        if r.status_code == 409 and "not_found" in r.text:
            raise DropboxNonTrovato(payload.get("path", ""))
        if r.status_code != 200:
            raise RuntimeError(f"Dropbox {endpoint}: {r.status_code} {r.text[:300]}")
        return r.json()

    def elenca(self, percorso):
        res = self._rpc("files/list_folder", {"path": percorso})
        voci = res["entries"]
        while res.get("has_more"):
            res = self._rpc("files/list_folder/continue", {"cursor": res["cursor"]})
            voci += res["entries"]
        return voci

    def leggi_testo(self, percorso):
        headers = dict(self._auth)
        # json.dumps con ensure_ascii: l'header deve essere ASCII (Dropbox accetta gli escape \uXXXX).
        headers["Dropbox-API-Arg"] = json.dumps({"path": percorso})
        r = requests.post(CONTENT + "files/download", headers=headers, timeout=30)
        if r.status_code == 409:
            raise DropboxNonTrovato(percorso)
        if r.status_code != 200:
            raise RuntimeError(f"Dropbox download {percorso}: {r.status_code} {r.text[:300]}")
        return r.content.decode("utf-8-sig")

    def leggi_json(self, percorso):
        return json.loads(self.leggi_testo(percorso))

    def scrivi_testo(self, percorso, testo):
        headers = dict(self._auth)
        headers["Dropbox-API-Arg"] = json.dumps({"path": percorso, "mode": "overwrite", "mute": True})
        headers["Content-Type"] = "application/octet-stream"
        r = requests.post(CONTENT + "files/upload", headers=headers, data=testo.encode("utf-8"), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Dropbox upload {percorso}: {r.status_code} {r.text[:300]}")

    def scrivi_json(self, percorso, dati):
        self.scrivi_testo(percorso, json.dumps(dati, ensure_ascii=False, indent=2) + "\n")
