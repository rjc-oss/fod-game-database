"""The validator, the naming and the score, against a real finished game and hand-broken copies of it.

    python -m pytest

The fixture is a real .fod written by the mod's "Save game locally" button at the end of a game
(no thumbnail, action log ending in a GameOverMarker), so it is exactly what an upload carries.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fodsave  # noqa: E402
from fodsave import Reject, file_name, file_token, score, validate  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "finished-game.fod"

META = {
    "schema": 1,
    "modVersion": "0.50.0",
    "configCode": "0C818D",
    "hunterVictory": True,
    "influence": 0,
    "draculaHealth": 0,
    "draculaMaxHealth": 15,
    "daysCompleted": 1,
    "endedAtUtc": "2026-09-17T21:08:48Z",
    "gameType": 0,
    "saveVersion": 40,
    "actionCount": 23,
}


@pytest.fixture
def save():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def envelope(meta=None, database="casual"):
    return {
        "id": "20260917T213744Z-da9f9ee5",
        "receivedAt": "2026-09-17T21:37:44.000Z",
        "database": database,
        "client": "DraculaMod/0.50.0",
        "meta": copy.deepcopy(meta if meta is not None else META),
    }


def body(save):
    return json.dumps(save).encode("utf-8")


# --- the good case -------------------------------------------------------------------------------

def test_accepts_a_finished_game(save):
    record = validate(body(save), envelope())

    assert record.identifier == save["Identifier"]
    assert record.hunters == "RToWin"                      # four seats, one person: named once
    assert record.dracula == "Player 2"
    assert record.winner_side == "Hunters"
    assert record.winner_players == "RToWin"
    assert record.loser_players == "Player 2"
    assert (record.influence, record.dracula_health, record.dracula_max_health) == (0, 0, 15)
    assert record.days_completed == 1
    assert record.score == -15
    assert record.game_type == "Local"
    assert record.has_ai is False
    assert (record.save_version, record.action_count) == (40, 23)
    assert record.database == "casual"
    assert len(record.log_sha256) == 64
    assert record.row()[fodsave.COLUMNS.index("has_ai")] == "false"


def test_the_hash_ignores_the_resulting_hashes(save):
    other = copy.deepcopy(save)
    for entry in other["ActionLog"]["_actions"]["$values"]:
        entry["ResultingHash"] = 12345
    assert validate(body(other), envelope()).log_sha256 == validate(body(save), envelope()).log_sha256


def test_several_hunters_are_listed_in_seat_order(save):
    for seat, name in enumerate(["Bob", "Alice", "Bob", "Carol"]):
        save["Config"]["Players"]["$values"][seat]["PlayerName"] = name
    record = validate(body(save), envelope())
    assert record.hunters == "Bob & Alice & Carol"
    assert record.winner_players == "Bob & Alice & Carol"


def test_an_ai_player_is_flagged(save):
    save["Config"]["Players"]["$values"][4]["Type"] = 1
    assert validate(body(save), envelope()).has_ai is True


# --- the setup the game was played with ----------------------------------------------------------

def test_reads_the_rules_out_of_the_save(save):
    record = validate(body(save), envelope())

    assert record.advanced_rules == "Power cards: all; Free placement; Rumour tokens; Lairs"
    assert record.house_rules == "Dark Call draws an event card; Trail slides before Dracula moves"
    # The mod's own house rules ride in HouseRules._powerCardsPlayLimits, highest bit first.
    assert record.mod_house_rules == "Dracula starting damage 10; Feed healing 6; Dark Call damage 5"


def test_a_standard_game_has_nothing_to_report(save):
    rules = save["Config"]["HouseRules"]
    rules["DarkCallDrawEventCard"] = False
    rules["SlideTrailBeforeMovingDracula"] = False
    rules["_powerCardsPlayLimits"] = {"$type": rules["_powerCardsPlayLimits"]["$type"]}
    save["Config"]["AdvancedRules"].update(PowerCardsConfig=0, FreePlacement=True,
                                           RumourTokens=False, Lairs=False)
    record = validate(body(save), envelope())

    assert record.advanced_rules == "Free placement"
    assert record.house_rules == ""
    assert record.mod_house_rules == ""


def test_reads_the_power_cards_that_were_in_play(save):
    save["Config"]["AdvancedRules"]["PowerCardsConfig"] = 1 | 8        # Hide and Dark Call
    assert fodsave.advanced_rules(save["Config"]).startswith("Power cards: Hide, Dark Call;")


def test_a_slider_set_to_zero_reads_as_zero(save):
    # A stored 0 would make the game call the whole set standard, so the mod stores 0 as -1.
    save["Config"]["HouseRules"]["_powerCardsPlayLimits"][str(1 << 17)] = fodsave.STORED_ZERO
    assert "Feed healing 0" in fodsave.mod_house_rules(save["Config"])


def test_a_mod_rule_this_database_has_not_heard_of_is_still_reported(save):
    save["Config"]["HouseRules"]["_powerCardsPlayLimits"][str(1999)] = 4
    assert "Mod rule 1999 4" in fodsave.mod_house_rules(save["Config"])


def test_a_vanilla_play_limit_is_a_house_rule_not_a_mod_rule(save):
    save["Config"]["HouseRules"]["_powerCardsPlayLimits"]["2"] = 3     # Wolf Form, three plays
    assert "Wolf Form limit 3" in fodsave.house_rules(save["Config"])
    assert "Wolf Form" not in fodsave.mod_house_rules(save["Config"])


def test_a_save_without_rules_says_so(save):
    del save["Config"]["HouseRules"]
    del save["Config"]["AdvancedRules"]
    record = validate(body(save), envelope())
    assert (record.advanced_rules, record.house_rules, record.mod_house_rules) == (
        "unknown", "unknown", "unknown")


# --- broken uploads ------------------------------------------------------------------------------

def test_rejects_something_that_is_not_a_save():
    with pytest.raises(Reject, match="not a Fury of Dracula save"):
        validate(b'{"$type": "Something.Else"}', envelope())

    with pytest.raises(Reject, match="not JSON"):
        validate(b"<html>", envelope())

    with pytest.raises(Reject, match="empty"):
        validate(b"", envelope())


def test_rejects_an_unfinished_game(save):
    save["ActionLog"]["_actions"]["$values"].pop()
    save["ActionLog"]["Size"] = 22
    meta = dict(META, actionCount=22)
    with pytest.raises(Reject, match="GameOverMarker"):
        validate(body(save), envelope(meta))


def test_rejects_a_log_whose_size_is_wrong(save):
    save["ActionLog"]["Size"] = 99
    with pytest.raises(Reject, match="Size"):
        validate(body(save), envelope())


def test_rejects_a_short_log(save):
    entries = save["ActionLog"]["_actions"]["$values"]
    save["ActionLog"]["_actions"]["$values"] = entries[:3] + entries[-1:]
    save["ActionLog"]["Size"] = 4
    with pytest.raises(Reject, match="only 4 actions"):
        validate(body(save), envelope(dict(META, actionCount=4)))


def test_rejects_a_tutorial(save):
    save["Config"]["GameType"] = 2
    with pytest.raises(Reject, match="not a real game"):
        validate(body(save), envelope(dict(META, gameType=2)))


def test_rejects_a_meta_that_contradicts_the_file(save):
    with pytest.raises(Reject, match="actionCount"):
        validate(body(save), envelope(dict(META, actionCount=22)))
    with pytest.raises(Reject, match="saveVersion"):
        validate(body(save), envelope(dict(META, saveVersion=39)))
    with pytest.raises(Reject, match="gameType"):
        validate(body(save), envelope(dict(META, gameType=1)))


def test_rejects_an_impossible_result(save):
    # The hunters win by running Dracula's health out, and only then.
    with pytest.raises(Reject, match="Dracula has 4 health"):
        validate(body(save), envelope(dict(META, draculaHealth=4)))
    with pytest.raises(Reject, match="hunters won at 13"):
        validate(body(save), envelope(dict(META, influence=13)))
    # Dracula wins at 13 influence, and only then.
    with pytest.raises(Reject, match="Dracula won at 7"):
        validate(body(save), envelope(dict(META, hunterVictory=False, influence=7)))


def test_rejects_health_outside_the_house_rules(save):
    with pytest.raises(Reject, match="draculaMaxHealth is 26"):
        validate(body(save), envelope(dict(META, draculaMaxHealth=26)))
    with pytest.raises(Reject, match="draculaMaxHealth is 14"):
        validate(body(save), envelope(dict(META, draculaMaxHealth=14)))
    with pytest.raises(Reject, match="draculaHealth is 20"):
        validate(body(save), envelope(dict(META, hunterVictory=False, influence=13, draculaHealth=20)))


def test_accepts_the_additional_health_house_rule(save):
    record = validate(body(save), envelope(
        dict(META, hunterVictory=False, influence=13, draculaHealth=20, draculaMaxHealth=25,
             daysCompleted=25)))
    assert record.winner_side == "Dracula"
    # 13 influence, 5 damage dealt, and the hunters lasted 21 days or more.
    assert record.score == 13 - 5 + 2


def test_rejects_a_bad_meta_header(save):
    with pytest.raises(Reject, match="schema"):
        validate(body(save), envelope(dict(META, schema=2)))
    with pytest.raises(Reject, match="modVersion"):
        validate(body(save), envelope(dict(META, modVersion="0.50")))
    with pytest.raises(Reject, match="configCode"):
        validate(body(save), envelope(dict(META, configCode="abcdef")))
    with pytest.raises(Reject, match="hunterVictory"):
        validate(body(save), envelope(dict(META, hunterVictory="yes")))


def test_rejects_a_bad_player_list(save):
    save["Config"]["Players"]["$values"].pop()
    with pytest.raises(Reject, match="4 players"):
        validate(body(save), envelope())


def test_rejects_an_unknown_database(save):
    with pytest.raises(Reject, match="database anything"):
        validate(body(save), envelope(database="anything"))


# --- naming and score ----------------------------------------------------------------------------

@pytest.mark.parametrize("name,token", [
    ("RToWin", "Rtowin"),
    ("scotSWORD", "Scotsword"),
    ("Dr. John Seward", "Drjohnseward"),
    ("", "Player"),
    ("   ", "Player"),
    ("Ünïcödé123", "Ncd123"),
    ("abcdefghijklmnopqrstuvwxyz", "Abcdefghijklmnopqrst"),
])
def test_file_token(name, token):
    assert file_token(name) == token


def test_file_name():
    when = datetime(2026, 9, 17, 20, 11, 5)
    assert file_name(["RToWin"], "scotSWORD", when) == "Rtowin_Vs_Scotsword_2026-09-17_20-11-05.fod"
    assert file_name(["Alice", "Bob"], "Carol", when) == "Alice_Bob_Vs_Carol_2026-09-17_20-11-05.fod"
    assert file_name([], "", when) == "Player_Vs_Player_2026-09-17_20-11-05.fod"


@pytest.mark.parametrize("influence,health,maximum,days,expected", [
    (13, 7, 15, 10, 5),        # Dracula won with 8 damage taken
    (13, 7, 15, 21, 7),        # the same game, but the hunters held out for three weeks
    (0, 0, 15, 23, -13),       # hunters won without Dracula scoring
    (13, 20, 25, 5, 8),        # additional health: the damage is measured against this game's maximum
])
def test_score(influence, health, maximum, days, expected):
    assert score(influence, health, maximum, days) == expected
