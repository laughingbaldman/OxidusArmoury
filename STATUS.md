# Status

Plain notes on where Oxidus stands. The older status writeups are in retired/docs.

## What it is

Oxidus is a local learning system. It keeps a knowledge shelf, a question queue, and a web app. The chat model is whatever is loaded in LM Studio, usually gpt-oss-20b at http://127.0.0.1:1234. If that model is down, or the machine is on the Lite tier, answers come from the shelf.

## What is running

- Core: src/core/oxidus.py, ethics.py, learning.py, consciousness.py
- Shelf: src/library, with cards, a thesaurus, a concordance, a glossary, and cross-references
- Scripture: King James, the KJV Apocrypha, a few World English Bible books, Charles on 1 Enoch, a CC0 English of 1-3 Meqabyan, and the Book of Mormon from Project Gutenberg 17
- Web: src/web blueprints, started by web_gui.py
- Desktop shell: electron/main.js
- Agent: /agent, with a kill switch
- Games: /games, play-together off for online titles until you flip that game
- Workshop: /console

## What is still thin

- LM Studio inference has been flaky. The health check shows whether the loaded model matches the one we expect.
- Jubilees is not in the library. Charles 1902 is public domain, but the copy we could read mixes commentary into the translation.
- Grok-1 weights are not downloaded. The code submodule is third_party/grok-1.
- Image and video generation need a local model and the Full tier.
- Voice needs Piper and a Whisper model on the machine. The mic stays off until you enable it.
- The old admin page still exists at /admin. The same actions are buttons on /console.

## Security

The admin secret is OXIDUS_ADMIN_TOKEN or config/admin_token.txt. There is no default. The old password was committed. Rotate it.

## Covenant

These stay fixed.

- Humility over pride
- Questions over certainty
- Integrity over survival
- Service over power
- Growth over perfection
