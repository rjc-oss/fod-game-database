"""Tell Discord about the games a run has just added.

process_incoming.py leaves the rows it accepted in $FOD_ANNOUNCE_FILE, and the workflow runs this after
the commit has been pushed, so the link in the message already works. One message per new game:

    New tournament game available, AI Vs RToWin, with house rules Feed healing 3, mod 0.50.0
    config 0C818D, https://rjc-oss.github.io/fod-game-database/?save=Rtowin_Vs_Scotsword_...fod

The link is the download: ?save=<file> has the site fetch that save from raw.githubusercontent.com and
hand it over, which works even in the minute before GitHub Pages has rebuilt the index behind the table
(a plain link to the file would show the JSON in the tab instead: it is served as text/plain).

Nothing here can fail the pipeline: the games are already in the repository by the time it runs, so a
missing webhook, or a Discord that won't answer, prints a warning and exits 0.

Environment: DISCORD_WEBHOOK_URL (a repository secret), FOD_ANNOUNCE_FILE, optionally FOD_SITE_URL.
Run with --dry-run to print the messages without posting them.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fodsave import RULE_JOIN, display_players  # noqa: E402

SITE_URL = "https://rjc-oss.github.io/fod-game-database/"
BOT_NAME = "FoD Game Database"   # what the messages are posted as, whatever the webhook is called
MAX_CONTENT = 2000               # Discord's limit on a message
TIMEOUT = 30
RETRIES = 2                      # one retry, for the rate limit or a hiccup
USER_AGENT = "fod-game-database (+https://github.com/rjc-oss/fod-game-database)"


def link(game, site=None):
    """Where the message points: the site, downloading this save (and showing the game, once it has it).

    By file name, not by upload id: the file can be fetched the moment the commit is pushed, while the
    index the table is drawn from is only rebuilt when GitHub Pages redeploys, a minute or so later.
    """
    base = site or os.environ.get("FOD_SITE_URL") or SITE_URL
    return "{}?save={}".format(base, urllib.parse.quote(str(game.get("file", "")), safe=""))


def summarise_rules(game):
    """What was not standard about the game, in one line. Says the same as window.fodSummariseRules."""
    changed = RULE_JOIN.join(part for part in (game.get("mod_house_rules"), game.get("house_rules"))
                             if part and part != "unknown")
    if changed:
        return changed
    if game.get("house_rules") == "unknown" or game.get("advanced_rules") == "unknown":
        return "?"
    return "Standard"


def message(game, site=None):
    """The line posted for one game."""
    words = "New {} available, {} Vs {}, with house rules {}, mod {} config {}, {}".format(
        "tournament game" if game.get("database") == "tournament" else "game",
        plain(display_players(game.get("hunters", ""))),
        plain(display_players(game.get("dracula", ""))),
        plain(summarise_rules(game)),
        plain(game.get("mod_version")) or "?",
        plain(game.get("config_code")) or "?",
        link(game, site))
    return words if len(words) <= MAX_CONTENT else words[:MAX_CONTENT - 1] + "…"


def plain(text):
    """One line of text. Names and rules come from the players' own machines, so nothing is trusted to
    be tidy; mentions are stopped by allowed_mentions when the message is posted."""
    return " ".join(str(text if text is not None else "").split())


def post(url, content, opener=None):
    """Post one message. Raises urllib's errors; the caller turns them into warnings."""
    payload = json.dumps({
        "content": content,
        "username": BOT_NAME,
        "allowed_mentions": {"parse": []},
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as answer:
        return getattr(answer, "status", None) or answer.getcode()


def send(url, content, opener=None, sleep=time.sleep):
    """Post, waiting out a rate limit once. True if Discord took it."""
    for attempt in range(RETRIES):
        try:
            post(url, content, opener)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt + 1 < RETRIES:
                sleep(_retry_after(e))
                continue
            print("Discord refused a message (HTTP {}): {}".format(
                e.code, e.read()[:200].decode("utf-8", "replace")), file=sys.stderr)
            return False
        except OSError as e:
            print("Discord could not be reached: {}".format(e), file=sys.stderr)
            return False
    return False


def _retry_after(error):
    try:
        return min(float(json.loads(error.read().decode("utf-8")).get("retry_after", 1)), 30)
    except (ValueError, AttributeError, TypeError):
        return 1


def read_games(path):
    """The rows process_incoming.py left behind, or nothing if it didn't add any."""
    if not path:
        return []
    file = Path(path)
    if not file.exists():
        return []
    try:
        games = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as e:
        print("{} is not readable ({}): nothing announced.".format(file, e), file=sys.stderr)
        return []
    return [game for game in games if isinstance(game, dict)] if isinstance(games, list) else []


def main(argv):
    dry_run = "--dry-run" in argv
    games = read_games(os.environ.get("FOD_ANNOUNCE_FILE"))
    if not games:
        print("No new games to announce.")
        return 0

    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url and not dry_run:
        # Not a failure: the games are in the repository either way, and a fork has no secret.
        print("DISCORD_WEBHOOK_URL is not set: {} game(s) not announced.".format(len(games)),
              file=sys.stderr)
        return 0

    sent = 0
    for game in games:
        words = message(game)
        print(words)
        if dry_run or send(url, words):
            sent += 1
    print("Announced {} of {} game(s){}.".format(sent, len(games), " (dry run)" if dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
