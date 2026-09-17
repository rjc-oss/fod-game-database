"""data/games.csv -> docs/index.json, the file the search site loads.

The csv is the canonical table; index.json is just the same rows as JSON, newest first, with the numbers
and flags as real JSON types so the site doesn't have to parse strings. Run by process_incoming.py after
every batch, and by hand with: python tools/build_index.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fodsave import BOOL_COLUMNS, INT_COLUMNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAMES_CSV = ROOT / "data" / "games.csv"
INDEX_JSON = ROOT / "docs" / "index.json"


def read_games(path=GAMES_CSV):
    """Every row of games.csv as a dict, in file order, with numbers and flags typed."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [_typed(row) for row in csv.DictReader(handle)]


def build(games_csv=GAMES_CSV, index_json=INDEX_JSON):
    """Writes index.json and returns how many games it holds."""
    games = read_games(games_csv)
    # Newest first; ids start with the receipt time, so they sort by time.
    games.sort(key=lambda game: game.get("id", ""), reverse=True)
    index = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(games),
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
