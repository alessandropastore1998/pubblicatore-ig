"""Montaggio dei Reel v2 (richiesta 14): più ritmo e più movimento. Il v1 (reel_montaggio.py) resta com'è:
si sceglie con la variabile MONTAGGIO_REEL (v1 / v2), così si torna indietro in un minuto.

Novità rispetto al v1:
- movimento sempre: zoom e spostamento lento in direzioni diverse su ogni foto;
- scene più corte (1,5-3 s), tagli a tempo con la musica (120 bpm: un battito ogni 0,5 s), transizioni brevi;
- clip video vere quando la scena ha "video" (link diretto .mp4 con licenza); se la clip non si apre, si usa la foto;
- scritte animate (salgono e compaiono una dopo l'altra), parola chiave evidenziata, numerazione "1/3";
- segni di avanzamento in alto (uno per scena);
- finale in loop: l'ultima scena torna sulla foto e sull'inquadratura della prima, così il Reel si rivede.
Stesse specifiche video del v1 (H.264 4:2:0, AAC 48 kHz, moov all'inizio).
"""
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import reel_musica
from .reel_montaggio import (A, BIANCO, FPS, L, MARGINE_DX, MARGINE_SX, ZONA_TESTO, _a_capo, _font, _sfumatura,
                             _titolo_adattato, _tricolore, ffmpeg_exe, scarica_foto)

BPM = 120
TRANSIZIONE = 0.2      # secondi, a cavallo del taglio (il taglio cade sul battito)
ROSSO = (206, 43, 55)  # rosso del tricolore, per evidenziare la parola chiave
COMPARSA = 0.28        # durata dell'animazione di ogni scritta
RITARDO = 0.09         # ritardo tra una scritta e la successiva
SALITA = 46            # pixel di cui sale una scritta mentre compare
Y_AVANZAMENTO = 238    # segni di avanzamento (sotto la barra di Instagram)


def _morbido(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# ───────────────────────────── scritte come elementi animati ─────────────────────────────

class Elemento:
    """Un pezzo di scritta già disegnato (ritagliato) con la sua posizione e il momento in cui compare."""

    def __init__(self, img, xy, ritardo, tipo="sale"):
        self.img, self.xy, self.ritardo, self.tipo = img, xy, ritardo, tipo

    def disegna(self, quadro, tempo):
        p = _morbido((tempo - self.ritardo) / COMPARSA)
        if p <= 0:
            return
        img = self.img
        if self.tipo == "striscia":  # la fascia rossa della parola chiave si allarga da sinistra
            larghezza = max(1, int(img.width * p))
            img = img.crop((0, 0, larghezza, img.height))
            quadro.alpha_composite(img, (self.xy[0], self.xy[1]))
            return
        if p < 1:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v, a=p: int(v * a)))
        quadro.alpha_composite(img, (self.xy[0], int(self.xy[1] + SALITA * (1 - p))))


def _blocco(righe, font, interlinea, colore=BIANCO, ombra=True):
    """Disegna le righe su un'immagine trasparente ritagliata (con ombra morbida). Restituisce (img, altezza utile)."""
    larghezza = int(max(font.getlength(r) for r in righe)) + 40
    fondo = interlinea * (len(righe) - 1) + font.getbbox(righe[-1] or " ")[3]
    img = Image.new("RGBA", (larghezza, fondo + 40), (0, 0, 0, 0))
    if ombra:
        ombra_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ombra_img)
        for i, r in enumerate(righe):
            d.text((13, 14 + i * interlinea), r, font=font, fill=(0, 0, 0, 175))
        img.alpha_composite(ombra_img.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    for i, r in enumerate(righe):
        d.text((10, 10 + i * interlinea), r, font=font, fill=colore + (255,))
    return img, fondo


def _scritta(elementi, testo, font, interlinea, x, y, ritardo, larghezza):
    righe = _a_capo(testo, font, larghezza)
    img, fondo = _blocco(righe, font, interlinea)
    elementi.append(Elemento(img, (x - 10, y - 10), ritardo))
    return y + fondo


def _barra_tricolore(elementi, x, y, ritardo, larghezza=150, altezza=8):
    img = Image.new("RGBA", (larghezza, altezza + 1), (0, 0, 0, 0))
    _tricolore(img, 0, 0, larghezza, altezza)
    elementi.append(Elemento(img, (x, y), ritardo, "striscia"))


def elementi_scena(scena):
    """Scritte della scena come elementi animati, nell'ordine in cui compaiono."""
    tipo = scena.get("tipo", "consiglio")
    el = []
    larghezza = L - MARGINE_SX - MARGINE_DX
    x, y, r = MARGINE_SX, ZONA_TESTO[0], 0.0
    # La prima scena è il gancio: le scritte ci sono già dal primo fotogramma.
    passo = 0.0 if scena.get("_prima") else RITARDO
    if tipo == "titolo":
        if scena.get("etichetta"):
            y = _scritta(el, scena["etichetta"].upper(), _font("testo", 38, 700), 50, x, y, r, larghezza) + 22
        _barra_tricolore(el, x, y, r)
        y += 40
        f, dim = _titolo_adattato(scena.get("testo", ""), 170, 600, larghezza)
        r += passo
        y = _scritta(el, scena.get("testo", ""), f, int(dim * 1.12), x - 6, y, r, larghezza) + 30
        if scena.get("sottotesto"):
            r += passo
            _scritta(el, scena["sottotesto"], _font("testo", 58, 600), 74, x, y, r, larghezza)
    elif tipo == "luogo":  # scena di solo colpo d'occhio: una piccola etichetta e basta
        if scena.get("etichetta"):
            _scritta(el, scena["etichetta"].upper(), _font("testo", 40, 700), 52, x, y, 0.1, larghezza)
    elif tipo == "locali":  # "Dove mangiare a …": i nomi compaiono uno alla volta
        y = _scritta(el, scena.get("etichetta", "Dove mangiare").upper(), _font("testo", 40, 700), 52, x, y, r, larghezza) + 18
        _barra_tricolore(el, x, y, r)
        y += 44
        f_nome = _font("titolo", 82, 600)
        f_perche = _font("testo", 44, 600)
        for i, loc in enumerate(scena.get("locali", [])[:3]):
            r = 0.15 + i * 0.35
            numero = _font("testo", 44, 700)
            el.append(Elemento(_blocco([f"{i + 1}"], numero, 50, ROSSO, ombra=False)[0], (x - 10, y + 6), r))
            y = _scritta(el, loc.get("nome", ""), f_nome, 92, x + 46, y, r, larghezza - 46) + 8
            if loc.get("perche"):
                y = _scritta(el, loc["perche"], f_perche, 56, x + 46, y, r + 0.08, larghezza - 46) + 8
            y += 26
    elif tipo == "chiusura":
        y += 40
        y = _scritta(el, scena.get("etichetta", "Commenta"), _font("testo", 52, 600), 68, x, y, r, larghezza) + 24
        f, dim = _titolo_adattato(scena.get("testo", ""), 150, 700, larghezza)
        righe = _a_capo(scena.get("testo", ""), f, larghezza - 40)
        img, fondo = _blocco(righe, f, int(dim * 1.13), ombra=False)
        striscia = Image.new("RGBA", (img.width + 24, fondo + 44), ROSSO + (235,))
        el.append(Elemento(striscia, (x - 16, y - 8), r + 0.1, "striscia"))
        el.append(Elemento(img, (x - 4, y - 6), r + 0.22))
        y += fondo + 60
        if scena.get("sottotesto"):
            y = _scritta(el, scena["sottotesto"], _font("testo", 56, 600), 72, x, y, r + 0.32, larghezza) + 40
        if os.environ.get("PROFILO"):
            _scritta(el, "@" + os.environ["PROFILO"].lstrip("@"), _font("titolo", 60, 500), 70, x, y, r + 0.42, larghezza)
    else:  # consiglio
        if scena.get("etichetta"):
            y = _scritta(el, scena["etichetta"].upper(), _font("testo", 40, 700), 52, x, y, r, larghezza) + 18
            _barra_tricolore(el, x, y, r)
            y += 44
        f, dim = _titolo_adattato(scena.get("testo", ""), 104, 600, larghezza)
        r += passo
        y = _scritta(el, scena.get("testo", ""), f, int(dim * 1.14), x - 4, y, r, larghezza) + 30
        if scena.get("sottotesto"):
            _scritta(el, scena["sottotesto"], _font("testo", 52, 600), 68, x, y, r + passo, larghezza)
    return el


# ───────────────────────────── immagini in movimento ─────────────────────────────

# (zoom iniziale, zoom finale, spostamento x iniziale→finale, spostamento y iniziale→finale), spostamenti in -1..1
MOVIMENTI = [
    (1.00, 1.13, (0.0, 0.0), (0.0, 0.0)),     # avvicinamento al centro
    (1.12, 1.12, (-0.8, 0.8), (0.0, 0.0)),    # da sinistra a destra
    (1.14, 1.02, (0.0, 0.0), (0.4, -0.2)),    # allontanamento salendo
    (1.12, 1.12, (0.8, -0.8), (0.0, 0.0)),    # da destra a sinistra
    (1.04, 1.15, (0.0, 0.3), (-0.4, 0.2)),    # avvicinamento in diagonale
]


def _inquadra(foto, zoom, sx, sy):
    w, h = foto.width / zoom, foto.height / zoom
    dx = (foto.width - w) * (0.5 + 0.5 * sx)
    dy = (foto.height - h) * (0.5 + 0.5 * sy)
    return foto.resize((L, A), Image.BILINEAR, box=(dx, dy, dx + w, dy + h))


class Foto:
    def __init__(self, img, movimento):
        self.img, self.m = img, movimento

    def quadro(self, avanzamento, _n):
        z0, z1 = self.m[0], self.m[1]
        p = 0.15 * avanzamento + 0.85 * (0.5 - 0.5 * np.cos(np.pi * avanzamento))  # quasi costante, parte e arriva dolce
        return _inquadra(self.img, z0 + (z1 - z0) * p, self.m[2][0] + (self.m[2][1] - self.m[2][0]) * p,
                         self.m[3][0] + (self.m[3][1] - self.m[3][0]) * p)


class Clip:
    """Clip video letta a fotogrammi da ffmpeg (ritaglio 9:16 al centro, 30 fps). Un leggero zoom si aggiunge al movimento della clip."""

    def __init__(self, url, secondi, inizio=0.0):
        filtro = f"scale={L}:{A}:force_original_aspect_ratio=increase,crop={L}:{A},fps={FPS}"
        comando = [ffmpeg_exe(), "-loglevel", "error", "-ss", str(inizio), "-t", f"{secondi + 0.5:.2f}",
                   "-i", url, "-an", "-vf", filtro, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        dati = subprocess.run(comando, capture_output=True, timeout=180, check=True).stdout
        n = len(dati) // (L * A * 3)
        if n < int(secondi * FPS * 0.8):
            raise RuntimeError("clip troppo corta o non leggibile")
        self.dati, self.n = dati, n

    def quadro(self, avanzamento, n_locale):
        k = min(self.n - 1, max(0, n_locale))
        img = Image.frombuffer("RGB", (L, A), self.dati[k * L * A * 3:(k + 1) * L * A * 3], "raw", "RGB", 0, 1)
        z = 1.0 + 0.04 * avanzamento
        w, h = L / z, A / z
        return img.resize((L, A), Image.BILINEAR, box=((L - w) / 2, (A - h) / 2, (L + w) / 2, (A + h) / 2))


def sorgente(scena, i, ultima, foto_prima):
    if scena.get("video"):
        try:
            return Clip(scena["video"], float(scena.get("secondi", 2.0)), float(scena.get("video_inizio", 0)))
        except Exception as e:  # noqa: BLE001 — una clip che non si apre non ferma il Reel: si usa la foto
            print(f"Clip della scena {i + 1} non usata ({type(e).__name__}): uso la foto.")
    if ultima and foto_prima is not None:
        # finale in loop: stessa foto della prima scena e il movimento finisce dove la prima comincia
        m = MOVIMENTI[0]
        return Foto(foto_prima, (m[1] + 0.02, m[0], (0.0, 0.0), (0.0, 0.0)))
    return Foto(scarica_foto(scena["foto"]), MOVIMENTI[i % len(MOVIMENTI)])


# ───────────────────────────── montaggio ─────────────────────────────

def _avanzamento(quadro, n_scene, i, frazione):
    # disegnati su uno strato trasparente e poi sovrapposti (ImageDraw da solo non fonde la trasparenza)
    alto = 14
    strato = Image.new("RGBA", (L, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(strato)
    spazio, x0 = 10, MARGINE_SX
    larghezza = (L - MARGINE_SX - MARGINE_DX - spazio * (n_scene - 1)) / n_scene
    for k in range(n_scene):
        x = x0 + k * (larghezza + spazio)
        d.rounded_rectangle([x, 4, x + larghezza, 10], 3, fill=(255, 255, 255, 70))
        pieno = 1.0 if k < i else (frazione if k == i else 0.0)
        if pieno > 0:
            d.rounded_rectangle([x, 4, x + max(6, larghezza * pieno), 10], 3, fill=(255, 248, 236, 240))
    quadro.alpha_composite(strato, (0, Y_AVANZAMENTO - 4))


def a_tempo(secondi):
    """Arrotonda la durata di una scena al mezzo battito (0,25 s a 120 bpm) più vicino, tra 1,5 e 4 s."""
    return min(4.0, max(1.5, round(float(secondi) * 4) / 4))


def monta(scene, uscita, seme_musica=1, audio_wav=None):
    """Monta il Reel v2 in 'uscita' (.mp4). Restituisce la durata in secondi. Stesso formato delle scene del v1,
    più: tipo "luogo" e "locali" (con "locali": [{"nome", "perche"}]), campo facoltativo "video" (link diretto .mp4)."""
    scene = [dict(s) for s in scene]
    scene[0]["_prima"] = True
    durate = [a_tempo(s.get("secondi", 2.0)) for s in scene]
    inizi = [sum(durate[:i]) for i in range(len(scene))]
    totale = sum(durate)
    foto_prima = scarica_foto(scene[0]["foto"]) if scene[0].get("foto") else None
    sorgenti = []
    for i, s in enumerate(scene):
        if i == 0 and not s.get("video") and foto_prima is not None:
            sorgenti.append(Foto(foto_prima, MOVIMENTI[0]))
        else:
            sorgenti.append(sorgente(s, i, i == len(scene) - 1 and s.get("tipo") == "chiusura", foto_prima))
    sfumature = [_sfumatura(0.8 if s.get("tipo") in ("chiusura", "locali") else 0.66, 165) for s in scene]
    elementi = [elementi_scena(s) for s in scene]
    meta = TRANSIZIONE / 2
    n_fotogrammi = int(round(totale * FPS))

    with tempfile.TemporaryDirectory() as tmp:
        wav = audio_wav or reel_musica.salva_wav(Path(tmp) / "musica.wav", totale + 0.1, seme_musica, bpm=BPM, marcato=True)
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
                quadro = None
                for i in range(len(scene)):
                    da = inizi[i] - (meta if i > 0 else 0)
                    a = inizi[i] + durate[i] + (meta if i < len(scene) - 1 else 0)
                    if not da <= tempo < a:
                        continue
                    locale = tempo - inizi[i]
                    img = sorgenti[i].quadro(min(1.0, max(0.0, (tempo - da) / (a - da))), int(round((tempo - da) * FPS)))
                    img = img.convert("RGBA")
                    img.alpha_composite(sfumature[i])
                    for e in elementi[i]:
                        e.disegna(img, max(0.0, locale) if i > 0 else tempo + COMPARSA)
                    if quadro is None:
                        quadro = img
                    else:
                        quadro = Image.blend(quadro, img, min(1.0, (tempo - da) / TRANSIZIONE))
                attuale = max(i for i in range(len(scene)) if inizi[i] <= tempo)
                _avanzamento(quadro, len(scene), attuale, (tempo - inizi[attuale]) / durate[attuale])
                proc.stdin.write(quadro.convert("RGB").tobytes())
        finally:
            proc.stdin.close()
            codice = proc.wait()
        if codice != 0:
            raise RuntimeError(f"ffmpeg ha restituito il codice {codice}")
    return totale


def copertina(scena, uscita):
    """Copertina 9:16: la foto della prima scena con le scritte già tutte visibili."""
    img = scarica_foto(scena["foto"]).resize((L, A), Image.LANCZOS).convert("RGBA")
    img.alpha_composite(_sfumatura(0.66, 165))
    for e in elementi_scena(dict(scena, _prima=True)):
        e.disegna(img, 10.0)
    img.convert("RGB").save(uscita, "JPEG", quality=90)
    return uscita
