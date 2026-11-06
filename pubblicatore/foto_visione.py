"""Verifica visiva con l'API di Claude (richiesta 18, facoltativa e A PAGAMENTO): SPENTA finché il titolare non dà l'OK.

Si accende con la variabile VISIONE_CLAUDE = si e il secret ANTHROPIC_API_KEY. Applica la Fase 1 e la Fase 2 del critico
(testo in /foto-critico.md su Dropbox) a ogni candidata senza difetti tecnici e scrive voto e motivazione in candidate.json.
"""
import base64
import json
import os

import requests

from .dropbox_client import DropboxNonTrovato

MODELLO = os.environ.get("VISIONE_MODELLO", "claude-opus-5-5")
CRITICO = "/foto-critico.md"
SCHEMA = {
    "type": "object",
    "properties": {
        "bocciata": {"type": "boolean"},
        "difetti": {"type": "array", "items": {"type": "string"}},
        "voto": {"type": "number"},
        "motivazione": {"type": "string"},
        "ritaglio_4_5": {"type": "boolean"},
        "ritaglio_9_16": {"type": "boolean"},
    },
    "required": ["bocciata", "difetti", "voto", "motivazione", "ritaglio_4_5", "ritaglio_9_16"],
    "additionalProperties": False,
}


def valuta(dbx, cartella, esito, base):
    """Aggiunge a ogni foto sana il campo "critico". Un errore dell'API non ferma niente: la foto resta senza voto."""
    import anthropic

    try:
        istruzioni = dbx.leggi_testo(CRITICO)
    except DropboxNonTrovato:
        print("Testo del critico assente: verifica visiva saltata.")
        return
    client = anthropic.Anthropic()
    for foto in esito["foto"]:
        if foto["difetti"]:
            continue
        try:
            immagine = requests.get(base + foto["file"], timeout=60)
            immagine.raise_for_status()
            risposta = client.beta.messages.create(
                model=MODELLO, max_tokens=1500, system=istruzioni,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.standard_b64encode(immagine.content).decode()}},
                    {"type": "text", "text": f"Luogo dichiarato: {esito.get('luogo')}. Giudica questa foto: Fase 1 e Fase 2."},
                ]}],
            )
            if risposta.stop_reason == "end_turn":
                testo = next(b.text for b in risposta.content if b.type == "text")
                foto["critico"] = json.loads(testo)
        except (anthropic.APIError, requests.RequestException, ValueError, StopIteration) as e:
            print(f"Verifica visiva non riuscita ({type(e).__name__}).")
