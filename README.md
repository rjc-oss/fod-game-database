# Fury of Dracula game database

A public collection of finished games of *Fury of Dracula: Digital Edition*, uploaded from the
Dracula mod's **Game Over** screen. Every game is kept as
the game's own save file, so any of them can be replayed move by move, and one row of a table says who
played, who won and how it ended.

**Search the games:** https://rjc-oss.github.io/fod-game-database/

Anyone who plays with the mod can upload. Uploads are public: the file holds the whole game and the
players' names as the game showed them. There are no accounts and nothing else about you is stored.

## Tournament and casual

Each game goes into one of two databases, chosen by which button was clicked:

- **tournament** — games played as part of the Discord community's tournament. A game sent there that was
  not part of it may be moved to casual or removed.
- **casual** — everything else.

A game can only be in one. If the same game is uploaded twice it is stored once; a `tournament` upload of
a game already stored as `casual` moves that row to `tournament`.

An upload is not on the site straight away: games are filed in batches, so a new one usually appears
within about an hour. Each new game is then announced in the Discord as **FoD Game Database**, with a
link that downloads the save.

## What's here

| Path | |
|---|---|
| `saves/` | the games themselves, one `.fod` each (the game's own save format) |
| `data/games.csv` | the table: one row per game, the canonical list |
| `data/rejected.csv` | uploads that were not stored, and why (no file is kept) |
| `docs/` | the search site (GitHub Pages), `index.json` built from `games.csv`; `config.js` holds the one copy of the repository's download URL |
| `tools/` | the Python that checks uploads, files them, and announces them in the Discord |
| `worker/` | the Cloudflare Worker the mod uploads to |
| `tests/` | `python -m pytest` |

## The table

`data/games.csv`, one row per game:

| Column | |
|---|---|
| `id` | upload id: the time the server received it, plus 8 random characters |
| `received_at_utc` | when the server received it (the file names use this time too) |
| `database` | `tournament` or `casual` |
| `file` | the save in `saves/` |
| `hunters`, `dracula` | player names as the game showed them; a person holding several hunter seats is named once. A seat still called after its character (`Van Helsing`, `Dracula`…) was the computer: the site and the Discord messages show it as `AI`, and the stored name stays as the game had it |
| `winner_side` | `Hunters` or `Dracula` |
| `winner_players`, `loser_players` | the names on each side |
| `influence` | Dracula's influence at the end (0–13; he wins at 13) |
| `dracula_health`, `dracula_max_health` | his health at the end, and the maximum in that game (15, or up to 25 with the mod's additional-health house rule) |
| `days_completed` | days the hunters survived |
| `score` | `influence − (dracula_max_health − dracula_health)`, plus 2 if the game lasted 21 days or more: what Dracula scored, less the damage the hunters dealt |
| `game_type` | `Local` or `Network` |
| `advanced_rules` | the advanced rules the game was set up with: power cards, free placement, rumour tokens, lairs |
| `house_rules` | the game's own house rules that were not the standard ones; empty means a standard game |
| `mod_house_rules` | the mod's house rules the game was played with, sliders with their number; empty means none |
| `has_ai` | whether any seat was played by the computer |
| `save_version` | the game's save format version |
| `mod_version`, `config_code` | the mod version and settings the game was played with — **a save only replays correctly with the same ones** |
| `identifier` | the game's own id, the same in every player's copy (this is what stops duplicates) |
| `log_sha256` | hash of the action log; two copies of one game that differ here went out of sync |
| `ended_at_utc` | when the game ended, by the uploading player's clock (not trusted, not used for naming) |
| `action_count` | how many actions the game took |

## Replaying a game

The `.fod` carries the setup it was played with: the advanced rules, the game's own house rules, and the
mod's house rules too — the mod keeps those inside the game's house rules, so they travel with the save
and with the online lobby. The three rules columns are read straight out of the file.

What the file does *not* carry is a version of the game or of the mod: `save_version` is only the save
format the game branches on, so `mod_version` and `config_code` are what tell you which build replays it.

## How a game gets here

```
mod (Game Over screen)  --POST .fod + result-->  Cloudflare Worker  --> Workers KV "incoming/"
                                                        |                        |
                                                        +--repository_dispatch-->+
                                                                                 v
                                            GitHub Actions: tools/process_incoming.py
                                          checks it, names it, commits saves/ + data/
                                                                                 |
                                                  tools/announce_discord.py  <----+
                                                  one message per new game, after the push
```

The announcement runs after the commit has been pushed, so its link already works. It is the last step
and cannot fail the run: with no `DISCORD_WEBHOOK_URL` secret, or a Discord that won't answer, the games
are in the repository just the same. One line per game, posted as **FoD Game Database**:

```
New tournament game available, AI Vs RToWin, with house rules Feed healing 3, mod 0.50.0 config 0C818D,
https://rjc-oss.github.io/fod-game-database/?save=Rtowin_Vs_Scotsword_2026-09-18_00-50-07.fod
```

The link names the **file**, not the upload id, because the file can be fetched as soon as the commit
is pushed, while the index the table is drawn from is only rebuilt when Pages redeploys a minute or so
later; `?save=<file>` downloads it whether or not the table has caught up (a plain link to the file
would show the JSON in the browser instead: raw.githubusercontent.com serves it as text). `?game=<id>`
still works and shows one game. Player names reach Discord as plain text with mentions turned off.

The Worker only does what is cheap (size, a header sniff, a rate limit) and never parses the file;
`tools/fodsave.py` does the real checking on a runner. An upload is stored only if it is a genuine
finished game: the save must be the game's own format, its action log must end with the game-over marker
and match its recorded size, the five seats must be there, and the result in the header must agree with
the file and with the game's victory conditions. Anything else lands in `data/rejected.csv` with a reason.

## Running the tools

Nothing to install: Python 3.12 and the standard library (`pytest` only for the tests).

```sh
# what the processor would do, without writing or deleting anything
CF_ACCOUNT_ID=... CF_KV_NAMESPACE_ID=... CF_API_TOKEN=... python tools/process_incoming.py --dry-run

# the Discord messages for a run's games, printed instead of posted
FOD_ANNOUNCE_FILE=... python tools/announce_discord.py --dry-run

# rebuild docs/index.json from data/games.csv
python tools/build_index.py

# preview the site exactly as GitHub Pages serves it, at http://localhost:8000/
python -m http.server -d docs 8000

# the tests (no network, no secrets)
python -m pytest
```

The Cloudflare values are also GitHub repository secrets (`CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`,
`CF_API_TOKEN`), which is how the workflow reads them, as is `DISCORD_WEBHOOK_URL` for the
announcements. Note that `wrangler kv key list` reads *local* state unless you pass `--remote`.
