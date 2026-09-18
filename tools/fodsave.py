"""Check one uploaded Fury of Dracula save and turn it into a database row.

Pure: no network, no files, no clock. `validate(body, envelope)` either returns a `Record` or raises
`Reject` with a short reason that goes into data/rejected.csv. Everything the Cloudflare Worker was too
small to check happens here (it only sniffs the first 256 bytes), so this is where junk is really stopped.

The rules and the naming come from the design:
docs/superpowers/specs/2026-09-17-game-over-save-and-database-design.md in the mod's repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import astuple, dataclass, fields

MAX_BODY_BYTES = 4_000_000
SAVE_TYPE = "FuryOfDracula.Core.PersistentGameState, Assembly-CSharp"
MARKER_TYPE = "GameActions.GameOverMarker, Assembly-CSharp"

PLAYER_COUNT = 5
DRACULA_SEAT = 4
MIN_ACTIONS = 20
MIN_SAVE_VERSION = 30
MAX_SAVE_VERSION = 60

MAX_INFLUENCE = 13
VANILLA_MAX_HEALTH = 15          # CharacterSheetData.StartingHealth
HIGHEST_MAX_HEALTH = 25          # plus the mod's DraculaAdditionalHealth house rule (up to 10)
MAX_DAYS = 500
LONG_GAME_DAYS = 21              # from here the hunters get the score bonus

MOD_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CONFIG_CODE = re.compile(r"^[0-9A-F]{6}$")
GAME_TYPES = {0: "Local", 1: "Network"}

NAME_JOIN = " & "
MAX_TOKEN = 20
RULE_JOIN = "; "

# The names the game gives seats nobody renamed (GameConfig.DefaultConfig in Assembly-CSharp). A computer
# seat keeps its character's name, and that is the only trace of it in a row: the save says which seats
# were the computer, but games.csv keeps only has_ai, for the game as a whole. So a name from this list
# is shown as "AI" wherever players are shown: here, and in the same words in docs/app.js. The names
# the game was played under are what is stored; this is display only.
CHARACTER_NAMES = ("Lord Godalming", "Dr. John Seward", "Van Helsing", "Mina Harker", "Dracula")
AI_NAME = "AI"

# The setup the game was played with. All of it is in the file's Config, and all of it is needed to
# replay the game. AdvancedRules.PowerCards: the five real cards are bits 1..16.
POWER_CARDS = ((1, "Hide"), (2, "Wolf Form"), (4, "Misdirect"), (8, "Dark Call"), (16, "Feed"))

# Config.HouseRules against HouseRules.DefaultRules: field, the standard value, and how a game that
# changed it reads in the table.
VANILLA_HOUSE_RULES = (
    ("DraculaLandToSeaDamage", 2, "Land-to-sea damage {}"),
    ("DraculaSeaToSeaDamage", 1, "Sea-to-sea damage {}"),
    ("FODInfluence", 3, "Fury of Dracula influence {}"),
    ("FODAllowedSeaHideoutCount", 6, "Sea hideouts {}"),
    ("RumourTokenInfluence", 3, "Rumour token influence {}"),
    ("DarkCallDrawEventCard", False, "Dark Call draws an event card"),
    ("DraculaCantSeeMarkers", False, "Dracula can't see the markers"),
    ("SlideTrailBeforeMovingDracula", False, "Trail slides before Dracula moves"),
    ("ForbidWolfFormOnConsecratedGround", False, "No Wolf Form onto consecrated ground"),
    ("RemoveRumourTokensWhenNoEncounters", False, "Rumour tokens go when the encounters do"),
)

# The mod's own house rules are in the same save: HouseRules has no room for a new setting, so each one
# is an entry in its private power card play limit dictionary, under a bit no power card uses, and only
# when it is not at its default. A label with {} is a slider and shows its number; the rest are tick
# boxes. Source of truth: src/DraculaMod/Fixes/HouseRuleFlag.cs and the *Rule.cs files beside it.
STORED_ZERO = -1                 # how a slider's 0 is stored (a stored 0 would read as "no house rules")
MOD_HOUSE_RULES = {
    1 << 30: "Revised Consecrated Ground",
    1 << 29: "Manchester becomes Whitby",
    1 << 28: "Sister Agatha damage {}",
    1 << 27: "Long Night at dawn or dusk",
    1 << 26: "Heavenly Host range {}",
    1 << 25: "Great Strength only at your location",
    1 << 24: "Advanced Dracula AI",
    1 << 23: "Heroic Leap damage limited by your health",
    1 << 22: "Dracula mulligans his opening hand",
    1 << 21: "Dracula starts with an encounter",
    1 << 20: "Dracula additional health {}",
    1 << 19: "Dracula starting damage {}",
    1 << 18: "Rifle does not clear Rats",
    1 << 17: "Feed healing {}",
    1 << 16: "Dark Call damage {}",
}

# Non-string columns, for reading games.csv back (build_index.py, the dedupe check).
INT_COLUMNS = (
    "influence", "dracula_health", "dracula_max_health", "days_completed", "score",
    "save_version", "action_count",
)
BOOL_COLUMNS = ("has_ai",)


class Reject(Exception):
    """The upload is not a finished game we can store; the message is the reason column."""


@dataclass
class Record:
    """One row of data/games.csv, in column order."""

    id: str
    received_at_utc: str
    database: str
    file: str
    hunters: str
    dracula: str
    winner_side: str
    winner_players: str
    loser_players: str
    influence: int
    dracula_health: int
    dracula_max_health: int
    days_completed: int
    score: int
    game_type: str
    advanced_rules: str
    house_rules: str
    mod_house_rules: str
    has_ai: bool
    save_version: int
    mod_version: str
    config_code: str
    identifier: str
    log_sha256: str
    ended_at_utc: str
    action_count: int

    @property
    def hunter_names(self):
        return self.hunters.split(NAME_JOIN) if self.hunters else []

    def row(self):
        """The values as games.csv holds them (booleans as true/false)."""
        return ["true" if value is True else "false" if value is False else str(value)
                for value in astuple(self)]


COLUMNS = [field.name for field in fields(Record)]


def file_token(name):
    """A player name as it appears in a file name: RToWin -> Rtowin, scotSWORD -> Scotsword."""
    token = "".join(c for c in (name or "") if c.isascii() and c.isalnum())[:MAX_TOKEN]
    if not token:
        token = "Player"
    return token[0].upper() + token[1:].lower()


def display_players(names):
    """Players as they are shown: a seat still called after its character reads as "AI", once.

    Takes a games.csv names cell or a list of names. Several computer seats collapse into one "AI", the
    way one person holding several hunter seats is named once.
    """
    if isinstance(names, str):
        names = names.split(NAME_JOIN) if names else []
    shown = []
    for name in names:
        label = AI_NAME if (name or "").strip() in CHARACTER_NAMES else (name or "").strip()
        if label and label not in shown:
            shown.append(label)
    return NAME_JOIN.join(shown)


def file_name(hunters, dracula, when):
    """Rtowin_Vs_Scotsword_2026-09-17_20-11-05.fod: the hunters in seat order, Dracula, the time."""
    tokens = [file_token(name) for name in hunters] or [file_token("")]
    return "{}_Vs_{}_{}.fod".format(
        "_".join(tokens), file_token(dracula), when.strftime("%Y-%m-%d_%H-%M-%S")
    )


def score(influence, dracula_health, dracula_max_health, days_completed):
    """Influence less the damage the hunters dealt, plus 2 if they held out for 21 days or more."""
    return (influence - (dracula_max_health - dracula_health)
            + (2 if days_completed >= LONG_GAME_DAYS else 0))


def log_sha256(entries):
    """The actions alone, hashed. ResultingHash is left out: it can differ between machines."""
    actions = json.dumps([entry.get("Action") for entry in entries], sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(actions.encode("utf-8")).hexdigest()


def advanced_rules(config):
    """The advanced rules the game was set up with, as the table reads them: "Lairs; rumour tokens…"."""
    rules = config.get("AdvancedRules")
    if not isinstance(rules, dict):
        return "unknown"
    parts = []
    cards = rules.get("PowerCardsConfig")
    if isinstance(cards, int) and not isinstance(cards, bool) and cards:
        named = [name for bit, name in POWER_CARDS if cards & bit]
        extra = cards & ~sum(bit for bit, _ in POWER_CARDS)
        if extra:                                    # a card the game gained after this was written
            named.append("+{}".format(extra))
        parts.append("Power cards: " +
                     ("all" if named == [name for _, name in POWER_CARDS] else ", ".join(named)))
    for field, label in (("FreePlacement", "Free placement"), ("RumourTokens", "Rumour tokens"),
                         ("Lairs", "Lairs")):
        if rules.get(field) is True:
            parts.append(label)
    return RULE_JOIN.join(parts) or "None"


def house_rules(config):
    """The game's own house rules that are not the standard ones; empty when the game was standard."""
    rules = config.get("HouseRules")
    if not isinstance(rules, dict):
        return "unknown"
    parts = []
    for field, standard, label in VANILLA_HOUSE_RULES:
        value = rules.get(field)
        if value is None or value == standard:
            continue
        if isinstance(standard, bool):
            parts.append(label if value is True else "not " + label[0].lower() + label[1:])
        elif isinstance(value, int) and not isinstance(value, bool):
            parts.append(label.format(value))
    for bit, name in POWER_CARDS:                    # the game's own limit on playing a power card
        limit = _limits(rules).get(bit)
        if limit:
            parts.append("{} limit {}".format(name, 0 if limit == STORED_ZERO else limit))
    return RULE_JOIN.join(parts)


def mod_house_rules(config):
    """The mod's house rules this game was played with; empty when none of them were used."""
    rules = config.get("HouseRules")
    if not isinstance(rules, dict):
        return "unknown"
    parts = []
    for bit, value in sorted(_limits(rules).items(), reverse=True):
        if bit <= POWER_CARDS[-1][0] or not value:
            continue                                 # a real power card's play limit, or an unset rule
        label = MOD_HOUSE_RULES.get(bit, "Mod rule {}".format(bit) + " {}")
        parts.append(label.format(0 if value == STORED_ZERO else value) if "{}" in label else label)
    return RULE_JOIN.join(parts)


def _limits(rules):
    """HouseRules._powerCardsPlayLimits as {bit: value}; its JSON keys are the numbers as strings."""
    limits = rules.get("_powerCardsPlayLimits")
    if not isinstance(limits, dict):
        return {}
    found = {}
    for key, value in limits.items():
        if key == "$type" or not isinstance(value, int) or isinstance(value, bool):
            continue
        try:
            found[int(key)] = value
        except (TypeError, ValueError):
            continue
    return found


def validate(body, envelope):
    """The record for this upload, or Reject(reason)."""
    save = _parse(body)
    players = _players(save)
    entries = _entries(save)
    meta = _meta(envelope, save, entries)

    # Seats 0..3 are the hunters; one person may hold several, so each name counts once.
    hunters = []
    for seat, player in enumerate(players):
        if seat != DRACULA_SEAT and player["name"] not in hunters:
            hunters.append(player["name"])
    dracula = players[DRACULA_SEAT]["name"]
    hunter_victory = meta["hunterVictory"]
    winners = hunters if hunter_victory else [dracula]
    losers = [dracula] if hunter_victory else hunters

    return Record(
        id=str(envelope.get("id", "")),
        received_at_utc=str(envelope.get("receivedAt", "")),
        database=_database(envelope),
        file="",                                  # the processor names the file (receipt time, collisions)
        hunters=NAME_JOIN.join(hunters),
        dracula=dracula,
        winner_side="Hunters" if hunter_victory else "Dracula",
        winner_players=NAME_JOIN.join(winners),
        loser_players=NAME_JOIN.join(losers),
        influence=meta["influence"],
        dracula_health=meta["draculaHealth"],
        dracula_max_health=meta["draculaMaxHealth"],
        days_completed=meta["daysCompleted"],
        score=score(meta["influence"], meta["draculaHealth"], meta["draculaMaxHealth"],
                    meta["daysCompleted"]),
        game_type=GAME_TYPES[save["Config"]["GameType"]],
        advanced_rules=advanced_rules(save["Config"]),
        house_rules=house_rules(save["Config"]),
        mod_house_rules=mod_house_rules(save["Config"]),
        has_ai=any(player["ai"] for player in players),
        save_version=save["SaveVersion"],
        mod_version=meta["modVersion"],
        config_code=meta["configCode"],
        identifier=save["Identifier"],
        log_sha256=log_sha256(entries),
        ended_at_utc=str(meta.get("endedAtUtc", "")),
        action_count=len(entries),
    )


def _parse(body):
    if len(body) > MAX_BODY_BYTES:
        raise Reject("too large: {} bytes".format(len(body)))
    if not body:
        raise Reject("empty")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Reject("not UTF-8")
    try:
        save = json.loads(text)
    except ValueError as e:
        raise Reject("not JSON: {}".format(e))
    if not isinstance(save, dict):
        raise Reject("not a JSON object")
    if save.get("$type") != SAVE_TYPE:
        raise Reject("not a Fury of Dracula save")
    version = save.get("SaveVersion")
    if not isinstance(version, int) or isinstance(version, bool):
        raise Reject("SaveVersion is not a number")
    if not MIN_SAVE_VERSION <= version <= MAX_SAVE_VERSION:
        raise Reject("SaveVersion {} out of range".format(version))
    try:
        uuid.UUID(str(save.get("Identifier")))
    except (ValueError, AttributeError, TypeError):
        raise Reject("Identifier is not a UUID")
    return save


def _players(save):
    config = save.get("Config")
    if not isinstance(config, dict):
        raise Reject("no Config")
    if config.get("GameType") not in GAME_TYPES:
        raise Reject("GameType {} is not a real game".format(config.get("GameType")))
    values = _values(config.get("Players"), "Config.Players")
    if len(values) != PLAYER_COUNT:
        raise Reject("{} players, expected {}".format(len(values), PLAYER_COUNT))
    players = []
    for seat, player in enumerate(values):
        if not isinstance(player, dict):
            raise Reject("player {} is not an object".format(seat))
        name = player.get("PlayerName")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise Reject("player {} has no usable name".format(seat))
        if player.get("Type") not in (0, 1):
            raise Reject("player {} has type {}".format(seat, player.get("Type")))
        players.append({"name": name.strip(), "ai": player["Type"] == 1})
    return players


def _entries(save):
    log = save.get("ActionLog")
    if not isinstance(log, dict):
        raise Reject("no ActionLog")
    entries = _values(log.get("_actions"), "ActionLog._actions")
    if log.get("Size") != len(entries):
        raise Reject("ActionLog.Size {} but {} actions".format(log.get("Size"), len(entries)))
    if len(entries) < MIN_ACTIONS:
        raise Reject("only {} actions".format(len(entries)))
    last = entries[-1]
    action = last.get("Action") if isinstance(last, dict) else None
    if not isinstance(action, dict) or not str(action.get("$type", "")).endswith(MARKER_TYPE):
        raise Reject("the game didn't end (no GameOverMarker)")
    return entries


def _meta(envelope, save, entries):
    meta = envelope.get("meta")
    if not isinstance(meta, dict):
        raise Reject("no meta")
    if meta.get("schema") != 1:
        raise Reject("meta schema {}".format(meta.get("schema")))
    if not MOD_VERSION.match(str(meta.get("modVersion", ""))):
        raise Reject("bad modVersion")
    if not CONFIG_CODE.match(str(meta.get("configCode", ""))):
        raise Reject("bad configCode")
    if not isinstance(meta.get("hunterVictory"), bool):
        raise Reject("hunterVictory is not a boolean")

    max_health = _int(meta, "draculaMaxHealth", VANILLA_MAX_HEALTH, HIGHEST_MAX_HEALTH)
    health = _int(meta, "draculaHealth", 0, max_health)
    influence = _int(meta, "influence", 0, MAX_INFLUENCE)
    _int(meta, "daysCompleted", 0, MAX_DAYS)
    if _int(meta, "actionCount", 0, 100000) != len(entries):
        raise Reject("meta actionCount {} but {} actions".format(meta["actionCount"], len(entries)))
    if meta.get("saveVersion") != save["SaveVersion"]:
        raise Reject("meta saveVersion {} but file {}".format(meta.get("saveVersion"),
                                                             save["SaveVersion"]))
    if meta.get("gameType") != save["Config"]["GameType"]:
        raise Reject("meta gameType {} but file {}".format(meta.get("gameType"),
                                                          save["Config"]["GameType"]))

    # GameController.EndGameCriteriaMet: Dracula wins at 13 influence, the hunters when his health runs out.
    if meta["hunterVictory"]:
        if health != 0:
            raise Reject("hunters won but Dracula has {} health".format(health))
        if influence >= MAX_INFLUENCE:
            raise Reject("hunters won at {} influence".format(influence))
    elif influence != MAX_INFLUENCE:
        raise Reject("Dracula won at {} influence".format(influence))
    return meta


def _database(envelope):
    database = envelope.get("database")
    if database not in ("tournament", "casual"):
        raise Reject("database {}".format(database))
    return database


def _values(container, what):
    if not isinstance(container, dict) or not isinstance(container.get("$values"), list):
        raise Reject("no {}".format(what))
    return container["$values"]


def _int(meta, field, low, high):
    value = meta.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Reject("{} is not a whole number".format(field))
    if not low <= value <= high:
        raise Reject("{} is {}, outside {}..{}".format(field, value, low, high))
    return value
