"""Montaggio dei Reel (9:16, 1080×1920, 30 fps) a partire da un elenco di scene.

Ogni scena: una foto con un leggero zoom e scritte grandi in sovrimpressione.
Le scritte stanno lontane dalle zone coperte dall'interfaccia di Instagram (in alto, in basso e a destra).
Video: H.264 4:2:0, AAC 48 kHz 128 kbps, moov all'inizio (specifiche ufficiali IG User Media → Reels).
"""
import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import reel_musica

L, A = 1080, 1920
FPS = 30
DISSOLVENZA = 0.4  # secondi di dissolvenza incrociata tra le scene
MARGINE_SX, MARGINE_DX = 90, 170   # a destra ci sono i pulsanti di Instagram
ZONA_TESTO = (300, 1380)           # sopra: nome e audio; sotto: descrizione e pulsanti
BIANCO = (255, 248, 236)           # #FFF8EC, colore del brand
TRICOLORE = [(0, 146, 70), (241, 242, 241), (206, 43, 55)]

# Font gratuiti (SIL Open Font License) scaricati da Google Fonts al momento dell'esecuzione.
FONT = {
    "titolo": ("BodoniModa-Italic.ttf", "https://github.com/google/fonts/raw/main/ofl/bodonimoda/BodoniModa-Italic%5Bopsz,wght%5D.ttf"),
    "testo": ("Montserrat.ttf", "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"),
}
CARTELLA_FONT = Path(os.environ.get("REEL_FONT_DIR", Path(tempfile.gettempdir()) / "reel-font"))


def _font(tipo, dimensione, peso=None):
    nome, url = FONT[tipo]
    percorso = CARTELLA_FONT / nome
    if not percorso.exists():
        CARTELLA_FONT.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        percorso.write_bytes(r.content)
    f = ImageFont.truetype(str(percorso), dimensione)
    try:
        assi = f.get_variation_axes()
        valori = []
        for asse in assi:
            nome_asse = asse.get("name", b"")
            nome_asse = nome_asse.decode() if isinstance(nome_asse, bytes) else str(nome_asse)
            if "eight" in nome_asse and peso:
                valori.append(peso)
            elif "ptical" in nome_asse:
                valori.append(min(asse["maximum"], max(asse["minimum"], dimensione / 3)))
            else:
                valori.append(asse.get("default", asse["minimum"]))
        f.set_variation_by_axes(valori)
    except (OSError, AttributeError, KeyError):
        pass
    return f


# ───────────────────────────── foto ─────────────────────────────

def url_9_16(url, larghezza=1215, altezza=2160):
    """Per Unsplash e Pexels chiede direttamente il ritaglio 9:16 (un po' più grande, per lo zoom)."""
    parti = urlsplit(url)
    if parti.hostname not in ("images.unsplash.com", "images.pexels.com"):
        return url
    query = [(k, v) for k, v in parse_qsl(parti.query, keep_blank_values=True) if k not in ("w", "h", "fit", "fm", "crop")]
    query += [("w", str(larghezza)), ("h", str(altezza)), ("fit", "crop"), ("fm", "jpg")]
    return urlunsplit((parti.scheme, parti.netloc, parti.path, urlencode(query), parti.fragment))


def scarica_foto(url):
    """Foto 9:16 a 1215×2160. Se il sito non ritaglia da solo, sfondo sfocato + foto intera al centro."""
    r = requests.get(url_9_16(url), headers={"Accept": "image/jpeg,image/*;q=0.8"}, timeout=60)
    r.raise_for_status()
    img = Image.open(BytesIO(r.content)).convert("RGB")
    lt, at = 1215, 2160
    if abs(img.width / img.height - lt / at) < 0.02:
        return img.resize((lt, at), Image.LANCZOS)
    sfondo = img.copy()
    scala = max(lt / sfondo.width, at / sfondo.height)
    sfondo = sfondo.resize((int(sfondo.width * scala) + 1, int(sfondo.height * scala) + 1), Image.LANCZOS)
    sfondo = sfondo.crop(((sfondo.width - lt) // 2, (sfondo.height - at) // 2,
                          (sfondo.width - lt) // 2 + lt, (sfondo.height - at) // 2 + at))
    sfondo = sfondo.filter(ImageFilter.GaussianBlur(40))
    scala = min(lt / img.width, at / img.height)
    primo = img.resize((int(img.width * scala), int(img.height * scala)), Image.LANCZOS)
    sfondo.paste(primo, ((lt - primo.width) // 2, (at - primo.height) // 2))
    return sfondo


# ───────────────────────────── scritte ─────────────────────────────

def _a_capo(testo, font, larghezza_max):
    righe = []
    for paragrafo in testo.split("\n"):
        parole, riga = [p for p in paragrafo.split(" ") if p], ""  # lo spazio unificabile (\u00a0) non va a capo
        for parola in parole:
            prova = f"{riga} {parola}".strip()
            if font.getlength(prova) <= larghezza_max or not riga:
                riga = prova
            else:
                righe.append(riga)
                riga = parola
        righe.append(riga)
    return righe


def _scrivi(strato, xy, righe, font, interlinea, colore=BIANCO, ombra=True):
    x, y = xy
    ombra_img = Image.new("RGBA", strato.size, (0, 0, 0, 0))
    d_ombra = ImageDraw.Draw(ombra_img)
    d = ImageDraw.Draw(strato)
    for riga in righe:
        if ombra:
            d_ombra.text((x + 3, y + 4), riga, font=font, fill=(0, 0, 0, 170))
        y += interlinea
    if ombra:
        strato.alpha_composite(ombra_img.filter(ImageFilter.GaussianBlur(6)))
    y = xy[1]
    for riga in righe:
        d.text((x, y), riga, font=font, fill=colore + (255,))
        y += interlinea
    # fondo reale dell'ultima riga (le lettere del Bodoni scendono più dell'interlinea)
    return xy[1] + interlinea * (len(righe) - 1) + font.getbbox(righe[-1] or " ")[3]


def _titolo_adattato(testo, dimensione, peso, larghezza):
    """Font del titolo rimpicciolito finché la parola più lunga sta nella larghezza (es. POLIGNANO)."""
    while True:
        f = _font("titolo", dimensione, peso)
        if dimensione <= 60 or all(f.getlength(p) <= larghezza for p in testo.split()):
            return f, dimensione
        dimensione = int(dimensione * 0.92)


def _tricolore(strato, x, y, larghezza=150, altezza=8):
    d = ImageDraw.Draw(strato)
    terzo = larghezza // 3
    for i, colore in enumerate(TRICOLORE):
        d.rectangle([x + i * terzo, y, x + (i + 1) * terzo - 1, y + altezza], fill=colore + (255,))


def _sfumatura(alto=0.62, forza=170):
    """Scurisce la parte alta (dove stanno le scritte) per leggerle su qualunque foto."""
    sf = np.zeros((A, L, 4), dtype=np.uint8)
    fine = int(A * alto)
    alfa = np.zeros(A)
    alfa[:fine] = forza * (1 - np.linspace(0, 1, fine)) ** 1.3
    sf[..., 3] = alfa[:, None].astype(np.uint8)
    return Image.fromarray(sf, "RGBA")


def etichetta_luogo(strato, testo):
    """Piccola etichetta discreta con il luogo (scena "foto", richiesta 15): niente fondo scuro, solo un'ombra leggera."""
    f = _font("testo", 34, 600)
    _scrivi(strato, (MARGINE_SX, ZONA_TESTO[0]), [testo], f, 44)


def strato_testo(scena):
    """Immagine RGBA 1080×1920 con le scritte della scena."""
    tipo = scena.get("tipo", "consiglio")
    s = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    if tipo == "foto":  # prime scene: solo la foto, al massimo la piccola etichetta del luogo
        if scena.get("etichetta"):
            etichetta_luogo(s, scena["etichetta"])
        return s
    s.alpha_composite(_sfumatura(0.66 if tipo != "chiusura" else 0.8, 165))
    larghezza = L - MARGINE_SX - MARGINE_DX
    y = ZONA_TESTO[0]
    if tipo == "titolo":
        if scena.get("etichetta"):
            f = _font("testo", 38, 700)
            _scrivi(s, (MARGINE_SX, y), [scena["etichetta"].upper()], f, 50)
            y += 64
        _tricolore(s, MARGINE_SX, y)
        y += 40
        f, dim = _titolo_adattato(scena.get("testo", ""), 170, 600, larghezza)
        y = _scrivi(s, (MARGINE_SX - 6, y), _a_capo(scena.get("testo", ""), f, larghezza), f, int(dim * 1.12))
        y += 30
        if scena.get("sottotesto"):
            f = _font("testo", 58, 600)
            _scrivi(s, (MARGINE_SX, y), _a_capo(scena["sottotesto"], f, larghezza), f, 74)
    elif tipo == "chiusura":
        y += 60
        f = _font("testo", 52, 600)
        y = _scrivi(s, (MARGINE_SX, y), _a_capo(scena.get("etichetta", "Commenta"), f, larghezza), f, 68) + 24
        f, dim = _titolo_adattato(scena.get("testo", ""), 150, 700, larghezza)
        y = _scrivi(s, (MARGINE_SX - 6, y), _a_capo(scena.get("testo", ""), f, larghezza), f, int(dim * 1.13)) + 16
        _tricolore(s, MARGINE_SX, y, 210, 10)
        y += 44
        if scena.get("sottotesto"):
            f = _font("testo", 56, 600)
            y = _scrivi(s, (MARGINE_SX, y), _a_capo(scena["sottotesto"], f, larghezza), f, 72) + 44
        f = _font("titolo", 60, 500)
        if os.environ.get("PROFILO"):
            _scrivi(s, (MARGINE_SX, y), ["@" + os.environ["PROFILO"].lstrip("@")], f, 70)
    else:  # consiglio
        if scena.get("etichetta"):
            f = _font("testo", 40, 700)
            _scrivi(s, (MARGINE_SX, y), [scena["etichetta"].upper()], f, 50)
            y += 62
            _tricolore(s, MARGINE_SX, y)
            y += 44
        f, dim = _titolo_adattato(scena.get("testo", ""), 104, 600, larghezza)
        y = _scrivi(s, (MARGINE_SX - 4, y), _a_capo(scena.get("testo", ""), f, larghezza), f, int(dim * 1.14)) + 30
        if scena.get("sottotesto"):
            f = _font("testo", 52, 600)
            _scrivi(s, (MARGINE_SX, y), _a_capo(scena["sottotesto"], f, larghezza), f, 68)
    return s


# ───────────────────────────── video ─────────────────────────────

def _fotogramma(foto, avanzamento, verso):
    """Leggero zoom (8%) e piccolo spostamento: avanzamento 0→1 dentro la scena."""
    p = 0.5 - 0.5 * np.cos(np.pi * avanzamento)  # parte e arriva dolce
    zoom = 1.0 + 0.08 * (p if verso % 2 == 0 else 1 - p)
    w, h = foto.width / zoom, foto.height / zoom
    dx = (foto.width - w) * (0.5 + (0.15 * (p - 0.5) if verso % 3 == 1 else 0))
    dy = (foto.height - h) * 0.5
    return foto.resize((L, A), Image.BILINEAR, box=(dx, dy, dx + w, dy + h))


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def monta(scene, uscita, seme_musica=1, audio_wav=None):
    """Monta il Reel in 'uscita' (.mp4). Restituisce la durata in secondi.
    scene: elenco di dict con 'foto' (URL), 'secondi', 'tipo', 'etichetta', 'testo', 'sottotesto'."""
    foto = [scarica_foto(sc["foto"]) for sc in scene]
    testi = [strato_testo(sc) for sc in scene]
    durate = [float(sc.get("secondi", 3.0)) for sc in scene]
    inizi, t = [], 0.0
    for i, d in enumerate(durate):
        inizi.append(t)
        t += d - (DISSOLVENZA if i < len(durate) - 1 else 0)
    totale = t
    n_fotogrammi = int(round(totale * FPS))

    with tempfile.TemporaryDirectory() as tmp:
        wav = audio_wav or reel_musica.salva_wav(Path(tmp) / "musica.wav", totale + 0.1, seme_musica)
        comando = [
            ffmpeg_exe(), "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{L}x{A}", "-r", str(FPS), "-i", "-",
            "-i", str(wav),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-g", str(FPS * 2), "-bf", "2", "-flags", "+cgop", "-maxrate", "12M", "-bufsize", "24M",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart", str(uscita),
        ]
        proc = subprocess.Popen(comando, stdin=subprocess.PIPE)
        try:
            for n in range(n_fotogrammi):
                tempo = n / FPS
                # scene visibili in questo istante (al massimo due, durante la dissolvenza)
                attive = [i for i in range(len(scene)) if inizi[i] <= tempo < inizi[i] + durate[i]]
                quadro = None
                for i in attive:
                    locale = tempo - inizi[i]
                    img = _fotogramma(foto[i], locale / durate[i], i).convert("RGBA")
                    # le scritte compaiono in fretta (il gancio deve vedersi subito)
                    comparsa = 0.15 if i == 0 else 0.35
                    alfa_testo = min(1.0, max(0.0, (locale - (0 if i == 0 else DISSOLVENZA * 0.5)) / comparsa))
                    if alfa_testo > 0:
                        strato = testi[i]
                        if alfa_testo < 1:
                            strato = strato.copy()
                            strato.putalpha(strato.getchannel("A").point(lambda v, a=alfa_testo: int(v * a)))
                        img.alpha_composite(strato)
                    if quadro is None:
                        quadro = img
                    else:
                        peso = min(1.0, locale / DISSOLVENZA)
                        quadro = Image.blend(quadro, img, peso)
                proc.stdin.write(quadro.convert("RGB").tobytes())
        finally:
            proc.stdin.close()
            codice = proc.wait()
        if codice != 0:
            raise RuntimeError(f"ffmpeg ha restituito il codice {codice}")
    return totale


def copertina(scena, uscita):
    """Copertina 9:16 in JPEG: la foto della scena con le sue scritte (senza zoom)."""
    img = scarica_foto(scena["foto"]).resize((L, A), Image.LANCZOS).convert("RGBA")
    img.alpha_composite(strato_testo(scena))
    img.convert("RGB").save(uscita, "JPEG", quality=90)
    return uscita


def collage_carosello(url_foto, uscita, colonne=4):
    """Anteprima del carosello: le foto (4:5) in griglia, nell'ordine di pubblicazione, per Telegram."""
    lato_l, lato_a = 270, 338
    righe = (len(url_foto) + colonne - 1) // colonne
    foglio = Image.new("RGB", (colonne * lato_l + (colonne + 1) * 8, righe * lato_a + (righe + 1) * 8), (20, 20, 20))
    for i, url in enumerate(url_foto):
        try:
            r = requests.get(url, headers={"Accept": "image/jpeg,image/*;q=0.8"}, timeout=60)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB")
            scala = max(lato_l / img.width, lato_a / img.height)
            img = img.resize((int(img.width * scala) + 1, int(img.height * scala) + 1), Image.LANCZOS)
            img = img.crop(((img.width - lato_l) // 2, (img.height - lato_a) // 2,
                            (img.width - lato_l) // 2 + lato_l, (img.height - lato_a) // 2 + lato_a))
        except Exception:  # noqa: BLE001 — una foto che non si apre resta un riquadro rosso
            img = Image.new("RGB", (lato_l, lato_a), (206, 43, 55))
        x, y = 8 + (i % colonne) * (lato_l + 8), 8 + (i // colonne) * (lato_a + 8)
        foglio.paste(img, (x, y))
        ImageDraw.Draw(foglio).text((x + 10, y + 8), str(i + 1), fill=(255, 255, 255))
    foglio.save(uscita, "JPEG", quality=85)
    return uscita
