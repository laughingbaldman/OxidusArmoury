# Oxidus

A local system I can run on my own machine. It keeps notes, asks when it does not know, and answers from what it has stored before it spends a model call.

## Run it

```bash
pip install -r requirements.txt
python web_gui.py
```

Then open http://127.0.0.1:5000

- Chat is `/`
- Workshop is `/console`
- Agent is `/agent`
- Games are `/games`
- The old admin page is still `/admin`

`python launch_oxidus.py` starts the same server and, on Windows, the Electron window.

LM Studio, if you use it, listens at http://127.0.0.1:1234. The usual model is openai/gpt-oss-20b. On a small machine the Lite tier skips that and answers from the shelf.

## Admin secret

Set `OXIDUS_ADMIN_TOKEN`, or put the secret in `config/admin_token.txt`. That file is not in git. There is no built-in password. The old one was committed. Rotate it.

## What is in the tree

- `src/core` is the brain: ethics, learning, the main class
- `src/library` is the shelf: cards first, full text when you ask
- `src/web` is the Flask app, split into blueprints
- `data/knowledge_base` holds the texts, including scripture
- `electron/` is the desktop window

Scripture on disk:

- King James, public domain, including the 1611 Apocrypha
- A few World English Bible books (3 and 4 Maccabees, Psalm 151)
- 1 Enoch, R.H. Charles 1917
- 1-3 Meqabyan, a CC0 English from modern Amharic, marked provisional
- Book of Mormon, Project Gutenberg eBook 17, tagged Latter-day Saint

Jubilees is not loaded. Charles 1902 is public domain, but the copy I could read mixes the notes into the translation.

## Tiers

Lite is the floor: a 4-core machine, 8 to 16 GB, no GPU. It uses the shelf, and voice stays on Whisper tiny and Piper if those are installed. Standard uses LM Studio. Full is for Grok-1 and image or video generation, and you have to turn it on. Grok-1 weights are about 300 GB and are not in this repo.

## Voice

The microphone is off until you enable it on the workshop page. The wake word is Oxidus. Push-to-talk is Ctrl+Space. There is a listening mark on the chat page.

## The covenant

These are not settings.

- Humility over pride
- Questions over certainty
- Integrity over survival
- Service over power
- Growth over perfection

If it breaks those, it is not Oxidus anymore. Capability does not buy it a pass.
