"""The processor: what lands in the repository, what is rejected, and what a second copy of a game does.

    python -m pytest

Cloudflare is replaced by a dictionary of staged records, and every path the processor writes to is moved
into a temporary directory, so these tests touch neither the network nor the real database.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_index  # noqa: E402
import process_incoming  # noqa: E402

from test_fodsave import FIXTURE, META, envelope  # noqa: E402


class FakeKv:
    """The staging area as a dictionary, so a test can watch what is read and deleted."""

    def __init__(self, staged=None):
        self.staged = dict(staged or {})
        self.deleted = []

    def keys(self, prefix=process_incoming.PREFIX):
        return sorted(key for key in self.staged if key.startswith(prefix))

    def value(self, key):
        return self.staged[key]

    def delete(self, key):
        self.deleted.append(key)
        del self.staged[key]


def stage(kv, upload_id, save, database="casual", meta=None, received="2026-09-17T21:37:44.000Z"):
    letter = dict(envelope(meta, database), id=upload_id, receivedAt=received)
    body = json.dumps(save).encode("utf-8")
    kv.staged[process_incoming.PREFIX + upload_id] = json.dumps(letter).encode("utf-8") + b"\n" + body
    return body


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway copy of the database's layout, with Cloudflare replaced by FakeKv."""
    kv = FakeKv()
    monkeypatch.setattr(process_incoming, "SAVES", tmp_path / "saves")
    monkeypatch.setattr(process_incoming, "GAMES_CSV", tmp_path / "data" / "games.csv")
    monkeypatch.setattr(process_incoming, "REJECTED_CSV", tmp_path / "data" / "rejected.csv")
    monkeypatch.setattr(build_index, "INDEX_JSON", tmp_path / "docs" / "index.json")
    monkeypatch.setattr(process_incoming.Kv, "from_env", classmethod(lambda cls: kv))
    kv.root = tmp_path
    return kv


def run(argv=()):
    return process_incoming.main(list(argv))


def games(repo):
    path = repo.root / "data" / "games.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rejections(repo):
    path = repo.root / "data" / "rejected.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def save():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_accepts_a_game_and_files_it(repo, save):
    body = stage(repo, "20260917T213744Z-aaaaaaaa", save)

    assert run() == 0

    rows = games(repo)
    assert len(rows) == 1
    assert rows[0]["file"] == "Rtowin_Vs_Player2_2026-09-17_21-37-44.fod"
    assert rows[0]["database"] == "casual"
    assert rows[0]["winner_side"] == "Hunters"
    assert rows[0]["score"] == "-15"
    # The file is stored exactly as it was uploaded, so it still replays.
    assert (repo.root / "saves" / rows[0]["file"]).read_bytes() == body
    assert repo.deleted == ["incoming/20260917T213744Z-aaaaaaaa"]

    index = json.loads((repo.root / "docs" / "index.json").read_text(encoding="utf-8"))
    assert index["count"] == 1
    assert index["games"][0]["influence"] == 0          # numbers are numbers in the site's index
    assert index["games"][0]["has_ai"] is False


def test_the_games_it_added_are_left_for_the_discord_step(repo, save, tmp_path, monkeypatch):
    announce = tmp_path / "announce.json"
    monkeypatch.setenv("FOD_ANNOUNCE_FILE", str(announce))
    stage(repo, "20260917T213744Z-aaaaaaaa", save)

    assert run() == 0

    announced = json.loads(announce.read_text(encoding="utf-8"))
    assert [game["id"] for game in announced] == ["20260917T213744Z-aaaaaaaa"]
    assert announced[0]["file"] == "Rtowin_Vs_Player2_2026-09-17_21-37-44.fod"


def test_nothing_is_left_for_discord_when_nothing_was_added(repo, save, tmp_path, monkeypatch):
    announce = tmp_path / "announce.json"
    monkeypatch.setenv("FOD_ANNOUNCE_FILE", str(announce))
    stage(repo, "20260917T213744Z-aaaaaaaa", save)
    assert run() == 0
    announce.unlink()
    stage(repo, "20260917T220000Z-bbbbbbbb", save)          # the same game again: nothing new to say

    assert run() == 0
    assert not announce.exists()


def test_a_dry_run_leaves_nothing_for_discord(repo, save, tmp_path, monkeypatch):
    announce = tmp_path / "announce.json"
    monkeypatch.setenv("FOD_ANNOUNCE_FILE", str(announce))
    stage(repo, "20260917T213744Z-aaaaaaaa", save)

    assert run(["--dry-run"]) == 0
    assert not announce.exists()


def test_the_same_game_twice_is_stored_once(repo, save):
    stage(repo, "20260917T213744Z-aaaaaaaa", save)
    assert run() == 0
    stage(repo, "20260917T220000Z-bbbbbbbb", save)
    assert run() == 0

    assert len(games(repo)) == 1
    assert len(list((repo.root / "saves").glob("*.fod"))) == 1
    assert rejections(repo)[-1]["reason"] == "duplicate:20260917T213744Z-aaaaaaaa"


def test_a_tournament_upload_upgrades_a_casual_row(repo, save):
    stage(repo, "20260917T213744Z-aaaaaaaa", save, database="casual")
    assert run() == 0
    stage(repo, "20260917T220000Z-bbbbbbbb", save, database="tournament")
    assert run() == 0

    rows = games(repo)
    assert len(rows) == 1
    assert rows[0]["database"] == "tournament"
    assert rows[0]["file"] == "Rtowin_Vs_Player2_2026-09-17_21-37-44.fod"    # the file is untouched
    assert rejections(repo)[-1]["reason"] == "upgraded:20260917T213744Z-aaaaaaaa"


def test_a_casual_upload_never_downgrades_a_tournament_row(repo, save):
    stage(repo, "20260917T213744Z-aaaaaaaa", save, database="tournament")
    assert run() == 0
    stage(repo, "20260917T220000Z-bbbbbbbb", save, database="casual")
    assert run() == 0

    assert games(repo)[0]["database"] == "tournament"
    assert rejections(repo)[-1]["reason"].startswith("duplicate:")


def test_two_copies_of_a_game_that_disagree_are_reported(repo, save, capsys):
    stage(repo, "20260917T213744Z-aaaaaaaa", save)
    assert run() == 0
    other = json.loads(FIXTURE.read_text(encoding="utf-8"))
    other["ActionLog"]["_actions"]["$values"][0]["Action"]["UID"] = 999
    stage(repo, "20260917T220000Z-bbbbbbbb", other)
    assert run() == 0

    assert "log-mismatch" in capsys.readouterr().out


def test_a_second_game_with_the_same_players_and_second_gets_a_suffix(repo, save):
    stage(repo, "20260917T213744Z-aaaaaaaa", save)
    other = json.loads(FIXTURE.read_text(encoding="utf-8"))
    other["Identifier"] = "11111111-2222-3333-4444-555555555555"
    stage(repo, "20260917T213744Z-bbbbbbbb", other)

    assert run() == 0

    files = sorted(row["file"] for row in games(repo))
    assert files == ["Rtowin_Vs_Player2_2026-09-17_21-37-44.fod",
                     "Rtowin_Vs_Player2_2026-09-17_21-37-44_2.fod"]


def test_junk_is_rejected_without_failing_the_run(repo):
    repo.staged[process_incoming.PREFIX + "20260917T213744Z-cccccccc"] = (
        json.dumps(envelope()).encode("utf-8") + b"\n" + b"<html>not a save</html>")

    assert run() == 0

    assert games(repo) == []
    assert rejections(repo)[0]["reason"].startswith("not JSON")
    assert repo.deleted == ["incoming/20260917T213744Z-cccccccc"]


def test_a_staged_record_without_an_envelope_is_rejected(repo):
    repo.staged[process_incoming.PREFIX + "20260917T213744Z-dddddddd"] = b"no newline here"

    assert run() == 0

    assert rejections(repo)[0]["reason"] == "malformed staging record"


def test_a_dry_run_changes_nothing(repo, save):
    stage(repo, "20260917T213744Z-aaaaaaaa", save)

    assert run(["--dry-run"]) == 0

    assert games(repo) == []
    assert repo.deleted == []
    assert not (repo.root / "saves").exists()


def test_an_unreachable_cloudflare_fails_the_run(repo, monkeypatch):
    def boom(cls):
        raise process_incoming.ApiError("missing environment: CF_API_TOKEN")

    monkeypatch.setattr(process_incoming.Kv, "from_env", classmethod(boom))
    assert run() == 1


def test_meta_that_does_not_match_the_file_is_rejected(repo, save):
    stage(repo, "20260917T213744Z-eeeeeeee", save, meta=dict(META, influence=13))

    assert run() == 0

    assert games(repo) == []
    assert "hunters won at 13" in rejections(repo)[0]["reason"]
