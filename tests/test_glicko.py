"""The leaderboard: which games count, Glicko-2's updates, and deviations growing between games.

    python -m pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import glicko  # noqa: E402

ANNA, BEN, CARL = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"
NEW = (glicko.START, glicko.START_DEVIATION, glicko.START_VOLATILITY)


def game(hunter, dracula, winner, when="2026-10-01T12:00:00.000Z", upload="", **extra):
    """A games.csv row for a rated game: (key, name) for each side."""
    row = {
        "id": upload or when, "received_at_utc": when, "database": "casual", "game_type": "Network",
        "has_ai": False, "hunters": hunter[1], "hunter_player_ids": hunter[0],
        "dracula": dracula[1], "dracula_player_id": dracula[0], "winner_side": winner,
    }
    row.update(extra)
    return row


def entry(board, side, name):
    return next(entry for entry in board[side] if entry["name"] == name)


def test_glickmans_worked_example():
    # The example in http://www.glicko.net/glicko/glicko2.pdf: one rating period, three games.
    rating, deviation, volatility = glicko.update(
        (1500, 200, 0.06), [((1400, 30, 0.06), 1), ((1550, 100, 0.06), 0), ((1700, 300, 0.06), 0)], 1)
    assert rating == pytest.approx(1464.06, abs=0.01)
    assert deviation == pytest.approx(151.52, abs=0.01)
    assert volatility == pytest.approx(0.05999, abs=0.00001)


def test_two_newcomers_move_a_lot_and_become_surer():
    board, changes = glicko.rate([game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", upload="g1")])
    ben, anna = entry(board, "dracula", "Ben"), entry(board, "hunter", "Anna")
    assert ben["rating"] > 1700 and anna["rating"] < 1300
    assert ben["rating"] - 1500 == pytest.approx(1500 - anna["rating"], abs=0.01)
    assert ben["deviation"] < glicko.START_DEVIATION
    assert changes["g1"]["dracula"][0] == 1500 and changes["g1"]["hunter"][0] == 1500
    # Each player only appears on the side they have played.
    assert [e["name"] for e in board["dracula"]] == ["Ben"]
    assert [e["name"] for e in board["hunter"]] == ["Anna"]


def test_losing_to_a_newcomer_costs_a_settled_player_little():
    settled = (1700, 60, 0.06)
    after = glicko.update(settled, [(NEW, 0)], 0)
    assert 1690 < after[0] < 1700
    newcomer = glicko.update(NEW, [(settled, 1)], 0)
    assert newcomer[0] > 1900


def test_the_favourite_gains_less():
    favourite = glicko.update((1700, 60, 0.06), [((1500, 60, 0.06), 1)], 0)
    even = glicko.update((1500, 60, 0.06), [((1500, 60, 0.06), 1)], 0)
    assert 0 < favourite[0] - 1700 < even[0] - 1500


def test_the_two_ratings_are_separate():
    board, _ = glicko.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Hunters", when="2026-10-01T12:00:00Z"),
        game((BEN, "Ben"), (ANNA, "Anna"), "Hunters", when="2026-10-02T12:00:00Z"),
    ])
    assert entry(board, "hunter", "Anna")["rating"] > 1500 > entry(board, "dracula", "Anna")["rating"]
    assert entry(board, "hunter", "Ben")["rating"] > 1500 > entry(board, "dracula", "Ben")["rating"]
    assert all(e["games"] == 1 for side in board.values() for e in side)


def test_two_newcomers_settle_after_sixteen_games_a_day_apart():
    games = [game((ANNA, "Anna"), (BEN, "Ben"), "Dracula" if i % 2 else "Hunters",
                  when="2026-10-{:02d}T12:00:00Z".format(i + 1)) for i in range(16)]
    board, _ = glicko.rate(games)
    assert entry(board, "dracula", "Ben")["deviation"] < glicko.PROVISIONAL_DEVIATION
    board, _ = glicko.rate(games[:15])
    assert entry(board, "dracula", "Ben")["deviation"] > glicko.PROVISIONAL_DEVIATION


@pytest.mark.parametrize("change", [
    {"game_type": "Local"},
    {"has_ai": True},
    {"has_ai": "true"},
    {"hunters": "Anna & Carl", "hunter_player_ids": ANNA + " & " + CARL},
    {"hunter_player_ids": ""},
    {"dracula_player_id": ""},
    {"received_at_utc": "not a date"},
])
def test_only_online_one_v_one_games_without_ai_count(change):
    row = game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", **change)
    assert not glicko.is_rated(row)
    assert glicko.rate([row]) == ({"dracula": [], "hunter": []}, {})


def test_games_are_rated_in_the_order_they_were_received():
    late = game((ANNA, "Anna"), (BEN, "Ben"), "Hunters", when="2026-10-02T12:00:00Z")
    early = game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:00:00Z")
    _, changes = glicko.rate([late, early])
    assert changes[early["id"]]["dracula"][0] == 1500
    assert changes[late["id"]]["dracula"][0] == changes[early["id"]]["dracula"][1]


def test_a_new_name_is_a_new_player_and_a_shared_name_is_tagged():
    board, _ = glicko.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:00:00Z"),
        game((ANNA, "Anna2"), (BEN, "Ben"), "Dracula", when="2026-10-02T12:00:00Z"),
        game((CARL, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-03T12:00:00Z"),
    ])
    shown = sorted(e["shown"] for e in board["hunter"])
    assert shown == ["Anna #aaaa", "Anna #cccc", "Anna2"]
    assert all(e["games"] == 1 for e in board["hunter"])


def test_a_deviation_grows_while_a_side_is_not_played():
    assert glicko.deviation_after(60, 0.06, 0) == pytest.approx(60)
    assert 60 < glicko.deviation_after(60, 0.06, 30) < glicko.deviation_after(60, 0.06, 365)
    assert glicko.deviation_after(400, 0.1, 100000) == glicko.MAX_DEVIATION
    # A settled rating left alone for a year or so is provisional again.
    assert glicko.deviation_after(60, 0.09, 365) > glicko.PROVISIONAL_DEVIATION


def test_time_away_makes_the_next_game_count_for_more():
    settled = (1600, 60, 0.06)
    soon = glicko.update(settled, [((1600, 60, 0.06), 1)], 0)
    later = glicko.update(settled, [((1600, 60, 0.06), 1)], 365 * glicko.PERIODS_PER_DAY)
    assert later[0] - 1600 > soon[0] - 1600 > 0


def test_the_rules_the_site_needs():
    p = glicko.params()
    assert (p["start"], p["start_deviation"], p["max_deviation"]) == (1500, 500, 500)
    assert (p["provisional_deviation"], p["periods_per_day"], p["scale"]) == (110, 0.21436, 173.7178)
