"""The Discord announcement: what it says, where it points, and that it never fails the run.

    python -m pytest

Discord is replaced by a fake opener, so nothing here touches the network. The games it is given are
rows of data/games.csv, which is what process_incoming.py leaves in $FOD_ANNOUNCE_FILE.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import announce_discord  # noqa: E402
from fodsave import display_players  # noqa: E402

WEBHOOK = "https://discord.com/api/webhooks/1/token"

GAME = {
    "id": "20260918T005007Z-869a14da",
    "database": "casual",
    "file": "Rtowin_Vs_Scotsword_2026-09-18_00-50-07.fod",
    "hunters": "RToWin",
    "dracula": "scotSWORD",
    "advanced_rules": "Power cards: all; Lairs",
    "house_rules": "Dark Call draws an event card",
    "mod_house_rules": "Feed healing 3",
    "mod_version": "0.50.0",
    "config_code": "0C818D",
}


class FakeDiscord:
    """urlopen, as far as announce_discord uses it: the posts arrive here instead."""

    def __init__(self, fail=None):
        self.posts = []
        self.fail = fail                      # an exception to raise, once, before the first success

    def __call__(self, request, timeout=None):
        self.posts.append({
            "url": request.full_url,
            "headers": dict(request.headers),
            "body": json.loads(request.data.decode("utf-8")),
        })
        if self.fail is not None:
            failure, self.fail = self.fail, None
            raise failure
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    status = 204


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError(WEBHOOK, code, "no", {}, _Body(body))


class _Body:
    """Just enough of a file for HTTPError.read()."""

    def __init__(self, data):
        self.data = data

    def read(self, *size):
        return self.data

    def close(self):
        pass


# What the message says ----------------------------------------------------


def test_the_message_reads_as_the_announcement_should():
    words = announce_discord.message(GAME, site="https://example.test/db/")

    assert words == ("New game available, RToWin Vs scotSWORD, with house rules Feed healing 3; "
                     "Dark Call draws an event card, mod 0.50.0 config 0C818D, "
                     "https://example.test/db/?save=Rtowin_Vs_Scotsword_2026-09-18_00-50-07.fod")


def test_a_tournament_game_says_so():
    words = announce_discord.message(dict(GAME, database="tournament"), site="https://example.test/")

    assert words.startswith("New tournament game available,")


def test_a_standard_game_says_standard():
    words = announce_discord.message(dict(GAME, house_rules="", mod_house_rules=""),
                                     site="https://example.test/")

    assert "with house rules Standard," in words


def test_computer_seats_are_announced_as_ai():
    game = dict(GAME, hunters="Lord Godalming & Dr. John Seward & Van Helsing & Mina Harker",
                dracula="RToWin")

    assert "AI Vs RToWin" in announce_discord.message(game, site="https://example.test/")


def test_a_person_beside_the_computer_keeps_their_name():
    game = dict(GAME, hunters="RToWin & Van Helsing & Mina Harker")

    assert "RToWin & AI Vs" in announce_discord.message(game, site="https://example.test/")


def test_the_link_downloads_the_save_by_name_not_by_upload_id():
    # By file name, so it works in the minute before Pages has rebuilt the index the table is drawn from.
    assert announce_discord.link(GAME, site="https://example.test/db/").endswith(
        "/db/?save=Rtowin_Vs_Scotsword_2026-09-18_00-50-07.fod")


def test_names_cannot_break_the_line_in_two():
    assert "\n" not in announce_discord.message(dict(GAME, dracula="line\none"),
                                                site="https://example.test/")


def test_a_very_long_line_is_cut_to_what_discord_takes():
    game = dict(GAME, house_rules="; ".join(["A house rule"] * 400))

    assert len(announce_discord.message(game, site="https://example.test/")) <= 2000


# Posting ------------------------------------------------------------------


def test_posting_sends_the_line_and_forbids_mentions():
    discord = FakeDiscord()

    assert announce_discord.send(WEBHOOK, "New game available", opener=discord) is True

    assert discord.posts[0]["url"] == WEBHOOK
    assert discord.posts[0]["body"]["content"] == "New game available"
    # Player names come from their own machines: nothing they write may ping the server.
    assert discord.posts[0]["body"]["allowed_mentions"] == {"parse": []}


def test_the_messages_are_posted_under_the_database_s_name():
    discord = FakeDiscord()

    announce_discord.send(WEBHOOK, "New game available", opener=discord)

    assert discord.posts[0]["body"]["username"] == "FoD Game Database"



def test_a_rate_limit_is_waited_out_and_the_message_still_goes():
    discord = FakeDiscord(fail=http_error(429, b'{"retry_after": 0.5}'))
    waited = []

    assert announce_discord.send(WEBHOOK, "New game available", opener=discord,
                                 sleep=waited.append) is True
    assert waited == [0.5]
    assert len(discord.posts) == 2


def test_a_discord_that_refuses_is_only_a_warning():
    discord = FakeDiscord(fail=http_error(400))

    assert announce_discord.send(WEBHOOK, "New game available", opener=discord,
                                 sleep=lambda seconds: None) is False


def test_a_discord_that_cannot_be_reached_is_only_a_warning():
    discord = FakeDiscord(fail=OSError("no route"))

    assert announce_discord.send(WEBHOOK, "x", opener=discord, sleep=lambda seconds: None) is False


# The run itself -----------------------------------------------------------


@pytest.fixture
def announced(tmp_path, monkeypatch):
    """$FOD_ANNOUNCE_FILE holding one game, with Discord and the site pointed somewhere harmless."""
    path = tmp_path / "announce.json"
    path.write_text(json.dumps([GAME]), encoding="utf-8")
    monkeypatch.setenv("FOD_ANNOUNCE_FILE", str(path))
    monkeypatch.setenv("FOD_SITE_URL", "https://example.test/")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    return path


def test_a_run_posts_one_message_for_each_new_game(announced, monkeypatch):
    discord = FakeDiscord()
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(announce_discord.urllib.request, "urlopen", discord)

    assert announce_discord.main([]) == 0
    assert len(discord.posts) == 1
    assert discord.posts[0]["body"]["content"].startswith("New game available, RToWin Vs scotSWORD")


def test_nothing_to_announce_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FOD_ANNOUNCE_FILE", str(tmp_path / "never-written.json"))

    assert announce_discord.main([]) == 0


def test_no_webhook_is_not_a_failure(announced):
    assert announce_discord.main([]) == 0


def test_a_dry_run_posts_nothing(announced, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(announce_discord.urllib.request, "urlopen", _explode)

    assert announce_discord.main(["--dry-run"]) == 0
    assert "https://example.test/?save=" in capsys.readouterr().out


def _explode(*arguments, **keywords):
    raise AssertionError("a dry run must not post")


# The rule the site shares -------------------------------------------------


def test_the_computer_collapses_into_one_ai_however_many_seats_it_held():
    assert display_players("Lord Godalming & Van Helsing & Mina Harker") == "AI"


def test_a_table_of_people_is_left_alone():
    assert display_players("RToWin & scotSWORD") == "RToWin & scotSWORD"
