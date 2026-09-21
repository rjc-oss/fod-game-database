"""data/tournaments.csv: the Discord community's tournaments, and which one a game belongs to.

One row per tournament: `name`, `start_utc`, `end_utc`, `format`. Times are UTC, written as ISO 8601
(`2026-10-01T18:00:00Z`; a bare date `2026-10-01` also works, and means the start of that day for
`start_utc` and the whole of that day for `end_utc`). Only the latest tournament may leave `end_utc`
empty: it is ongoing. Tournaments may not overlap.

A game belongs to a tournament when it is in the `tournament` database and the server received it between
that tournament's start (included) and end (excluded). Nothing about this is stored in games.csv: it is
worked out again every time docs/index.json is built, so correcting a date here re-files every game.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOURNAMENTS_CSV = ROOT / "data" / "tournaments.csv"
COLUMNS = ["name", "start_utc", "end_utc", "format"]


class TournamentError(Exception):
    """tournaments.csv can't be used as it stands; the message says which row and why."""


@dataclass(frozen=True)
class Tournament:
    name: str
    start: datetime
    end: datetime | None        # None: ongoing
    format: str

    def holds(self, received: datetime) -> bool:
        return self.start <= received and (self.end is None or received < self.end)

    def to_json(self):
        return {"name": self.name, "start_utc": _iso(self.start),
                "end_utc": _iso(self.end) if self.end else "", "format": self.format}


def load(path=None):
    """Every tournament, oldest first. Raises TournamentError if the file breaks the rules above."""
    path = TOURNAMENTS_CSV if path is None else path
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise TournamentError("{}: missing column(s) {}".format(path.name, ", ".join(missing)))
        tournaments = []
        for line, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue                        # a blank line
            tournaments.append(_parse(row, "{} line {}".format(path.name, line)))

    tournaments.sort(key=lambda tournament: tournament.start)
    names = set()
    for index, tournament in enumerate(tournaments):
        if tournament.name in names:
            raise TournamentError("two tournaments are called {!r}".format(tournament.name))
        names.add(tournament.name)
        if index + 1 == len(tournaments):
            break
        following = tournaments[index + 1]
        if tournament.end is None:
            raise TournamentError("{!r} has no end but is not the latest tournament ({!r} starts after it)"
                                  .format(tournament.name, following.name))
        if following.start < tournament.end:
            raise TournamentError("{!r} and {!r} overlap".format(tournament.name, following.name))
    return tournaments


def assign(game, tournaments):
    """The name of the tournament a game (a games.csv row) belongs to, or "" for none."""
    if game.get("database") != "tournament":
        return ""
    received = parse_time(game.get("received_at_utc", ""))
    if received is None:
        return ""
    for tournament in tournaments:
        if tournament.holds(received):
            return tournament.name
    return ""


def parse_time(text, end_of_day=False):
    """An ISO 8601 time as an aware UTC datetime, or None if it isn't one. A time without a zone is UTC."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if len(text) == 10 and end_of_day:          # a bare date: the whole of that day
        when += timedelta(days=1)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _parse(row, where):
    name = (row.get("name") or "").strip()
    if not name:
        raise TournamentError("{}: no name".format(where))
    start = parse_time(row.get("start_utc"))
    if start is None:
        raise TournamentError("{}: start_utc {!r} is not a date and time".format(where, row.get("start_utc")))
    end_text = (row.get("end_utc") or "").strip()
    end = parse_time(end_text, end_of_day=True) if end_text else None
    if end_text and end is None:
        raise TournamentError("{}: end_utc {!r} is not a date and time".format(where, end_text))
    if end is not None and end <= start:
        raise TournamentError("{}: {!r} ends before it starts".format(where, name))
    return Tournament(name, start, end, (row.get("format") or "").strip())


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")
