"""Instagram API with Instagram Login (graph.instagram.com) — solo endpoint ufficiali.

Documentazione: https://developers.facebook.com/docs/instagram-platform/content-publishing
"""
import time

import requests

VERSIONE = "v25.0"
API = f"https://graph.instagram.com/{VERSIONE}"


class ErroreInstagram(Exception):
    pass


class Instagram:
    def __init__(self, token):
        self._token = token
        self._s = requests.Session()

    def _chiama(self, metodo, percorso, base=API, **parametri):
        parametri["access_token"] = self._token
        try:
            if metodo == "GET":
                r = self._s.get(f"{base}/{percorso}", params=parametri, timeout=60)
            else:
                r = self._s.post(f"{base}/{percorso}", data=parametri, timeout=120)
        except requests.RequestException as e:
            # Il messaggio di requests può contenere l'URL con il token: non lo riportiamo.
            raise ErroreInstagram(f"rete: {type(e).__name__} su {percorso}") from None
        try:
            dati = r.json()
        except ValueError:
            dati = {}
        if r.status_code >= 400 or "error" in dati:
            err = dati.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
            raise ErroreInstagram(f"{percorso}: {msg} (codice {err.get('code')}/{err.get('error_subcode')})")
        return dati

    def chiama(self, metodo, percorso, **parametri):
        """Chiamata generica all'API (usata per gli Insights)."""
        return self._chiama(metodo, percorso, **parametri)

    def chi_sono(self):
        return self._chiama("GET", "me", fields="user_id,username")

    def quota(self, user_id):
        dati = self._chiama("GET", f"{user_id}/content_publishing_limit", fields="quota_usage,config")
        voce = (dati.get("data") or [{}])[0]
        return voce.get("quota_usage"), (voce.get("config") or {}).get("quota_total")

    def crea_foto(self, user_id, url, carosello=False, storia=False, didascalia=None):
        parametri = {"image_url": url}
        if didascalia is not None:
            parametri["caption"] = didascalia
        if carosello:
            parametri["is_carousel_item"] = "true"
        if storia:
            parametri["media_type"] = "STORIES"
        return self._chiama("POST", f"{user_id}/media", **parametri)["id"]

    def crea_carosello(self, user_id, figli, didascalia):
        return self._chiama(
            "POST", f"{user_id}/media",
            media_type="CAROUSEL", children=",".join(figli), caption=didascalia,
        )["id"]

    def attendi_pronto(self, container_id, max_secondi=300):
        """Aspetta status_code FINISHED. La documentazione consiglia un controllo al minuto per max 5 minuti;
        il primo controllo lo facciamo dopo pochi secondi perché le foto di solito sono pronte subito."""
        attese = [5, 15] + [60] * (max_secondi // 60)
        for attesa in attese:
            time.sleep(attesa)
            dati = self._chiama("GET", container_id, fields="status_code,status")
            stato = dati.get("status_code")
            if stato == "FINISHED":
                return
            if stato in ("ERROR", "EXPIRED"):
                raise ErroreInstagram(f"container {container_id}: {stato} {dati.get('status', '')}")
        raise ErroreInstagram(f"container {container_id}: non pronto dopo {max_secondi} secondi")

    def pubblica(self, user_id, container_id):
        return self._chiama("POST", f"{user_id}/media_publish", creation_id=container_id)["id"]

    def link(self, media_id):
        return self._chiama("GET", media_id, fields="permalink").get("permalink", "")

    def ultimi_post(self, user_id, quanti=5):
        return self._chiama(
            "GET", f"{user_id}/media", fields="id,caption,timestamp,permalink", limit=str(quanti)
        ).get("data", [])

    def rinnova_token(self):
        """Rinnova il token di lunga durata (deve avere almeno 24 ore). Restituisce (nuovo_token, secondi_validità)."""
        dati = self._chiama(
            "GET", "refresh_access_token", base="https://graph.instagram.com", grant_type="ig_refresh_token"
        )
        return dati["access_token"], int(dati.get("expires_in", 0))
