# Pubblicatore automatico per Instagram

Pubblica caroselli, Storie e Reel su un account Instagram professionale con l'**API ufficiale di Instagram** (Instagram Login), leggendo i contenuti preparati da una redazione su Dropbox. Gira su GitHub Actions.

**© Tutti i diritti riservati.** Questo codice è visibile pubblicamente ma NON è concesso in licenza: non è permesso copiarlo, modificarlo, distribuirlo o riusarlo, in tutto o in parte, senza autorizzazione scritta del proprietario.

## Cosa contiene
- `pubblicatore/main.py` — caroselli e Storie (due fasce al giorno).
- `pubblicatore/reel.py`, `reel_montaggio.py`, `reel_musica.py` — montaggio (foto 9:16, scritte, musica originale) e pubblicazione dei Reel, separati dai caroselli.
- `pubblicatore/statistiche.py` — Insights dei post e dati aggregati del pubblico.
- `pubblicatore/reel_montaggio_v2.py` — montaggio v2 (movimento, scritte animate, tagli a tempo, finale in loop, clip video).
- `pubblicatore/commenti.py` — avviso dei commenti con la parola chiave.
- `pubblicatore/guida.py` — risposte di viaggio ai DM (bozze) e inoltro via email di appuntamenti e richieste commerciali.
- `pubblicatore/ispirazione.py` — comando Telegram `/esempio` per salvare Reel d'esempio.
- `pubblicatore/messaggio.py` — messaggio manuale al titolare (testo letto da Dropbox, mai nel repository).
- `pubblicatore/rinnova_token.py`, `verifica.py` — manutenzione.

## Configurazione
Tutto ciò che identifica l'account e i contenuti sta FUORI dal repository:
- secret: `IG_TOKEN`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PROFILO`, `REFERENTE`, `GH_PAT_SECRETS`, `EMAIL_CONTATTO` (email data alle persone), `EMAIL_MITTENTE`, `EMAIL_PASSWORD_APP`, `EMAIL_DESTINATARIO` (nei log compaiono sempre come ***);
- variabili: `ATTIVO` (`si` per far partire i workflow), `PAUSA`, `PAUSA_REEL`, `RICHIEDI_APPROVAZIONE`, `PUBBLICA_STORIA`, `MONTAGGIO_REEL` (`v1`/`v2`), `GUIDA_INVIO` (`automatico` per far rispondere la Guida da sola);
- contenuti, istruzioni, prompt, brand e storico: solo su Dropbox, letti al momento dell'esecuzione.
I log scrivono solo l'esito delle operazioni.
