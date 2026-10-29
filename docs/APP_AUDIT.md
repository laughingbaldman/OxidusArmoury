# Oxidus app audit (2026-10-29)

Made by `scripts/app_audit.py` against the running app (`/api/app/routes`). It sends only safe GET requests;
routes that change data are listed but not fired. Re-run: `.venv\Scripts\python.exe scripts\app_audit.py`.

| Result | Count |
|---|---|
| Working (GET answered 200, no error) | 67 |
| Broken | 1 |
| Changes data (not fired, listed) | 63 |
| Needs an id in the URL | 1 |
| Skipped (static files) | 1 |
| **Total routes** | **133** |

## Fixed in this pass
- **LM Studio link**. Two things were wrong. Oxidus asked for a model that isn't installed (`openai/gpt-oss-20b`), and it sent no API token while LM Studio had "Require Authentication" on.
  - The model now defaults to `auto`, which picks whatever LM Studio has loaded and prefers `google/gemma-4-12b`. You can also pick it in Settings.
  - The API key is optional. It is read from `config/lmstudio_api_key.txt` or the `LMSTUDIO_API_KEY` environment variable (both gitignored), and it is sent only if set.
  - Chat now streams the reply, so a slow first token no longer trips a timeout.
  - Settings shows plain-language status ("server is off", "needs key", "Connected") and has a **Test connection** button.
- **Verified round trip:** `google/gemma-4-12b` replied "Oxidus link OK". The first words came after 13.4 s and the full reply after 51.7 s.
- **`test_wikipedia_check` error.** A helper named `test_*` was collected by pytest, which then asked for a `url` fixture. The domain check now lives in `src/utils/url_safety.py`, and there are parametrized tests for it, including spoofed domains. The Wikipedia client also has mocked tests, plus a live check that is skipped when offline.
- **Layout.**
  - Every page (Chat, Brain, Workshop, Games, Agent, Admin, Settings) now uses one shared header and grouped left nav: Talk, Brain, Library, Questions, Play, Settings, Admin.
  - There is one design system in `static/assets/oxidus-theme.css`. It has no CDN and no GPU effects.
  - The background video is off unless you turn it on in Settings > Appearance.
- **New Settings page** with LM Studio, Voice & mic, Obsidian vault, Ethics house rules, Memory, Performance tier and Appearance. Anything not wired up is labelled "Not built yet".
- Workshop sections can be linked directly (`/console#Shelf`, `#Bible`, `#Questions`).

## Still broken / not built
- `/api/console/finetune`: "Dataset file is missing." There is no fine-tune dataset yet. This is expected until one is created, so the route was left as is.
- Voice: the mic and mute switches are saved, but no speech-to-text or text-to-speech is connected (Whisper and Piper aren't installed or wired). This is marked in Settings.
- Editing or forgetting a single memory from the UI is not built.

## Broken
| Route | Methods | Note |
|---|---|---|
| `/api/console/finetune` | GET,POST | HTTP 200 error: Dataset file is missing. |

## Needs an id
| Route | Methods | Note |
|---|---|---|
| `/api/console/entry/<entry_id>` | GET | needs entry_id |

## Working
| Route | Methods | Note |
|---|---|---|
| `/` | GET | HTTP 200 109302B |
| `/admin` | GET | HTTP 200 129115B |
| `/agent` | GET | HTTP 200 6580B |
| `/api/admin/access-heat` | GET | HTTP 401 (admin token required) |
| `/api/admin/archive/manifest` | GET | HTTP 401 (admin token required) |
| `/api/admin/archive/preview` | GET | HTTP 401 (admin token required) |
| `/api/admin/conversation` | GET | HTTP 401 (admin token required) |
| `/api/admin/health` | GET | HTTP 401 (admin token required) |
| `/api/admin/hybrid-status` | GET | HTTP 401 (admin token required) |
| `/api/admin/indexing/metrics` | GET | HTTP 401 (admin token required) |
| `/api/admin/indexing/status` | GET | HTTP 401 (admin token required) |
| `/api/admin/integrity` | GET | HTTP 401 (admin token required) |
| `/api/admin/knowledge-config` | GET,POST | HTTP 401 (admin token required) |
| `/api/admin/learning-trace` | GET | HTTP 401 (admin token required) |
| `/api/admin/memryx/devices` | GET | HTTP 401 (admin token required) |
| `/api/admin/memryx/health` | GET | HTTP 401 (admin token required) |
| `/api/admin/notes` | GET,POST | HTTP 401 (admin token required) |
| `/api/admin/ops/summary` | GET | HTTP 401 (admin token required) |
| `/api/admin/search/overview` | GET | HTTP 401 (admin token required) |
| `/api/admin/status` | GET | HTTP 401 (admin token required) |
| `/api/admin/telemetry/recent` | GET | HTTP 401 (admin token required) |
| `/api/admin/tiering/overview` | GET | HTTP 401 (admin token required) |
| `/api/agent/status` | GET | HTTP 200 722B |
| `/api/app/routes` | GET | HTTP 200 13016B |
| `/api/brain/activity` | GET | HTTP 200 22B |
| `/api/brain/graph` | GET | HTTP 202 31B |
| `/api/chat-style` | GET,POST | HTTP 200 21B |
| `/api/console/bible/books` | GET | HTTP 200 12270B |
| `/api/console/bible/chapter` | GET | HTTP 200 5175B |
| `/api/console/bible/verse` | GET | HTTP 200 12B |
| `/api/console/ethics` | GET,POST | HTTP 200 482B |
| `/api/console/glossary` | GET,POST | HTTP 200 1304B |
| `/api/console/graph` | GET,POST | HTTP 200 28B |
| `/api/console/logs` | GET | HTTP 200 41B |
| `/api/console/models` | GET,POST | HTTP 200 446B |
| `/api/console/questions` | GET | HTTP 200 17B |
| `/api/console/search` | GET | HTTP 200 102B |
| `/api/console/skills` | GET | HTTP 200 193B |
| `/api/console/status` | GET | HTTP 200 903B |
| `/api/console/thesaurus` | GET,POST | HTTP 200 658B |
| `/api/console/toc` | GET | HTTP 200 27094B |
| `/api/console/voice` | GET,POST | HTTP 200 270B |
| `/api/conversation` | GET | HTTP 200 7002B |
| `/api/files/recent` | GET | HTTP 200 1191B |
| `/api/games/board` | GET,POST | HTTP 200 199B |
| `/api/games/library` | GET | HTTP 200 1300B |
| `/api/health` | GET | HTTP 200 2290B |
| `/api/knowledge` | GET | HTTP 200 17619B |
| `/api/lm-studio-status` | GET | HTTP 200 618B |
| `/api/memories` | GET | HTTP 200 119B |
| `/api/mode` | GET,POST | HTTP 200 1034B |
| `/api/mode-analysis` | GET | HTTP 200 905B |
| `/api/obsidian/search` | GET | HTTP 200 15B |
| `/api/obsidian/status` | GET | HTTP 200 363B |
| `/api/ops/mode` | GET,POST | HTTP 200 140B |
| `/api/ops/summary` | GET | HTTP 200 9739B |
| `/api/settings/lm-studio` | GET | HTTP 200 434B |
| `/api/settings/overview` | GET | HTTP 200 256B |
| `/api/status` | GET | HTTP 200 104B |
| `/api/thoughts` | GET | HTTP 200 2049B |
| `/api/understanding` | GET | HTTP 200 108B |
| `/api/wiki-crawl/settings` | GET,POST | HTTP 401 (admin token required) |
| `/api/wiki-crawl/status` | GET | HTTP 401 (admin token required) |
| `/brain` | GET | HTTP 200 10132B |
| `/console` | GET | HTTP 200 15858B |
| `/games` | GET | HTTP 200 6717B |
| `/settings` | GET | HTTP 200 14565B |

## Changes data (not fired by the audit)
| Route | Methods | Note |
|---|---|---|
| `/api/admin/archive/execute` | POST | not fired (changes data) |
| `/api/admin/archive/restore` | POST | not fired (changes data) |
| `/api/admin/cache/clear` | POST | not fired (changes data) |
| `/api/admin/cache/warm` | POST | not fired (changes data) |
| `/api/admin/cleanup-external` | POST | not fired (changes data) |
| `/api/admin/clear-conversation` | POST | not fired (changes data) |
| `/api/admin/indexing/start` | POST | not fired (changes data) |
| `/api/admin/knowledge/dedupe` | POST | not fired (changes data) |
| `/api/admin/knowledge/rebuild-dedupe` | POST | not fired (changes data) |
| `/api/admin/maintenance/run` | POST | not fired (changes data) |
| `/api/admin/memryx/benchmark` | POST | not fired (changes data) |
| `/api/admin/moltbook/ingest` | POST | not fired (changes data) |
| `/api/admin/notes/review` | POST | not fired (changes data) |
| `/api/admin/rebuild-knowledge` | POST | not fired (changes data) |
| `/api/admin/reset-knowledge` | POST | not fired (changes data) |
| `/api/admin/restart` | POST | not fired (changes data) |
| `/api/admin/safe-mode/clear` | POST | not fired (changes data) |
| `/api/admin/shutdown` | POST | not fired (changes data) |
| `/api/admin/stop-autonomy` | POST | not fired (changes data) |
| `/api/admin/validate-e2e` | POST | not fired (changes data) |
| `/api/agent/approve` | POST | not fired (changes data) |
| `/api/agent/config` | POST | not fired (changes data) |
| `/api/agent/kill` | POST | not fired (changes data) |
| `/api/agent/resume` | POST | not fired (changes data) |
| `/api/agent/run` | POST | not fired (changes data) |
| `/api/agent/schedule` | POST | not fired (changes data) |
| `/api/ask-lm-studio` | POST | not fired (changes data) |
| `/api/ask-novel-question` | POST | not fired (changes data) |
| `/api/clear` | POST | not fired (changes data) |
| `/api/console/desktop/approve` | POST | not fired (changes data) |
| `/api/console/desktop/propose` | POST | not fired (changes data) |
| `/api/console/desktop/reject` | POST | not fired (changes data) |
| `/api/console/entry` | POST | not fired (changes data) |
| `/api/console/entry/<entry_id>/archive` | POST | not fired (changes data) |
| `/api/console/questions/<question_id>/answer` | POST | not fired (changes data) |
| `/api/console/tier` | POST | not fired (changes data) |
| `/api/console/tools/image` | POST | not fired (changes data) |
| `/api/console/tools/predict` | POST | not fired (changes data) |
| `/api/console/tools/video` | POST | not fired (changes data) |
| `/api/files/open` | POST | not fired (changes data) |
| `/api/games/coach` | POST | not fired (changes data) |
| `/api/games/companion` | POST | not fired (changes data) |
| `/api/games/launch` | POST | not fired (changes data) |
| `/api/games/player2` | POST | not fired (changes data) |
| `/api/games/player2/act` | POST | not fired (changes data) |
| `/api/games/toggle` | POST | not fired (changes data) |
| `/api/games/warning` | POST | not fired (changes data) |
| `/api/initiate` | POST | not fired (changes data) |
| `/api/message` | POST | not fired (changes data) |
| `/api/obsidian/export-brain` | POST | not fired (changes data) |
| `/api/obsidian/note` | POST | not fired (changes data) |
| `/api/obsidian/sync` | POST | not fired (changes data) |
| `/api/process-ai-response` | POST | not fired (changes data) |
| `/api/recall` | POST | not fired (changes data) |
| `/api/settings/lm-studio` | POST | not fired (changes data) |
| `/api/settings/lm-studio/test` | POST | not fired (changes data) |
| `/api/settings/obsidian` | POST | not fired (changes data) |
| `/api/wiki-crawl/resume` | POST | not fired (changes data) |
| `/api/wiki-crawl/settings/reset` | POST | not fired (changes data) |
| `/api/wiki-crawl/start` | POST | not fired (changes data) |
| `/api/wiki-crawl/stop` | POST | not fired (changes data) |
| `/api/wiki-page` | POST | not fired (changes data) |
| `/api/wiki-search` | POST | not fired (changes data) |

## Screenshots
Before and after shots are in `docs/screens/`: `before-*.png` and `after-*.png` for home, console, games, agent, brain and admin, plus `after-settings.png`.
