"""Drain the Cloudflare KV staging area into this repository.

The Worker parks every accepted upload under `incoming/<id>` in KV: one line of envelope JSON, then the
.fod itself. This reads them oldest first, checks each one properly (tools/fodsave.py), and either

  * writes `saves/<name>.fod` and appends a row to `data/games.csv`, or
  * appends a line to `data/rejected.csv` saying why (no file is kept),

then deletes the key either way. Rebuilds `docs/index.json` at the end and writes a Markdown summary to
$GITHUB_STEP_SUMMARY. Bad uploads are not a pipeline failure: it exits 0 even if everything was rejected,
and 1 only when the Cloudflare API itself won't answer.

Environment: CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN (GitHub secrets in the workflow).
Run with --dry-run to see what it would do without writing or deleting anything.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_index  # noqa: E402
from fodsave import COLUMNS, Reject, file_name, validate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAVES = ROOT / "saves"
GAMES_CSV = ROOT / "data" / "games.csv"
REJECTED_CSV = ROOT / "data" / "rejected.csv"
REJECTED_COLUMNS = ["id", "received_at_utc", "reason"]

PREFIX = "incoming/"
API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 60


class ApiError(Exception):
    """Cloudflare wouldn't answer: the run failed, and the keys stay for the next one."""


class Kv:
    """The bits of the Workers KV REST API this needs."""

    def __init__(self, account, namespace, token):
        self.base = "{}/accounts/{}/storage/kv/namespaces/{}".format(API, account, namespace)
        self.token = token

    @classmethod
    def from_env(cls):
        missing = [name for name in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN")
                   if not os.environ.get(name)]
        if missing:
            raise ApiError("missing environment: " + ", ".join(missing))
        return cls(os.environ["CF_ACCOUNT_ID"], os.environ["CF_KV_NAMESPACE_ID"],
                   os.environ["CF_API_TOKEN"])

    def keys(self, prefix=PREFIX):
        """Every key with the prefix, oldest first (ids start with the receipt time)."""
        names = []
        cursor = None
        while True:
            query = {"prefix": prefix, "limit": "1000"}
            if cursor:
                query["cursor"] = cursor
            answer = json.loads(self._call("/keys?" + urllib.parse.urlencode(query)).decode("utf-8"))
            if not answer.get("success", False):
                raise ApiError("listing keys: {}".format(answer.get("errors")))
            names.extend(item["name"] for item in answer.get("result", []))
            cursor = (answer.get("result_info") or {}).get("cursor") or None
            if not cursor:
                return sorted(names)

    def value(self, key):
        return self._call("/values/" + urllib.parse.quote(key, safe=""))

    def delete(self, key):
        self._call("/values/" + urllib.parse.quote(key, safe=""), method="DELETE")

    def _call(self, path, method="GET"):
        request = urllib.request.Request(self.base + path, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                return answer.read()
        except urllib.error.HTTPError as e:
            raise ApiError("{} {} -> HTTP {}: {}".format(method, path.split("?")[0], e.code,
                                                         e.read()[:200].decode("utf-8", "replace")))
        except OSError as e:
            raise ApiError("{} {} -> {}".format(method, path.split("?")[0], e))


def main(argv):
    dry_run = "--dry-run" in argv
    try:
        kv = Kv.from_env()
        keys = kv.keys()
    except ApiError as e:
        print("Cloudflare: {}".format(e), file=sys.stderr)
        _summary(["## Processing failed", "", "Cloudflare: `{}`".format(e)])
        return 1

    rows = _read_rows()
    by_identifier = {row["identifier"]: row for row in rows}
    used_names = {row["file"] for row in rows} | {path.name for path in _saved_files()}
    accepted, rejected, warnings, changed = [], [], [], False

    for key in keys:
        try:
            value = kv.value(key)
        except ApiError as e:
            # One unreadable key shouldn't stop the batch; it stays for the next run.
            warnings.append("could not read `{}`: {}".format(key, e))
            continue
        upload_id = key[len(PREFIX):]
        envelope, body = _split(value)
        if envelope is None:
            rejected.append((upload_id, "", "malformed staging record"))
            _delete(kv, key, dry_run, warnings)
            continue

        try:
            record = validate(body, envelope)
        except Reject as e:
            rejected.append((upload_id, str(envelope.get("receivedAt", "")), str(e)))
            _delete(kv, key, dry_run, warnings)
            continue

        seen = by_identifier.get(record.identifier)
        if seen is not None:
            # Identifier is made once per game and shared by every player's copy, so this is the same game.
            if seen["log_sha256"] != record.log_sha256:
                warnings.append(
                    "log-mismatch: `{}` has the same game as `{}` but a different action log "
                    "(the players' games disagreed)".format(record.id, seen["id"]))
            if record.database == "tournament" and seen["database"] == "casual":
                seen["database"] = "tournament"
                changed = True
                rejected.append((upload_id, record.received_at_utc, "upgraded:" + seen["id"]))
            else:
                rejected.append((upload_id, record.received_at_utc, "duplicate:" + seen["id"]))
            _delete(kv, key, dry_run, warnings)
            continue

        record.file = _unique_name(record, used_names)
        used_names.add(record.file)
        if not dry_run:
            SAVES.mkdir(parents=True, exist_ok=True)
            (SAVES / record.file).write_bytes(body)
        rows.append(dict(zip(COLUMNS, record.row())))
        by_identifier[record.identifier] = rows[-1]
        accepted.append(record)
        changed = True
        _delete(kv, key, dry_run, warnings)

    if not dry_run:
        if changed:
            _write_rows(rows)
        if rejected:
            _append_rejected(rejected)
        count = build_index.build(GAMES_CSV, build_index.INDEX_JSON)
    else:
        count = len(rows)

    _report(keys, accepted, rejected, warnings, count, dry_run)
    return 0


def _split(value):
    """The staged record: envelope JSON on the first line, then the file itself."""
    line, newline, body = value.partition(b"\n")
    if not newline:
        return None, b""
    try:
        envelope = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, b""
    return (envelope, body) if isinstance(envelope, dict) else (None, b"")


def _unique_name(record, used):
    """The record's file name, with _2, _3 … before .fod if that name is taken."""
    name = file_name(record.hunter_names, record.dracula, _received(record.received_at_utc))
    if name not in used:
        return name
    stem = name[:-len(".fod")]
    number = 2
    while "{}_{}.fod".format(stem, number) in used:
        number += 1
    return "{}_{}.fod".format(stem, number)


def _received(text):
    """The Worker's receipt time; the client's clock is never used for names."""
    try:
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _saved_files():
    return sorted(SAVES.glob("*.fod")) if SAVES.exists() else []


def _read_rows():
    if not GAMES_CSV.exists():
        return []
    with GAMES_CSV.open(newline="", encoding="utf-8") as handle:
        return [{column: row.get(column, "") for column in COLUMNS} for row in csv.DictReader(handle)]


def _write_rows(rows):
    GAMES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with GAMES_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([row.get(column, "") for column in COLUMNS])


def _append_rejected(rejected):
    REJECTED_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not REJECTED_CSV.exists()
    with REJECTED_CSV.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if new:
            writer.writerow(REJECTED_COLUMNS)
        writer.writerows(rejected)


def _delete(kv, key, dry_run, warnings):
    """A key that won't delete is left alone: the next run dedupes it against games.csv."""
    if dry_run:
        return
    try:
        kv.delete(key)
    except ApiError as e:
        warnings.append("could not delete `{}`: {}".format(key, e))


def _report(keys, accepted, rejected, warnings, count, dry_run):
    lines = ["## Incoming saves" + (" (dry run)" if dry_run else ""), "",
             "{} staged, {} accepted, {} rejected.".format(len(keys), len(accepted), len(rejected)), ""]
    if accepted:
        lines += ["### Accepted", "", "| File | Winner | Influence | Health | Days | Score | Database |",
                  "|---|---|---|---|---|---|---|"]
        lines += ["| {} | {} | {} | {}/{} | {} | {} | {} |".format(
            record.file, record.winner_players, record.influence, record.dracula_health,
            record.dracula_max_health, record.days_completed, record.score, record.database)
            for record in accepted]
        lines.append("")
    if rejected:
        lines += ["### Rejected", ""]
        lines += ["- `{}`: {}".format(upload_id, reason) for upload_id, _, reason in rejected]
        lines.append("")
    if warnings:
        lines += ["### Warnings", ""] + ["- " + warning for warning in warnings] + [""]
    lines.append("The database now holds {} games.".format(count))
    text = "\n".join(lines)
    print(text)
    _summary(lines)


def _summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
