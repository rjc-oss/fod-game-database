"""The leaderboard: every player's Dracula and Hunter rating, worked out from games.csv with Glicko-2.

Pure: no files, no clock. `rate(games)` replays the rated games in the order the server received them and
returns the ratings, and what each game did to them. build_index.py puts both in index.json; the site
grows each deviation up to today (the same rule as `deviation_after`, written out again in docs/app.js).

The rules:
- Rated games are 1 v 1 online games with no computer seats: one person holds all four hunter seats.
  Local games never count, because nothing says who sat in the seats.
- A player is a Steam ID (as the key fodsave.player_id makes of it) plus the name they played under, and
  is shown by that name. A new name starts a new player.
- Everyone has a separate Dracula and Hunter rating. Each is a Glicko-2 rating (Glickman,
  http://www.glicko.net/glicko/glicko2.pdf) with lichess's settings: a rating, a deviation (how unsure
  the rating is) and a volatility. A new rating is 1500, deviation 500, volatility 0.09.
- Every game is its own rating period, as on lichess. Between a side's games its deviation grows with
  the time since its last game (0.21436 rating periods a day), so a rating nobody has played for a while
  becomes unsure again and moves faster when its player comes back. Deviation stays between 45 and 500,
  volatility at most 0.1, and the volatility's step uses tau = 0.75.
- A rating whose deviation is above 110 is provisional (shown with a ?) and isn't ranked: newcomers until
  they have played enough, and players who have been away long enough. Playing a provisional opponent
  moves a settled rating very little, so there's no risk in playing newcomers.
"""

from __future__ import annotations

import math
from datetime import datetime

SCALE = 173.7178                 # Glicko-2's conversion between its own units and rating points
START = 1500.0
START_DEVIATION = 500.0
START_VOLATILITY = 0.09
MIN_DEVIATION = 45.0
MAX_DEVIATION = 500.0
MAX_VOLATILITY = 0.1
TAU = 0.75
PERIODS_PER_DAY = 0.21436
PROVISIONAL_DEVIATION = 110.0    # above this a rating is provisional and not ranked
CONVERGENCE = 0.000001
SIDES = ("dracula", "hunter")
NAME_JOIN = " & "
TAG_LENGTH = 4                   # characters of the key shown after a name two players share


def params():
    """The rules as the site needs them: it grows the deviations up to today, and explains the board."""
    return {
        "start": START, "start_deviation": START_DEVIATION, "max_deviation": MAX_DEVIATION,
        "provisional_deviation": PROVISIONAL_DEVIATION, "periods_per_day": PERIODS_PER_DAY,
        "scale": SCALE,
    }


def is_rated(game):
    """Whether a games.csv row counts: online, no computer, one hunter player, everyone identified."""
    hunter_ids = [key for key in str(game.get("hunter_player_ids") or "").split(NAME_JOIN) if key]
    hunters = [name for name in str(game.get("hunters") or "").split(NAME_JOIN) if name]
    return (game.get("game_type") == "Network" and not _flag(game.get("has_ai"))
            and len(hunter_ids) == 1 and len(hunters) == 1 and bool(game.get("dracula_player_id"))
            and game.get("winner_side") in ("Hunters", "Dracula")
            and _when(game.get("received_at_utc")) is not None)


def deviation_after(deviation, volatility, days):
    """A deviation after `days` without a game on that side."""
    phi = deviation / SCALE
    grown = math.sqrt(phi * phi + volatility * volatility * days * PERIODS_PER_DAY) * SCALE
    return min(MAX_DEVIATION, grown)


def update(own, results, periods):
    """Glicko-2's update of `own` (rating, deviation, volatility) over one rating period.

    `results` is a list of (opponent's (rating, deviation, volatility), score), score 1 for a win and 0
    for a loss. `periods` is how many rating periods the deviation grows by: 1 in Glickman's paper; here
    the time since own's last game on that side, as lichess does, so 0 for a first game.
    """
    rating, deviation, volatility = own
    mu, phi = (rating - START) / SCALE, deviation / SCALE
    v_inverse = 0.0
    improvement = 0.0
    for opponent, score in results:
        mu_j, phi_j = (opponent[0] - START) / SCALE, opponent[1] / SCALE
        g = 1.0 / math.sqrt(1.0 + 3.0 * phi_j * phi_j / (math.pi * math.pi))
        e = 1.0 / (1.0 + math.exp(-g * (mu - mu_j)))
        v_inverse += g * g * e * (1.0 - e)
        improvement += g * (score - e)
    v = 1.0 / v_inverse
    delta = v * improvement

    new_volatility = min(MAX_VOLATILITY, _volatility(delta, phi, v, volatility))
    phi_star = math.sqrt(phi * phi + new_volatility * new_volatility * periods)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * improvement
    new_deviation = min(MAX_DEVIATION, max(MIN_DEVIATION, new_phi * SCALE))
    return (START + new_mu * SCALE, new_deviation, new_volatility)


def _volatility(delta, phi, v, volatility):
    """Step 5 of Glickman's paper: the new volatility, by the Illinois algorithm."""
    a = math.log(volatility * volatility)

    def f(x):
        ex = math.exp(x)
        return (ex * (delta * delta - phi * phi - v - ex) / (2.0 * (phi * phi + v + ex) ** 2)
                - (x - a) / (TAU * TAU))

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        big_b = a - k * TAU
    f_a, f_b = f(big_a), f(big_b)
    while abs(big_b - big_a) > CONVERGENCE:
        big_c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
        f_c = f(big_c)
        if f_c * f_b <= 0:
            big_a, f_a = big_b, f_b
        else:
            f_a /= 2.0
        big_b, f_b = big_c, f_c
    return math.exp(big_a / 2.0)


class Player:
    def __init__(self, key, name):
        self.key = key
        self.name = name
        self.sides = {side: {"rating": START, "deviation": START_DEVIATION, "volatility": START_VOLATILITY,
                             "games": 0, "wins": 0, "losses": 0, "last_played": None}
                      for side in SIDES}


def rate(games):
    """Ratings from games.csv rows (any order).

    Returns (board, changes): board is {"dracula": [...], "hunter": [...]}, each the players with at
    least one game on that side, as of their last game on it, highest rating first; changes maps a rated
    game's id to {"dracula": [before, after], "hunter": [before, after]}.
    """
    rated = sorted((game for game in games if is_rated(game)),
                   key=lambda game: (_when(game["received_at_utc"]), str(game.get("id", ""))))
    players = {}
    changes = {}
    for game in rated:
        when = _when(game["received_at_utc"])
        d = _player(players, game["dracula_player_id"], game["dracula"]).sides["dracula"]
        h = _player(players, game["hunter_player_ids"], game["hunters"]).sides["hunter"]
        dracula_score = 1.0 if game["winner_side"] == "Dracula" else 0.0
        d_after = update(_state(d), [(_state(h), dracula_score)], _periods(d, when))
        h_after = update(_state(h), [(_state(d), 1.0 - dracula_score)], _periods(h, when))
        changes[game.get("id", "")] = {"dracula": [_round(d["rating"]), _round(d_after[0])],
                                       "hunter": [_round(h["rating"]), _round(h_after[0])]}
        for side, after, won in ((d, d_after, dracula_score == 1.0), (h, h_after, dracula_score == 0.0)):
            side["rating"], side["deviation"], side["volatility"] = after
            side["games"] += 1
            side["wins" if won else "losses"] += 1
            side["last_played"] = when

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
                "rating": _round(side["rating"]), "deviation": _round(side["deviation"]),
                "volatility": round(side["volatility"], 6), "games": side["games"],
                "wins": side["wins"], "losses": side["losses"],
                "last_played": side["last_played"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        entries.sort(key=lambda entry: (-entry["rating"], entry["shown"].lower()))
        board[side_name] = entries
    return board, changes


def _state(side):
    return (side["rating"], side["deviation"], side["volatility"])


def _periods(side, when):
    """The rating periods since a side's last game: lichess counts time, not games."""
    if side["last_played"] is None:
        return 0.0
    return max(0.0, (when - side["last_played"]).total_seconds() / 86400.0) * PERIODS_PER_DAY


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
