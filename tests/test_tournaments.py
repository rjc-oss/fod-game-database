"""The tournament list: what data/tournaments.csv may say, and which tournament a game falls in.

    python -m pytest
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_index  # noqa: E402
import tournaments  # noqa: E402
from tournaments import TournamentError, assign, load  # noqa: E402

HEADER = "name,start_utc,end_utc,format\n"


def write(tmp_path, *rows):
    path = tmp_path / "tournaments.csv"
    path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def game(received, database="tournament"):
    return {"database": database, "received_at_utc": received}


def test_the_repositorys_own_list_is_valid():
    load()          # raises if data/tournaments.csv breaks the rules


def test_no_file_means_no_tournaments(tmp_path):
    assert load(tmp_path / "missing.csv") == []


def test_a_game_falls_in_the_tournament_it_was_received_during(tmp_path):
    listed = load(write(tmp_path,
                        "Spring Cup,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss",
                        "Autumn League,2026-09-01T18:00:00Z,,Round robin"))
    assert assign(game("2026-03-15T12:00:00.000Z"), listed) == "Spring Cup"
    assert assign(game("2026-03-01T00:00:00.000Z"), listed) == "Spring Cup"      # the start is in
    assert assign(game("2026-04-01T00:00:00.000Z"), listed) == ""                # the end is not
    assert assign(game("2026-09-01T17:59:59.999Z"), listed) == ""
    assert assign(game("2030-01-01T00:00:00.000Z"), listed) == "Autumn League"   # ongoing
    assert assign(game("2026-02-01T00:00:00.000Z"), listed) == ""


def test_casual_games_are_never_in_a_tournament(tmp_path):
    listed = load(write(tmp_path, "Cup,2026-03-01T00:00:00Z,,Swiss"))
    assert assign(game("2026-03-15T12:00:00.000Z", database="casual"), listed) == ""


def test_a_bare_end_date_includes_that_whole_day(tmp_path):
    listed = load(write(tmp_path, "Cup,2026-03-01,2026-03-31,Swiss"))
    assert assign(game("2026-03-01T00:00:00.000Z"), listed) == "Cup"
    assert assign(game("2026-03-31T23:59:59.000Z"), listed) == "Cup"
    assert assign(game("2026-04-01T00:00:00.000Z"), listed) == ""


def test_a_time_with_another_offset_is_converted_to_utc(tmp_path):
    listed = load(write(tmp_path, "Cup,2026-03-01T01:00:00+01:00,2026-03-02T00:00:00Z,Swiss"))
    assert assign(game("2026-03-01T00:00:00.000Z"), listed) == "Cup"


def test_rows_may_come_in_any_order(tmp_path):
    listed = load(write(tmp_path,
                        "Second,2026-05-01T00:00:00Z,,Swiss",
                        "First,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss"))
    assert [tournament.name for tournament in listed] == ["First", "Second"]


@pytest.mark.parametrize("rows, complaint", [
    (["A,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss",
      "B,2026-03-15T00:00:00Z,2026-05-01T00:00:00Z,Swiss"], "overlap"),
    (["A,2026-03-01T00:00:00Z,,Swiss", "B,2026-05-01T00:00:00Z,,Swiss"], "no end"),
    (["A,2026-03-01T00:00:00Z,2026-02-01T00:00:00Z,Swiss"], "ends before it starts"),
    (["A,1st March,,Swiss"], "not a date"),
    (["A,2026-03-01T00:00:00Z,soon,Swiss"], "not a date"),
    ([",2026-03-01T00:00:00Z,,Swiss"], "no name"),
    (["A,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss", "A,2026-05-01T00:00:00Z,,Swiss"], "two tournaments"),
])
def test_a_list_that_breaks_the_rules_is_refused(tmp_path, rows, complaint):
    with pytest.raises(TournamentError, match=complaint):
        load(write(tmp_path, *rows))


def test_back_to_back_tournaments_are_fine(tmp_path):
    listed = load(write(tmp_path,
                        "A,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss",
                        "B,2026-04-01T00:00:00Z,,Swiss"))
    assert assign(game("2026-04-01T00:00:00.000Z"), listed) == "B"


def test_the_index_carries_each_games_tournament_and_the_list(tmp_path):
    games_csv = tmp_path / "games.csv"
    games_csv.write_text("id,received_at_utc,database\n"
                         "20260315T120000Z-aaaaaaaa,2026-03-15T12:00:00.000Z,tournament\n"
                         "20260316T120000Z-bbbbbbbb,2026-03-16T12:00:00.000Z,casual\n", encoding="utf-8")
    listed = write(tmp_path,
                   "Spring Cup,2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Swiss",
                   "Autumn League,2026-09-01T00:00:00Z,,Round robin")
    index_json = tmp_path / "index.json"

    build_index.build(games_csv, index_json, listed)

    index = json.loads(index_json.read_text(encoding="utf-8"))
    assert [t["name"] for t in index["tournaments"]] == ["Autumn League", "Spring Cup"]     # newest first
    assert index["tournaments"][0] == {"name": "Autumn League", "start_utc": "2026-09-01T00:00:00Z",
                                       "end_utc": "", "format": "Round robin"}
    by_id = {g["id"]: g["tournament"] for g in index["games"]}
    assert by_id == {"20260315T120000Z-aaaaaaaa": "Spring Cup", "20260316T120000Z-bbbbbbbb": ""}


def test_a_broken_list_never_stops_the_index_being_built(tmp_path, capsys):
    games_csv = tmp_path / "games.csv"
    games_csv.write_text("id,received_at_utc,database\n"
                         "20260315T120000Z-aaaaaaaa,2026-03-15T12:00:00.000Z,tournament\n", encoding="utf-8")
    listed = write(tmp_path, "A,2026-03-01T00:00:00Z,,Swiss", "B,2026-05-01T00:00:00Z,,Swiss")
    index_json = tmp_path / "index.json"

    assert build_index.build(games_csv, index_json, listed) == 1

    index = json.loads(index_json.read_text(encoding="utf-8"))
    assert index["tournaments"] == [] and index["games"][0]["tournament"] == ""
    assert "tournaments.csv ignored" in capsys.readouterr().err
