"""Musica ORIGINALE per i Reel, generata qui (nessuna libreria di terzi → nessuna licenza da verificare,
nessun rischio di audio silenziato da Instagram per diritti d'autore).

Brano leggero e luminoso: accordi morbidi, arpeggio pizzicato, basso e percussioni leggere.
Ogni "seme" cambia tonalità, progressione e arpeggio, così i Reel non suonano tutti uguali.
"""
import wave

import numpy as np

SR = 48000  # Instagram: AAC max 48 kHz

# Progressioni (gradi della scala maggiore, 0 = I)
PROGRESSIONI = [
    [0, 4, 5, 3],   # I V vi IV
    [0, 5, 3, 4],   # I vi IV V
    [5, 3, 0, 4],   # vi IV I V
    [0, 3, 5, 4],   # I IV vi V
]
TONALITA = [62, 60, 65, 67, 64]  # D, C, F, G, E (nota MIDI della tonica)
SCALA = [0, 2, 4, 5, 7, 9, 11]


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _accordo(tonica, grado):
    """Triade diatonica sul grado indicato (note MIDI)."""
    note = []
    for salto in (0, 2, 4):
        i = grado + salto
        note.append(tonica + SCALA[i % 7] + 12 * (i // 7))
    return note


def _inviluppo(n, attacco, rilascio):
    env = np.ones(n)
    a, r = min(int(attacco * SR), n), min(int(rilascio * SR), n)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _pad(freq, durata, rng):
    t = np.arange(int(durata * SR)) / SR
    s = np.zeros_like(t)
    for detune in (-0.12, 0.0, 0.11):
        f = freq * 2 ** (detune / 12)
        fase = rng.uniform(0, 2 * np.pi)
        for k in range(1, 7):  # "dente di sega" morbido: poche armoniche
            s += np.sin(2 * np.pi * f * k * t + fase * k) / (k ** 1.6)
    return s * _inviluppo(len(t), 0.5, 0.6) / 3


def _pizzico(freq, durata):
    t = np.arange(int(durata * SR)) / SR
    s = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
         + 0.12 * np.sin(2 * np.pi * 3 * freq * t))
    return s * np.exp(-t * 6.0) * _inviluppo(len(t), 0.004, 0.05)


def _basso(freq, durata):
    t = np.arange(int(durata * SR)) / SR
    s = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 2 * freq * t)
    return s * np.exp(-t * 2.5) * _inviluppo(len(t), 0.01, 0.08)


def _cassa(durata=0.35):
    t = np.arange(int(durata * SR)) / SR
    f = 50 + 70 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 12)


def _shaker(rng, durata=0.08):
    n = int(durata * SR)
    rumore = rng.standard_normal(n)
    rumore = np.diff(rumore, prepend=0)  # passa-alto semplice
    return rumore * np.exp(-np.arange(n) / SR * 60)


def _passa_basso(x, taglio):
    a = np.exp(-2 * np.pi * taglio / SR)
    y = np.empty_like(x)
    acc = 0.0
    # filtro a un polo; vettorizzato a blocchi sarebbe più veloce, ma 15 s vanno bene così
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def _riverbero(x, rng):
    """Riverbero leggero: alcune eco sfumate a destra e sinistra."""
    sx, dx = x.copy(), x.copy()
    for ritardo, guadagno in ((0.031, 0.35), (0.047, 0.3), (0.071, 0.25), (0.113, 0.2), (0.167, 0.15), (0.241, 0.1)):
        d = int(ritardo * SR)
        sx[d:] += x[:-d] * guadagno * rng.uniform(0.8, 1.0)
        d2 = int(ritardo * 1.13 * SR)
        dx[d2:] += x[:-d2] * guadagno * rng.uniform(0.8, 1.0)
    return sx, dx


def _aggiungi(dest, suono, inizio):
    i = int(inizio * SR)
    if i >= len(dest):
        return
    fine = min(len(dest), i + len(suono))
    dest[i:fine] += suono[: fine - i]


def genera(durata, seme=1, bpm=96, marcato=False):
    """Restituisce un array stereo float32 (n, 2) di 'durata' secondi.
    marcato=True (montaggio v2): percussioni da subito e cassa su ogni battito, per sentire i tagli a tempo."""
    rng = np.random.default_rng(seme)
    tonica = TONALITA[seme % len(TONALITA)]
    prog = PROGRESSIONI[seme % len(PROGRESSIONI)]
    battito = 60 / bpm
    battute = int(np.ceil(durata / (4 * battito))) + 1
    n = int((durata + 1) * SR)
    pad, arp, basso, perc = (np.zeros(n) for _ in range(4))
    schema_arp = [[0, 1, 2, 1, 0, 1, 2, 3], [0, 2, 1, 2, 0, 2, 3, 2], [2, 1, 0, 1, 2, 3, 2, 1]][seme % 3]

    for b in range(battute):
        accordo = _accordo(tonica, prog[b % len(prog)])
        t0 = b * 4 * battito
        for nota in accordo:
            _aggiungi(pad, _pad(_hz(nota - 12), 4 * battito + 0.6, rng), t0)
        _aggiungi(basso, _basso(_hz(accordo[0] - 24), 2 * battito), t0)
        _aggiungi(basso, _basso(_hz(accordo[0] - 24), 2 * battito), t0 + 2 * battito)
        note_arp = accordo + [accordo[0] + 12]
        for j, idx in enumerate(schema_arp):
            _aggiungi(arp, _pizzico(_hz(note_arp[idx] + 12), battito * 1.5) * rng.uniform(0.75, 1.0),
                      t0 + j * battito / 2)
        if b >= 1 or marcato:  # v1: le percussioni entrano dalla seconda battuta, il primo secondo resta "aperto"
            for k in ((0, 1, 2, 3) if marcato else (0, 2)):
                _aggiungi(perc, _cassa() * 0.9, t0 + k * battito)
            for k in range(4):
                _aggiungi(perc, _shaker(rng) * 0.25, t0 + k * battito + battito / 2)

    pad = _passa_basso(pad, 1800)
    misto = pad * 0.22 + arp * 0.22 + basso * 0.35 + perc * 0.3
    sx, dx = _riverbero(misto, rng)
    stereo = np.stack([sx + arp * 0.04, dx - arp * 0.04], axis=1)[: int(durata * SR)]
    stereo *= _inviluppo(len(stereo), 0.25, 1.4)[:, None]
    picco = np.max(np.abs(stereo)) or 1.0
    return (stereo / picco * 0.7).astype(np.float32)


def salva_wav(percorso, durata, seme=1, bpm=96, marcato=False):
    dati = genera(durata, seme, bpm, marcato)
    pcm = (np.clip(dati, -1, 1) * 32767).astype("<i2")
    with wave.open(str(percorso), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return percorso
