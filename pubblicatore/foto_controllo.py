"""Controllo tecnico delle foto ed esportazione per il carosello (richiesta 18).

Solo misure oggettive sul file vero: dimensioni dell'originale, nitidezza, alte luci, ombre, colore, qualità JPEG.
Il giudizio estetico resta al direttore artistico: qui si bocciano solo i difetti che si possono misurare.
Le soglie stanno in SOGLIE, tarate sulle foto approvate; ogni valore misurato finisce in controllo-foto.json.
"""
import io
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import numpy as np
import requests
from PIL import Image, ImageCms, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = 300_000_000

SOGLIE = {
    "lato_corto": 3000,          # px dell'originale, carosello
    "lato_corto_reel": 4000,     # px dell'originale, Reel…
    "larghezza_verticale_reel": 2400,  # …salvo foto già verticale larga almeno così
    "nitidezza": 90.0,           # varianza del Laplaciano (10% più nitido dell'immagine, misurata a 1620 px)
    "bruciate": 2.0,             # % di pixel a 254-255
    "ombre": 30.0,               # % di pixel quasi neri
    "saturazione_min": 0.045,    # sotto: bianco e nero o foto spenta
    "saturazione_fluo": 18.0,    # % di pixel a saturazione estrema: colori finti
    "jpeg": 74,                  # qualità JPEG stimata
}
LATO_MISURA = 1620
CAROSELLO = (1080, 1350)
QUALITA_CAROSELLO = 93
UA = "Mozilla/5.0 (compatible; pubblicatore/1.0)"
# Tabella di quantizzazione base della luminanza (standard JPEG): serve a stimare la qualità del file.
BASE_LUMA = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
                      14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                      49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=float)


def url_originale(url):
    """Link alla foto intera alla massima risoluzione. Pexels: niente parametri (cs=tinysrgb e auto=compress riducono
    colori e qualità). Unsplash: niente ritaglio né larghezza, q=90 e JPEG."""
    parti = urlsplit(url.strip())
    if parti.hostname == "images.pexels.com":
        return urlunsplit((parti.scheme, parti.netloc, parti.path, "", ""))
    if parti.hostname == "images.unsplash.com":
        return urlunsplit((parti.scheme, parti.netloc, parti.path, urlencode([("q", "90"), ("fm", "jpg")]), ""))
    return url.strip()


def url_pulito(url):
    """Stesso link senza i parametri che rovinano la qualità (Pexels) e con q=90 (Unsplash), mantenendo il ritaglio."""
    parti = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parti.query, keep_blank_values=True)]
    if parti.hostname == "images.pexels.com":
        query = [(k, v) for k, v in query if k not in ("cs", "auto", "dpr")]
    elif parti.hostname == "images.unsplash.com":
        query = [(k, v) for k, v in query if k not in ("q", "fm", "auto")] + [("q", "90"), ("fm", "jpg")]
    else:
        return url.strip()
    return urlunsplit((parti.scheme, parti.netloc, parti.path, urlencode(query), parti.fragment))


def scarica(url, timeout=120):
    r = requests.get(url, headers={"Accept": "image/jpeg,image/*;q=0.8", "User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.content


def apri(dati):
    """Apre l'immagine girata nel verso giusto e in sRGB (i profili di altri spazi colore si convertono, non si buttano)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(dati)))
    profilo = img.info.get("icc_profile")
    if profilo:
        try:
            img = ImageCms.profileToProfile(img, ImageCms.ImageCmsProfile(io.BytesIO(profilo)),
                                            ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass
    return img.convert("RGB")


def qualita_jpeg(img_originale):
    """Stima della qualità JPEG (scala 1-100) dalla tabella di quantizzazione; None se il file non è un JPEG."""
    tabelle = getattr(img_originale, "quantization", None)
    if not tabelle or 0 not in tabelle:
        return None
    t = np.array(tabelle[0], dtype=float)
    scala = float(np.mean(t / BASE_LUMA) * 100)
    q = 200 - scala if scala <= 100 else 5000 / scala
    return int(round(min(100, max(1, q / 2 if scala <= 100 else q))))


def _nitidezza(grigio):
    """Varianza del Laplaciano per zone: vale la zona più nitida (10° percentile dall'alto), così un cielo liscio
    non abbassa il voto di una foto con il soggetto a fuoco."""
    lap = (-4 * grigio[1:-1, 1:-1] + grigio[:-2, 1:-1] + grigio[2:, 1:-1] + grigio[1:-1, :-2] + grigio[1:-1, 2:])
    zone = []
    passo = max(32, min(lap.shape) // 12)
    for y in range(0, lap.shape[0] - passo + 1, passo):
        for x in range(0, lap.shape[1] - passo + 1, passo):
            zone.append(float(lap[y:y + passo, x:x + passo].var()))
    return float(np.percentile(zone, 90)) if zone else 0.0


def misura(dati):
    """Valori tecnici di una foto (bytes dell'originale)."""
    grezza = Image.open(io.BytesIO(dati))
    qualita = qualita_jpeg(grezza)
    img = apri(dati)
    larghezza, altezza = img.size
    scala = LATO_MISURA / max(larghezza, altezza)
    piccola = img.resize((round(larghezza * scala), round(altezza * scala)), Image.LANCZOS) if scala < 1 else img
    rgb = np.asarray(piccola, dtype=np.float32)
    grigio = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    massimo, minimo = rgb.max(axis=2), rgb.min(axis=2)
    saturazione = (massimo - minimo) / np.maximum(massimo, 1)
    return {
        "larghezza": larghezza, "altezza": altezza,
        "nitidezza": round(_nitidezza(grigio), 1),
        "bruciate": round(float((grigio >= 254).mean() * 100), 2),
        "ombre": round(float((grigio <= 6).mean() * 100), 2),
        "saturazione": round(float(saturazione[massimo > 25].mean()) if (massimo > 25).any() else 0.0, 3),
        "saturazione_fluo": round(float(((saturazione > 0.92) & (massimo > 90)).mean() * 100), 2),
        "jpeg": qualita,
    }


def difetti(v, reel=False):
    """Motivi di bocciatura (lista vuota = foto sana). reel=True applica anche la soglia del Reel."""
    s, motivi = SOGLIE, []
    corto = min(v["larghezza"], v["altezza"])
    if corto < s["lato_corto"]:
        motivi.append(f"originale piccolo ({v['larghezza']}×{v['altezza']})")
    elif reel and corto < s["lato_corto_reel"] and not (v["altezza"] > v["larghezza"] and v["larghezza"] >= s["larghezza_verticale_reel"]):
        motivi.append(f"originale piccolo per il Reel ({v['larghezza']}×{v['altezza']})")
    if v["nitidezza"] < s["nitidezza"]:
        motivi.append(f"poco nitida ({v['nitidezza']})")
    if v["bruciate"] > s["bruciate"]:
        motivi.append(f"alte luci bruciate ({v['bruciate']}%)")
    if v["ombre"] > s["ombre"]:
        motivi.append(f"ombre chiuse ({v['ombre']}%)")
    if v["saturazione"] < s["saturazione_min"]:
        motivi.append(f"bianco e nero o spenta ({v['saturazione']})")
    if v["saturazione_fluo"] > s["saturazione_fluo"]:
        motivi.append(f"colori finti ({v['saturazione_fluo']}%)")
    if v["jpeg"] is not None and v["jpeg"] < s["jpeg"]:
        motivi.append(f"JPEG troppo compresso (qualità ~{v['jpeg']})")
    return motivi


def ritaglia(img, rapporto, fuoco=None):
    """Ritaglio al rapporto larghezza/altezza, centrato (o attorno a fuoco = [x, y] tra 0 e 1)."""
    fx, fy = (fuoco or (0.5, 0.5))[:2]
    larghezza, altezza = img.size
    if larghezza / altezza > rapporto:
        w, h = round(altezza * rapporto), altezza
    else:
        w, h = larghezza, round(larghezza / rapporto)
    x = min(larghezza - w, max(0, round(fx * larghezza - w / 2)))
    y = min(altezza - h, max(0, round(fy * altezza - h / 2)))
    return img.crop((x, y, x + w, y + h))


def esporta_carosello(dati, uscita, fuoco=None):
    """1080×1350, sRGB, Lanczos + leggera maschera di contrasto dopo il ridimensionamento, JPEG 93."""
    img = ritaglia(apri(dati), CAROSELLO[0] / CAROSELLO[1], fuoco)
    if img.width < CAROSELLO[0]:
        raise ValueError("foto troppo piccola per il carosello")
    img = img.resize(CAROSELLO, Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=2))
    img.save(uscita, "JPEG", quality=QUALITA_CAROSELLO, subsampling=0, optimize=True,
             icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    return uscita
