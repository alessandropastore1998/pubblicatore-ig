"""Controllo delle foto prima di darle a Instagram.

Limiti dalla documentazione ufficiale (IG User Media, ottobre 2026): solo JPEG, max 8 MB,
proporzioni tra 4:5 e 1,91:1, larghezza 320-1440 px (fuori da questo intervallo Instagram ridimensiona).
Nel carosello tutte le foto vengono ritagliate come la prima: per questo pretendiamo 4:5 per tutte.
"""
import struct
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

MAX_BYTE = 8 * 1024 * 1024
RAPPORTO_4_5 = 4 / 5
TOLLERANZA = 0.01
# Pexels (e in parte Unsplash) cambiano formato in base all'header Accept: a un browser moderno danno AVIF.
# Controlliamo con l'Accept "peggiore" e forziamo fm=jpg.
ACCEPT_PEGGIORE = "image/avif,image/webp,image/apng,*/*;q=0.8"
HOST_CON_FM = ("images.pexels.com", "images.unsplash.com")


def normalizza_url(url):
    """Aggiunge fm=jpg agli URL Pexels/Unsplash (sostituisce un eventuale fm diverso)."""
    parti = urlsplit(url.strip())
    if parti.hostname not in HOST_CON_FM:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parti.query, keep_blank_values=True) if k != "fm"]
    query.append(("fm", "jpg"))
    return urlunsplit((parti.scheme, parti.netloc, parti.path, urlencode(query), parti.fragment))


def dimensioni_jpeg(dati):
    """Larghezza e altezza leggendo il marcatore SOF del JPEG (senza librerie esterne)."""
    i = 2
    while i + 9 < len(dati):
        if dati[i] != 0xFF:
            i += 1
            continue
        marcatore = dati[i + 1]
        if marcatore in (0xD8, 0x01) or 0xD0 <= marcatore <= 0xD7 or marcatore == 0xFF:
            i += 1 if marcatore == 0xFF else 2
            continue
        lunghezza = struct.unpack(">H", dati[i + 2:i + 4])[0]
        if 0xC0 <= marcatore <= 0xCF and marcatore not in (0xC4, 0xC8, 0xCC):
            altezza, larghezza = struct.unpack(">HH", dati[i + 5:i + 9])
            return larghezza, altezza
        i += 2 + lunghezza
    return None


def controlla(url):
    """Scarica la foto come farebbe Instagram e la controlla.
    Restituisce (url_normalizzato, None) se va bene, oppure (url_normalizzato, "motivo") se no."""
    url = normalizza_url(url)
    try:
        r = requests.get(url, headers={"Accept": ACCEPT_PEGGIORE}, timeout=60)
    except requests.RequestException as e:
        return url, f"non raggiungibile ({type(e).__name__})"
    if r.status_code != 200:
        return url, f"risposta {r.status_code}"
    tipo = r.headers.get("Content-Type", "").split(";")[0].strip()
    dati = r.content
    if tipo != "image/jpeg" or not dati.startswith(b"\xff\xd8\xff"):
        return url, f"non è un JPEG (tipo {tipo or 'sconosciuto'})"
    if len(dati) > MAX_BYTE:
        return url, f"troppo pesante ({len(dati) // 1024} KB, max 8 MB)"
    dim = dimensioni_jpeg(dati)
    if not dim:
        return url, "dimensioni illeggibili"
    larghezza, altezza = dim
    if abs(larghezza / altezza - RAPPORTO_4_5) > TOLLERANZA:
        return url, f"non è 4:5 ({larghezza}x{altezza})"
    if larghezza < 320:
        return url, f"troppo piccola ({larghezza}x{altezza})"
    return url, None
