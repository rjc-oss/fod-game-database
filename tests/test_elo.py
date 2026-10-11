"""The Elo leaderboard: which games count, how ratings move, and the inactivity penalty.

    python -m pytest
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import elo  # noqa: E402

ANNA, BEN, CARL = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"


def game(hunter, dracula, winner, when="2026-10-01T12:00:00.000Z", upload="", **extra):
    """A games.csv row for a rated game: (key, name) for each side."""
    row = {
        "id": upload or when, "received_at_utc": when, "database": "casual", "game_type": "Network",
        "has_ai": False, "hunters": hunter[1], "hunter_player_ids": hunter[0],
        "dracula": dracula[1], "dracula_player_id": dracula[0], "winner_side": winner,
    }
    row.update(extra)
    return row


def rating(board, side, name):
    return next(entry["rating"] for entry in board[side] if entry["name"] == name)


def test_an_even_game_moves_both_by_half_k():
    board, changes = elo.rate([game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", upload="g1")])
    assert rating(board, "dracula", "Ben") == 1520
    assert rating(board, "hunter", "Anna") == 1480
    assert changes == {"g1": {"dracula": [1500, 1520], "hunter": [1500, 1480]}}
    # Each player only appears on the side they have played.
    assert [entry["name"] for entry in board["dracula"]] == ["Ben"]
    assert [entry["name"] for entry in board["hunter"]] == ["Anna"]


def test_the_two_ratings_are_separate():
    board, _ = elo.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Hunters", when="2026-10-01T12:00:00Z"),
        game((BEN, "Ben"), (ANNA, "Anna"), "Hunters", when="2026-10-02T12:00:00Z"),
    ])
    assert rating(board, "hunter", "Anna") == 1520
    assert rating(board, "dracula", "Anna") == 1480
    assert rating(board, "dracula", "Ben") == 1480
    assert rating(board, "hunter", "Ben") == 1520


def test_a_tournament_game_has_k_ten_higher():
    board, _ = elo.rate([game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", database="tournament")])
    assert rating(board, "dracula", "Ben") == 1525


def test_k_drops_after_ten_games_on_a_side():
    assert elo.k_factor(9, False) == 40
    assert elo.k_factor(10, False) == 20
    assert elo.k_factor(10, True) == 30
    games = [game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:{:02d}:00Z".format(i))
             for i in range(11)]
    _, changes = elo.rate(games)
    before, after = changes["2026-10-01T12:10:00Z"]["dracula"]
    assert 0 < after - before < 20 * 0.5          # K 20, and Ben is now the favourite


def test_the_favourite_gains_less():
    assert elo.expected(1600, 1400) == pytest.approx(1 / (1 + 10 ** -0.5))
    board, _ = elo.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:00:00Z"),
        game((CARL, "Carl"), (BEN, "Ben"), "Dracula", when="2026-10-02T12:00:00Z"),
    ])
    assert 1520 < rating(board, "dracula", "Ben") < 1540
    assert 1480 < rating(board, "hunter", "Carl") < 1500


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
    assert not elo.is_rated(row)
    assert elo.rate([row]) == ({"dracula": [], "hunter": []}, {})


def test_games_are_rated_in_the_order_they_were_received():
    late = game((ANNA, "Anna"), (BEN, "Ben"), "Hunters", when="2026-10-02T12:00:00Z")
    early = game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:00:00Z")
    _, changes = elo.rate([late, early])
    assert changes[early["id"]]["dracula"][0] == 1500
    assert changes[late["id"]]["dracula"][0] == 1520


def test_a_new_name_is_a_new_player_and_a_shared_name_is_tagged():
    board, _ = elo.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-01T12:00:00Z"),
        game((ANNA, "Anna2"), (BEN, "Ben"), "Dracula", when="2026-10-02T12:00:00Z"),
        game((CARL, "Anna"), (BEN, "Ben"), "Dracula", when="2026-10-03T12:00:00Z"),
    ])
    shown = sorted(entry["shown"] for entry in board["hunter"])
    assert shown == ["Anna #aaaa", "Anna #cccc", "Anna2"]
    assert all(entry["games"] == 1 for entry in board["hunter"])


@pytest.mark.parametrize("rating, days, expected", [
    (1600, 30, 1600),       # inside the grace period
    (1600, 36.9, 1600),     # less than a week past it
    (1600, 37, 1595),
    (1600, 51, 1585),
    (1510, 100, 1500),      # never below the start
    (1500, 400, 1500),
    (1450, 400, 1450),      # a rating below the start is left alone
])
def test_the_inactivity_penalty(rating, days, expected):
    assert elo.decayed(rating, days) == expected


def test_the_penalty_is_applied_before_a_returning_players_next_game():
    _, changes = elo.rate([
        game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-01-01T12:00:00Z"),
        # Ben plays as a hunter after 44 days: two weeks past the grace period, on both ratings.
        game((BEN, "Ben"), (CARL, "Carl"), "Dracula", when="2026-02-14T12:00:00Z"),
        game((ANNA, "Anna"), (BEN, "Ben"), "Dracula", when="2026-02-15T12:00:00Z"),
    ])
    assert changes["2026-02-15T12:00:00Z"]["dracula"][0] == 1510


def test_the_rules_the_site_needs():
    p = elo.params()
    assert (p["start"], p["k_new"], p["k_settled"], p["k_tournament_bonus"]) == (1500, 40, 20, 10)
    assert (p["new_games"], p["grace_days"], p["decay_points"], p["decay_days"]) == (10, 30, 5, 7)
