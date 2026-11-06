"""Montaggio dei Reel v2 (richieste 14, 16, 17 e 18): solo immagini, ritmo, massima qualità. Il v1 (reel_montaggio.py)
resta com'è: si sceglie con la variabile MONTAGGIO_REEL (v1 / v2), così si torna indietro in un minuto.

Regole:
- nessuna scritta sul video: gli eventuali testo/sottotesto/etichetta/locali delle scene si ignorano. Unica eccezione
  il campo "gancio" sulla scena 1 (test A/B della richiesta 17, massimo 7 parole, primi 2 secondi);
- il 9:16 si ritaglia dalla foto intera ad alta risoluzione (campo "originale"), mai dalla versione 4:5 già ritagliata;
- mai ingrandire oltre 1,0× i pixel originali: lo zoom si limita a quanto la risoluzione permette, altrimenti foto ferma;
- clip video vere solo se nel verticale restano almeno 1080 px di larghezza; altrimenti si usa la foto;
- tagli di 1-2,5 s a tempo con la musica (120 bpm), finale in loop sulla prima scena;
- uscita 1080×1920, 30 fps, H.264 High 4:2:0, CRF 18, AAC 48 kHz, moov all'inizio.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import foto_controllo, reel_musica
from .reel_montaggio import A, BIANCO, FPS, L, MARGINE_DX, MARGINE_SX, ZONA_TESTO, _a_capo, _titolo_adattato, ffmpeg_exe

BPM = 120
TRANSIZIONE = 0.2      # secondi, a cavallo del taglio (il taglio cade sul battito)
ZOOM_MASSIMO = 1.18    # movimento delle foto (richiesta 17: 15-20%), solo se i pixel lo permettono
GANCIO_SECONDI = 1.8
FORMATO = L / A
MIN_LARGHEZZA_CLIP = L
UA = "Mozilla/5.0 (X11; Linux x86_64)"  # alcuni archivi di video rifiutano il nome predefinito di ffmpeg


class FotoNonAdatta(Exception):
    """La foto non regge il 9:16 a piena risoluzione: la scena va tolta o sostituita."""


# ───────────────────────────── gancio (solo scena 1, test A/B) ─────────────────────────────

def strato_gancio(testo):
    """La frase-gancio grande nella zona sicura, con ombra morbida."""
    larghezza = L - MARGINE_SX - MARGINE_DX
    font, dimensione = _titolo_adattato(testo, 112, 600, larghezza)
    righe = _a_capo(testo, font, larghezza)
    interlinea = int(dimensione * 1.14)
    strato = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    ombra = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    disegno_ombra, disegno = ImageDraw.Draw(ombra), ImageDraw.Draw(strato)
    y = ZONA_TESTO[0] + 80
    for i, riga in enumerate(righe):
        disegno_ombra.text((MARGINE_SX + 3, y + i * interlinea + 4), riga, font=font, fill=(0, 0, 0, 200))
        disegno.text((MARGINE_SX, y + i * interlinea), riga, font=font, fill=BIANCO + (255,))
    ombra = ombra.filter(ImageFilter.GaussianBlur(7))
    ombra.alpha_composite(strato)
    return ombra


# ───────────────────────────── immagini in movimento ─────────────────────────────

# (zoom iniziale, zoom finale, spostamento x iniziale→finale, spostamento y iniziale→finale).
# Lo zoom è la frazione (0-1) di quello che la foto permette; gli spostamenti sono in -1..1.
MOVIMENTI = [
    (0.0, 1.0, (0.0, 0.0), (0.0, 0.0)),     # avvicinamento al centro
    (0.8, 0.8, (-0.9, 0.9), (0.0, 0.0)),    # da sinistra a destra
    (1.0, 0.1, (0.0, 0.0), (0.4, -0.2)),    # allontanamento salendo
    (0.8, 0.8, (0.9, -0.9), (0.0, 0.0)),    # da destra a sinistra
    (0.2, 1.0, (0.0, 0.3), (-0.4, 0.2)),    # avvicinamento in diagonale
]


def foto_verticale(scena):
    """(immagine 9:16, zoom massimo). L'immagine è larga L × zoom massimo: al massimo dello zoom un pixel della foto
    vale un pixel del video, mai di più."""
    dati = foto_controllo.scarica(foto_controllo.url_originale(scena.get("originale") or scena["foto"]))
    img = foto_controllo.ritaglia(foto_controllo.apri(dati), FORMATO, scena.get("fuoco"))
    if img.width < L:
        raise FotoNonAdatta(f"nel 9:16 restano {img.width} px di larghezza")
    zoom = min(ZOOM_MASSIMO, img.width / L)
    return img.resize((round(L * zoom), round(A * zoom)), Image.LANCZOS), zoom


class Foto:
    def __init__(self, img, zoom_massimo, movimento):
        self.img, self.z, self.m = img, zoom_massimo, movimento

    def _inquadra(self, zoom, sx, sy):
        w, h = self.img.width / zoom, self.img.height / zoom
        dx = (self.img.width - w) * (0.5 + 0.5 * sx)
        dy = (self.img.height - h) * (0.5 + 0.5 * sy)
        return self.img.resize((L, A), Image.BICUBIC, box=(dx, dy, dx + w, dy + h))

    def quadro(self, avanzamento, _n):
        if self.z < 1.015:  # niente margine: foto ferma, piena definizione
            return self.img if self.img.size == (L, A) else self.img.resize((L, A), Image.LANCZOS)
        p = 0.15 * avanzamento + 0.85 * (0.5 - 0.5 * np.cos(np.pi * avanzamento))  # quasi costante, parte e arriva dolce
        z0, z1 = self.m[0], self.m[1]
        return self._inquadra(1 + (self.z - 1) * (z0 + (z1 - z0) * p), self.m[2][0] + (self.m[2][1] - self.m[2][0]) * p,
                              self.m[3][0] + (self.m[3][1] - self.m[3][0]) * p)


def dimensioni_clip(url):
    """Larghezza e altezza della clip, lette da ffmpeg (scambiate se il video è girato in verticale)."""
    uscita = subprocess.run([ffmpeg_exe(), "-hide_banner", "-user_agent", UA, "-i", url], capture_output=True, text=True, timeout=60).stderr
    trovato = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", uscita)
    if not trovato:
        raise RuntimeError("dimensioni della clip illeggibili")
    w, h = int(trovato.group(1)), int(trovato.group(2))
    return (h, w) if re.search(r"rotation of -?90", uscita) else (w, h)


class Clip:
    """Clip video letta a fotogrammi da ffmpeg: ritaglio 9:16 al centro, solo riduzione (mai ingrandita), 30 fps."""

    def __init__(self, url, secondi, inizio=0.0):
        w, h = dimensioni_clip(url)
        ritaglio = min(w, round(h * FORMATO))
        if ritaglio < MIN_LARGHEZZA_CLIP:
            raise RuntimeError(f"clip troppo piccola per il verticale ({w}x{h})")
        filtro = f"crop={ritaglio}:{min(h, round(ritaglio / FORMATO))},scale={L}:{A}:flags=lanczos,fps={FPS}"
        comando = [ffmpeg_exe(), "-loglevel", "error", "-ss", str(inizio), "-t", f"{secondi + 0.5:.2f}",
                   "-user_agent", UA, "-i", url, "-an", "-vf", filtro, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        dati = subprocess.run(comando, capture_output=True, timeout=180, check=True).stdout
        n = len(dati) // (L * A * 3)
        if n < int(secondi * FPS * 0.8):
            raise RuntimeError("clip troppo corta o non leggibile")
        self.dati, self.n = dati, n

    def quadro(self, _avanzamento, n_locale):
        k = min(self.n - 1, max(0, n_locale))
        return Image.frombuffer("RGB", (L, A), self.dati[k * L * A * 3:(k + 1) * L * A * 3], "raw", "RGB", 0, 1)


def sorgente(scena, i, prima):
    """Clip se c'è e regge il verticale, altrimenti foto. 'prima' = (img, zoom) della scena 1, per chiudere il loop."""
    if scena.get("video"):
        try:
            return Clip(scena["video"], float(scena.get("secondi", 2.0)), float(scena.get("video_inizio", 0))), "clip"
        except Exception as e:  # noqa: BLE001 — una clip che non si apre non ferma il Reel: si usa la foto
            print(f"Clip della scena {i + 1} non usata ({type(e).__name__}): uso la foto.")
    if prima is not None and scena.get("_loop"):
        # finale in loop: stessa foto della prima scena e il movimento finisce dove la prima comincia
        m = MOVIMENTI[0]
        return Foto(prima[0], prima[1], (m[1], m[0], (0.0, 0.0), (0.0, 0.0))), "foto"
    img, zoom = foto_verticale(scena)
    return Foto(img, zoom, MOVIMENTI[i % len(MOVIMENTI)]), "foto"


# ───────────────────────────── montaggio ─────────────────────────────

def a_tempo(secondi):
    """Arrotonda la durata di una scena al mezzo battito (0,25 s a 120 bpm) più vicino, tra 1 e 2,5 s."""
    return min(2.5, max(1.0, round(float(secondi) * 4) / 4))


def _solo_immagini(scene):
    """Delle scene tiene solo i campi delle immagini: scritte e nomi non finiscono mai sul video (richiesta 16)."""
    pulite = []
    for i, s in enumerate(scene):
        pulita = {k: s[k] for k in ("foto", "originale", "fuoco", "video", "video_inizio", "secondi") if k in s}
        if i == 0 and str(s.get("gancio") or "").strip():
            pulita["gancio"] = str(s["gancio"]).strip()
        pulite.append(pulita)
    if len(pulite) > 1 and pulite[-1].get("foto") == pulite[0].get("foto") and not pulite[-1].get("video"):
        pulite[-1]["_loop"] = True
    return pulite


def monta(scene, uscita, seme_musica=1, audio_wav=None):
    """Monta il Reel v2 in 'uscita' (.mp4). Restituisce la durata; in monta.statistiche i conteggi per l'Analista."""
    scene = _solo_immagini(scene)
    durate = [a_tempo(s.get("secondi", 2.0)) for s in scene]
    inizi = [sum(durate[:i]) for i in range(len(scene))]
    totale = sum(durate)
    prima = foto_verticale(scene[0]) if scene[0].get("foto") else None
    sorgenti, tipi = [], []
    for i, s in enumerate(scene):
        if i == 0 and not s.get("video") and prima is not None:
            fonte, tipo = Foto(prima[0], prima[1], MOVIMENTI[0]), "foto"
        else:
            fonte, tipo = sorgente(s, i, prima)
        sorgenti.append(fonte)
        tipi.append(tipo)
    gancio = strato_gancio(scene[0]["gancio"]) if scene[0].get("gancio") else None
    meta = TRANSIZIONE / 2
    n_fotogrammi = int(round(totale * FPS))

    with tempfile.TemporaryDirectory() as tmp:
        wav = audio_wav or reel_musica.prepara_audio(Path(tmp) / "musica.wav", totale + 0.1, seme_musica, bpm=BPM, marcato=True)
        comando = [
            ffmpeg_exe(), "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{L}x{A}", "-r", str(FPS), "-i", "-",
            "-i", str(wav),
            "-map", "0:v", "-map", "1:a", "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-g", str(FPS * 2), "-bf", "2", "-flags", "+cgop", "-maxrate", "12M", "-bufsize", "24M",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
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
                    img = sorgenti[i].quadro(min(1.0, max(0.0, (tempo - da) / (a - da))), int(round((tempo - da) * FPS))).convert("RGB")
                    quadro = img if quadro is None else Image.blend(quadro, img, min(1.0, (tempo - da) / TRANSIZIONE))
                if gancio is not None and tempo < GANCIO_SECONDI:
                    quadro = quadro.convert("RGBA")
                    quadro.alpha_composite(gancio)
                proc.stdin.write(quadro.convert("RGB").tobytes())
        finally:
            proc.stdin.close()
            codice = proc.wait()
        if codice != 0:
            raise RuntimeError(f"ffmpeg ha restituito il codice {codice}")
    monta.statistiche = {"clip": tipi.count("clip"), "foto": tipi.count("foto"), "gancio": gancio is not None}
    return totale


def copertina(scena, uscita):
    """Copertina 9:16 senza scritte: il primo secondo della clip se la scena 1 è una clip, altrimenti la foto."""
    img = None
    if scena.get("video"):
        try:
            img = Clip(scena["video"], 1.0, float(scena.get("video_inizio", 0))).quadro(0, 15)
        except Exception as e:  # noqa: BLE001
            print(f"Copertina dalla clip non riuscita ({type(e).__name__}): uso la foto.")
    if img is None:
        img = foto_verticale(scena)[0].resize((L, A), Image.LANCZOS)
    img.convert("RGB").save(uscita, "JPEG", quality=93, subsampling=0)
    return uscita
