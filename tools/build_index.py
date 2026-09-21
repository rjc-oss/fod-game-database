"""data/games.csv -> docs/index.json, the file the search site loads.

The csv is the canonical table; index.json is just the same rows as JSON, newest first, with the numbers
and flags as real JSON types so the site doesn't have to parse strings. It adds one thing the csv doesn't
have: each game's `tournament`, worked out from data/tournaments.csv (see tools/tournaments.py), and the
list of tournaments itself for the site's dropdown. Run by process_incoming.py after every batch, and by
hand with: python tools/build_index.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tournaments as tournament_list  # noqa: E402
from fodsave import BOOL_COLUMNS, INT_COLUMNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAMES_CSV = ROOT / "data" / "games.csv"
INDEX_JSON = ROOT / "docs" / "index.json"

# Fields index.json gives each game that games.csv doesn't have.
DERIVED_COLUMNS = ("tournament",)


def read_games(path=GAMES_CSV):
    """Every row of games.csv as a dict, in file order, with numbers and flags typed."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [_typed(row) for row in csv.DictReader(handle)]


def build(games_csv=GAMES_CSV, index_json=INDEX_JSON, tournaments_csv=None):
    """Writes index.json and returns how many games it holds.

    A tournaments.csv that breaks its rules never stops the build (the batch's games must still be
    filed): the index is built without tournaments, and the problem printed. process_incoming.py also
    lists it under Warnings in the run's summary.
    """
    games = read_games(games_csv)
    try:
        tournaments = tournament_list.load(tournaments_csv)
    except tournament_list.TournamentError as e:
        print("tournaments.csv ignored: {}".format(e), file=sys.stderr)
        tournaments = []
    for game in games:
        game["tournament"] = tournament_list.assign(game, tournaments)
    # Newest first; ids start with the receipt time, so they sort by time.
    games.sort(key=lambda game: game.get("id", ""), reverse=True)
    index = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(games),
        # Newest first, the order the site's dropdown lists them in.
        "tournaments": [tournament.to_json() for tournament in reversed(tournaments)],
        "games": games,
    }
    index_json.parent.mkdir(parents=True, exist_ok=True)
    with index_json.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(index, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    return len(games)


def _typed(row):
    game = dict(row)
    for column in INT_COLUMNS:
        try:
            game[column] = int(game.get(column, ""))
        except (TypeError, ValueError):
            game[column] = None
    for column in BOOL_COLUMNS:
        game[column] = str(game.get(column, "")).lower() == "true"
    return game


if __name__ == "__main__":
    print("{} games -> {}".format(build(), INDEX_JSON))
