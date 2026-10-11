"""The Elo leaderboard: every player's Dracula and Hunter rating, worked out from games.csv.

Pure: no files, no clock. `rate(games)` replays the rated games in the order the server received them and
returns the ratings, and what each game did to them. build_index.py puts both in index.json; the site
applies the inactivity penalty up to today (the same rule as `decayed`, written out again in docs/app.js).

The rules:
- Rated games are 1 v 1 online games with no computer seats: one person holds all four hunter seats.
  Local games never count, because nothing says who sat in the seats.
- A player is a Steam ID (as the key fodsave.player_id makes of it) plus the name they played under, and
  is shown by that name. A new name starts a new player.
- Everyone has a separate Dracula and Hunter rating, both starting at 1500.
- Expected score E = 1 / (1 + 10^((opponent - own) / 400)); a win scores 1, a loss 0, nothing else counts.
  The rating moves by K x (score - E), K = 40 for a player's first 10 rated games on that side and 20
  after, both 10 higher for a tournament game.
- After 30 days without a rated game (on either side), each rating above 1500 loses 5 a week, never
  going below 1500.
"""

from __future__ import annotations

import math
from datetime import datetime

START = 1500.0
SCALE = 400.0
K_NEW = 40.0
K_SETTLED = 20.0
K_TOURNAMENT_BONUS = 10.0
NEW_GAMES = 10                   # games on a side played with K_NEW; the board marks them provisional
GRACE_DAYS = 30
DECAY_POINTS = 5.0
DECAY_DAYS = 7
SIDES = ("dracula", "hunter")
NAME_JOIN = " & "
TAG_LENGTH = 4                   # characters of the key shown after a name two players share


def params():
    """The rules as the site needs them: it applies the penalty, and explains the board."""
    return {
        "start": START, "k_new": K_NEW, "k_settled": K_SETTLED,
        "k_tournament_bonus": K_TOURNAMENT_BONUS, "new_games": NEW_GAMES,
        "grace_days": GRACE_DAYS, "decay_points": DECAY_POINTS, "decay_days": DECAY_DAYS,
    }


def is_rated(game):
    """Whether a games.csv row counts: online, no computer, one hunter player, everyone identified."""
    hunter_ids = [key for key in str(game.get("hunter_player_ids") or "").split(NAME_JOIN) if key]
    hunters = [name for name in str(game.get("hunters") or "").split(NAME_JOIN) if name]
    return (game.get("game_type") == "Network" and not _flag(game.get("has_ai"))
            and len(hunter_ids) == 1 and len(hunters) == 1 and bool(game.get("dracula_player_id"))
            and game.get("winner_side") in ("Hunters", "Dracula")
            and _when(game.get("received_at_utc")) is not None)


def expected(own, opponent):
    return 1.0 / (1.0 + 10.0 ** ((opponent - own) / SCALE))


def k_factor(games_before, tournament):
    return ((K_NEW if games_before < NEW_GAMES else K_SETTLED)
            + (K_TOURNAMENT_BONUS if tournament else 0.0))


def decayed(rating, days_idle):
    """A rating after days_idle days without a rated game."""
    if rating <= START or days_idle <= GRACE_DAYS:
        return rating
    weeks = math.floor((days_idle - GRACE_DAYS) / DECAY_DAYS)
    return max(START, rating - DECAY_POINTS * weeks)


class Player:
    def __init__(self, key, name):
        self.key = key
        self.name = name
        self.last_played = None          # datetime of the last rated game, either side
        self.sides = {side: {"rating": START, "games": 0, "wins": 0, "losses": 0} for side in SIDES}

    def catch_up(self, when):
        """The inactivity penalty from the last rated game up to `when`, on both ratings."""
        if self.last_played is not None:
            idle = (when - self.last_played).total_seconds() / 86400.0
            for side in self.sides.values():
                side["rating"] = decayed(side["rating"], idle)
        self.last_played = when


def rate(games):
    """Ratings from games.csv rows (any order).

    Returns (board, changes): board is {"dracula": [...], "hunter": [...]}, each the players with at
    least one game on that side, rating as of their last rated game, highest first; changes maps a rated
    game's id to {"dracula": [before, after], "hunter": [before, after]}.
    """
    rated = sorted((game for game in games if is_rated(game)),
                   key=lambda game: (_when(game["received_at_utc"]), str(game.get("id", ""))))
    players = {}
    changes = {}
    for game in rated:
        when = _when(game["received_at_utc"])
        dracula = _player(players, game["dracula_player_id"], game["dracula"])
        hunter = _player(players, game["hunter_player_ids"], game["hunters"])
        dracula.catch_up(when)
        hunter.catch_up(when)
        d, h = dracula.sides["dracula"], hunter.sides["hunter"]
        tournament = game.get("database") == "tournament"
        dracula_score = 1.0 if game["winner_side"] == "Dracula" else 0.0
        d_after = d["rating"] + k_factor(d["games"], tournament) * (
            dracula_score - expected(d["rating"], h["rating"]))
        h_after = h["rating"] + k_factor(h["games"], tournament) * (
            (1.0 - dracula_score) - expected(h["rating"], d["rating"]))
        changes[game.get("id", "")] = {"dracula": [_round(d["rating"]), _round(d_after)],
                                       "hunter": [_round(h["rating"]), _round(h_after)]}
        d["rating"], h["rating"] = d_after, h_after
        for side, won in ((d, dracula_score == 1.0), (h, dracula_score == 0.0)):
            side["games"] += 1
            side["wins" if won else "losses"] += 1

    shown = _shown_names(players.values())
    board = {}
    for side_name in SIDES:
        entries = []
        for player in players.values():
            side = player.sides[side_name]
            if not side["games"]:
                continue
            entries.append({
                "key": player.key, "name": player.name, "shown": shown[player.key],
                "rating": _round(side["rating"]), "games": side["games"],
                "wins": side["wins"], "losses": side["losses"],
                "last_played": player.last_played.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        entries.sort(key=lambda entry: (-entry["rating"], entry["shown"].lower()))
        board[side_name] = entries
    return board, changes


def _player(players, steam_key, name):
    name = name.strip()
    key = steam_key + ":" + name
    if key not in players:
        players[key] = Player(key, name)
    return players[key]


def _shown_names(players):
    """Each player's name, with a short tag from their key where two players share it."""
    counts = {}
    for player in players:
        counts[player.name.lower()] = counts.get(player.name.lower(), 0) + 1
    return {player.key: player.name if counts[player.name.lower()] == 1
            else "{} #{}".format(player.name, player.key[:TAG_LENGTH])
            for player in players}


def _when(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _flag(value):
    return value is True or str(value).lower() == "true"


def _round(value):
    return round(value, 2)
