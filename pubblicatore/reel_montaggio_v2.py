"""Montaggio dei Reel v2 (richieste 14, 16, 17, 18 e 19): solo immagini, ritmo, massima qualità. Il v1 (reel_montaggio.py)
resta com'è: si sceglie con la variabile MONTAGGIO_REEL (v1 / v2), così si torna indietro in un minuto.
La versione prima della richiesta 19 (foto con zoom/spostamento del 15-20%) è in archivio/reel_montaggio_v2-pre19.py.

Regole (richiesta 19):
- FOTO FERME: nessuno zoom, spostamento o Ken Burns. Il ritmo lo danno i tagli a tempo di musica, taglio netto.
  Variabile MOVIMENTO_REEL = "lento" (solo con il sì di Ale): avvicinamento continuo del 3,5% su tutta la scena,
  lineare, calcolato da un'immagine sovracampionata e ritagliato con coordinate frazionarie (niente scatti).
- nessuna scritta sul video: testo/sottotesto/etichetta/locali si ignorano. Il campo "gancio" sulla scena 1 si usa
  solo con la variabile GANCIO_REEL = "si" (sì di Ale): una frase di massimo 7 parole, almeno 2,7 s sullo schermo,
  carattere elegante e grande nella zona sicura, velatura sfumata, comparsa morbida;
- il 9:16 si ritaglia dalla foto intera ad alta risoluzione (campo "originale"), mai ingrandita oltre 1,0×;
- clip video vere solo se nel verticale restano almeno 1080 px di larghezza; altrimenti si usa la foto;
- scene di 1-3 s a tempo con la musica (120 bpm), finale in loop sulla prima scena;
- uscita 1080×1920, 30 fps, H.264 High 4:2:0, CRF 18, AAC 48 kHz, moov all'inizio.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import foto_controllo, reel_musica
from .reel_montaggio import A, BIANCO, FPS, L, MARGINE_DX, MARGINE_SX, _a_capo, _font, ffmpeg_exe

BPM = 120
ZOOM_LENTO = 1.035     # variante "lento": avvicinamento massimo 3,5% su tutta la scena
SOVRACAMPIONA = 2      # la variante lenta lavora su un'immagine grande il doppio (se i pixel ci sono), poi riduce
GANCIO_MIN_SECONDI = 3.0   # con il gancio la scena 1 dura almeno 3 s (la frase resta a schermo ~2,7 s)
SCENA_MAX_SECONDI = 3.0
FORMATO = L / A
MIN_LARGHEZZA_CLIP = L
UA = "Mozilla/5.0 (X11; Linux x86_64)"  # alcuni archivi di video rifiutano il nome predefinito di ffmpeg


def movimento():
    """'fermo' (predefinito, richiesta 19) o 'lento' (solo dopo il sì di Ale)."""
    return "lento" if os.environ.get("MOVIMENTO_REEL", "").strip().lower() == "lento" else "fermo"


def gancio_acceso():
    return os.environ.get("GANCIO_REEL", "").strip().lower() in ("si", "sì", "true", "1")


def transizione():
    """Taglio netto. Dissolvenza (al massimo 0,2 s) solo se richiesta con TRANSIZIONE_REEL in secondi."""
    try:
        return min(0.2, max(0.0, float(os.environ.get("TRANSIZIONE_REEL", "0") or 0)))
    except ValueError:
        return 0.0


class FotoNonAdatta(Exception):
    """La foto non regge il 9:16 a piena risoluzione: la scena va tolta o sostituita."""


# ───────────────────────────── gancio (solo scena 1, con il sì di Ale) ─────────────────────────────

# Font elegante e molto leggibile (SIL Open Font License), scaricato da Google Fonts come gli altri.
_FONT_GANCIO = ("PlayfairDisplay.ttf", "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf")
GANCIO_Y = 560          # centro verticale del blocco di testo: terzo alto, lontano da nome/audio in alto
GANCIO_ENTRA = 0.15     # secondi: comparsa…
GANCIO_SFUMA = 0.45     # …morbida in dissolvenza


def strato_gancio(testo):
    """La frase-gancio grande nella zona sicura, con velatura sfumata dietro (mai riquadri) e ombra morbida."""
    from . import reel_montaggio
    reel_montaggio.FONT.setdefault("gancio", _FONT_GANCIO)
    larghezza = L - MARGINE_SX - MARGINE_DX
    dimensione = 96
    while True:
        font = _font("gancio", dimensione, 600)
        righe = _a_capo(testo, font, larghezza)
        if (len(righe) <= 3 and all(font.getlength(r) <= larghezza for r in righe)) or dimensione <= 64:
            break
        dimensione -= 4
    interlinea = int(dimensione * 1.18)
    alto = interlinea * len(righe)
    y0 = GANCIO_Y - alto // 2
    strato = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    # velatura: banda scura molto sfumata dietro il testo (massimo ~38% di opacità al centro)
    velo = Image.new("L", (L, A), 0)
    ImageDraw.Draw(velo).rectangle((0, y0 - 60, L, y0 + alto + 60), fill=96)
    strato.putalpha(velo.filter(ImageFilter.GaussianBlur(70)))
    ombra = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    testo_img = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    d_ombra, d = ImageDraw.Draw(ombra), ImageDraw.Draw(testo_img)
    for i, riga in enumerate(righe):
        x = MARGINE_SX + (larghezza - font.getlength(riga)) / 2  # centrato nella zona sicura (a destra i pulsanti)
        y = y0 + i * interlinea
        d_ombra.text((x + 2, y + 3), riga, font=font, fill=(0, 0, 0, 190))
        d.text((x, y), riga, font=font, fill=BIANCO + (255,))
    strato.alpha_composite(ombra.filter(ImageFilter.GaussianBlur(5)))
    strato.alpha_composite(testo_img)
    return strato


def _opacita_gancio(tempo, fine):
    if tempo < GANCIO_ENTRA:
        return 0.0
    if tempo < GANCIO_ENTRA + GANCIO_SFUMA:
        return (tempo - GANCIO_ENTRA) / GANCIO_SFUMA
    if tempo > fine - 0.25:
        return max(0.0, (fine - tempo) / 0.25)
    return 1.0


# ───────────────────────────── immagini ─────────────────────────────

def foto_verticale(scena):
    """Ritaglio 9:16 dalla foto intera, alla risoluzione piena (larghezza ≥ L). Mai ingrandito."""
    dati = foto_controllo.scarica(foto_controllo.url_originale(scena.get("originale") or scena["foto"]))
    img = foto_controllo.ritaglia(foto_controllo.apri(dati), FORMATO, (scena.get("fuoco") or [0.5, 0.5])[:2])
    if img.width < L:
        raise FotoNonAdatta(f"nel 9:16 restano {img.width} px di larghezza")
    return img


class Foto:
    """Foto ferma (predefinito) o avvicinamento lentissimo (variante 'lento')."""

    def __init__(self, img, lento=False):
        self.lento = lento and img.width >= L * ZOOM_LENTO  # senza pixel di margine resta ferma
        if self.lento:
            w = min(img.width, L * SOVRACAMPIONA)
            self.img = img.resize((w, round(w / FORMATO)), Image.LANCZOS)
        else:
            self.img = img.resize((L, A), Image.LANCZOS)
            self.fisso = self.img

    def quadro(self, avanzamento, _n):
        if not self.lento:
            return self.fisso
        zoom = 1 + (ZOOM_LENTO - 1) * avanzamento  # lineare, nessuna accelerazione
        w, h = self.img.width / zoom, self.img.height / zoom
        dx, dy = (self.img.width - w) / 2, (self.img.height - h) / 2
        # coordinate frazionarie + riduzione Lanczos da un'immagine più grande: nessun arrotondamento al pixel, niente tremolio
        return self.img.resize((L, A), Image.LANCZOS, box=(dx, dy, dx + w, dy + h))


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


def sorgente(scena, i, prima, lento):
    """Clip se c'è e regge il verticale, altrimenti foto. 'prima' = ritaglio della scena 1, per chiudere il loop."""
    if scena.get("video"):
        try:
            return Clip(scena["video"], float(scena.get("secondi", 2.0)), float(scena.get("video_inizio", 0))), "clip"
        except Exception as e:  # noqa: BLE001 — una clip che non si apre non ferma il Reel: si usa la foto
            print(f"Clip della scena {i + 1} non usata ({type(e).__name__}): uso la foto.")
    if prima is not None and scena.get("_loop"):
        return Foto(prima, lento), "foto"  # finale in loop: stessa foto della prima scena
    return Foto(foto_verticale(scena), lento), "foto"


# ───────────────────────────── montaggio ─────────────────────────────

def a_tempo(secondi):
    """Arrotonda la durata di una scena al mezzo battito (0,25 s a 120 bpm) più vicino, tra 1 e 3 s
    (3 s = variante L "scene lunghe" della riunione del 09/11)."""
    return min(SCENA_MAX_SECONDI, max(1.0, round(float(secondi) * 4) / 4))


def _solo_immagini(scene, con_gancio=None):
    """Delle scene tiene solo i campi delle immagini: scritte e nomi non finiscono mai sul video (richieste 16 e 19).
    Il gancio della scena 1 resta solo se GANCIO_REEL è acceso (o con_gancio=True nelle prove)."""
    con_gancio = gancio_acceso() if con_gancio is None else con_gancio
    pulite = []
    for i, s in enumerate(scene):
        pulita = {k: s[k] for k in ("foto", "originale", "fuoco", "video", "video_inizio", "secondi") if k in s}
        if i == 0 and con_gancio and str(s.get("gancio") or "").strip():
            pulita["gancio"] = str(s["gancio"]).strip()
        pulite.append(pulita)
    if len(pulite) > 1 and pulite[-1].get("foto") == pulite[0].get("foto") and not pulite[-1].get("video"):
        pulite[-1]["_loop"] = True
    return pulite


def monta(scene, uscita, seme_musica=1, audio_wav=None, lento=None, con_gancio=None):
    """Monta il Reel v2 in 'uscita' (.mp4). Restituisce la durata; in monta.statistiche i conteggi per l'Analista.
    lento/con_gancio = None → valgono le variabili MOVIMENTO_REEL e GANCIO_REEL (le prove li forzano)."""
    lento = (movimento() == "lento") if lento is None else lento
    scene = _solo_immagini(scene, con_gancio)
    durate = [a_tempo(s.get("secondi", 2.0)) for s in scene]
    if scene[0].get("gancio"):
        durate[0] = max(durate[0], GANCIO_MIN_SECONDI)  # il tempo di leggerla (richiesta 19, punto 3)
    inizi = [sum(durate[:i]) for i in range(len(scene))]
    totale = sum(durate)
    prima = foto_verticale(scene[0]) if scene[0].get("foto") else None
    sorgenti, tipi = [], []
    for i, s in enumerate(scene):
        if i == 0 and not s.get("video") and prima is not None:
            fonte, tipo = Foto(prima, lento), "foto"
        else:
            fonte, tipo = sorgente(s, i, prima, lento)
        sorgenti.append(fonte)
        tipi.append(tipo)
    gancio = strato_gancio(scene[0]["gancio"]) if scene[0].get("gancio") else None
    TRANSIZIONE = transizione()
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
                    if quadro is None or TRANSIZIONE <= 0:
                        quadro = img
                    else:
                        quadro = Image.blend(quadro, img, min(1.0, (tempo - da) / TRANSIZIONE))
                if gancio is not None and tempo < durate[0]:
                    opacita = _opacita_gancio(tempo, durate[0])
                    if opacita > 0:
                        strato = gancio if opacita >= 1 else Image.blend(Image.new("RGBA", gancio.size, (0, 0, 0, 0)), gancio, opacita)
                        quadro = quadro.convert("RGBA")
                        quadro.alpha_composite(strato)
                proc.stdin.write(quadro.convert("RGB").tobytes())
        finally:
            proc.stdin.close()
            codice = proc.wait()
        if codice != 0:
            raise RuntimeError(f"ffmpeg ha restituito il codice {codice}")
    monta.statistiche = {"clip": tipi.count("clip"), "foto": tipi.count("foto"), "gancio": gancio is not None,
                         "movimento": "lento" if lento else "fermo", "transizione": TRANSIZIONE,
                         "scena1_secondi": durate[0]}
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
        img = foto_verticale(scena).resize((L, A), Image.LANCZOS)
    img.convert("RGB").save(uscita, "JPEG", quality=93, subsampling=0)
    return uscita
