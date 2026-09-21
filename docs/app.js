/* The search site. Loads index.json (built from data/games.csv) and renders one filterable,
   sortable table. No framework, no build step: edit this file and push.

   Downloads go through fetch + a Blob URL because the `download` attribute is ignored on a
   cross-origin link — a plain link to raw.githubusercontent.com would show the JSON in the tab
   instead of saving a .fod. That is also why the Discord announcements link here: ?save=<file> fetches
   that save and hands it over straight away, without waiting for the table (the index is only rebuilt
   when Pages redeploys, a minute or so after the file itself is there), and ?game=<id> shows one game. */

(function () {
  "use strict";

  var CONFIG = window.FOD_CONFIG || {};
  var NUMBERS = ["influence", "dracula_health", "dracula_max_health", "days_completed", "score",
                 "save_version", "action_count"];

  // The names the game gives seats nobody renamed (GameConfig.DefaultConfig). A computer seat keeps its
  // character's name, and that is all a row says about it: has_ai covers the whole game, not the seats.
  // So those names are shown as "AI" here, the way tools/fodsave.py shows them in a Discord message.
  // Kept in step with CHARACTER_NAMES there; tests/test_site.py checks the two lists agree.
  var CHARACTER_NAMES = ["Lord Godalming", "Dr. John Seward", "Van Helsing", "Mina Harker", "Dracula"];
  var AI_NAME = "AI";
  var NAME_JOIN = " & ";

  var games = [];
  var tournaments = [];                // newest first, as index.json lists them
  var state = {
    // What a link points at: a game by upload id, a save by file name. Shown on their own; any filter
    // or search clears them.
    game: parameter("game"),
    save: parameter("save"),
    search: "",
    database: "all",
    // A tournament's name, or "all". ?tournament=<name> picks one, for a link to a tournament's games.
    tournament: parameter("tournament") || "all",
    winner: "all",
    months: "all",
    key: "received_at_utc",
    descending: true
  };

  var pending = !!state.game;          // a ?game= link downloads once the table knows which file it is

  var rows = document.getElementById("rows");
  var count = document.getElementById("count");
  var message = document.getElementById("message");

  // A linked save is fetched at once, before the table has loaded: the file is in the repository as
  // soon as the message is posted, while the index behind the table is a redeploy behind.
  if (state.save) { download(state.save); }

  // Loading ---------------------------------------------------------------

  // no-store: the index changes whenever a game is uploaded, and a stale copy looks like a bug.
  fetch("index.json", { cache: "no-store" })
    .then(function (answer) {
      if (!answer.ok) { throw new Error("HTTP " + answer.status); }
      return answer.json();
    })
    .then(function (index) {
      games = (index && index.games) || [];
      tournaments = (index && index.tournaments) || [];
      fillTournaments();
      var generated = document.getElementById("generated");
      if (generated && index && index.generated) {
        generated.textContent = "index built " + local(index.generated);
      }
      render();
    })
    .catch(function (error) {
      count.textContent = "";
      say("The list of games could not be loaded (" + error.message + "). Try again in a moment.", true);
    });

  if (CONFIG.repoUrl) {
    document.getElementById("repo-link").href = CONFIG.repoUrl;
  }

  // Controls --------------------------------------------------------------

  document.getElementById("search").addEventListener("input", function (event) {
    state.search = event.target.value.trim().toLowerCase();
    state.game = state.save = "";
    render();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
    chip.addEventListener("click", function () {
      var group = chip.getAttribute("data-filter");
      state[group] = chip.getAttribute("data-value");
      state.game = state.save = "";
      Array.prototype.forEach.call(
        document.querySelectorAll('.chip[data-filter="' + group + '"]'),
        function (other) { other.classList.toggle("is-on", other === chip); });
      render();
    });
  });

  document.getElementById("tournament").addEventListener("change", function (event) {
    state.tournament = event.target.value;
    state.game = state.save = "";
    render();
  });

  function fillTournaments() {
    var select = document.getElementById("tournament");
    tournaments.forEach(function (tournament) {
      var option = document.createElement("option");
      option.value = tournament.name;
      option.textContent = tournament.name + (tournament.end_utc ? "" : " (ongoing)");
      select.appendChild(option);
    });
    // A link to a tournament that isn't in the list (renamed, or mistyped) shows every game instead.
    if (!findTournament(state.tournament)) { state.tournament = "all"; }
    select.value = state.tournament;
    document.getElementById("tournament-picker").hidden = tournaments.length === 0;
  }

  function findTournament(name) {
    for (var i = 0; i < tournaments.length; i++) {
      if (tournaments[i].name === name) { return tournaments[i]; }
    }
    return null;
  }

  Array.prototype.forEach.call(document.querySelectorAll(".sort"), function (header) {
    header.addEventListener("click", function () {
      var key = header.getAttribute("data-key");
      if (state.key === key) {
        state.descending = !state.descending;
      } else {
        state.key = key;
        // Numbers and dates are most interesting largest-first; names read better A to Z.
        state.descending = NUMBERS.indexOf(key) >= 0 || key === "received_at_utc";
      }
      render();
    });
  });

  // Rendering -------------------------------------------------------------

  function render() {
    var shown = games.filter(matches);
    var linked = state.game || state.save;
    var downloaded = state.save;       // kept for the notice: the fallback below clears state.save
    var stale = false;
    if (linked && shown.length === 0 && games.length > 0) {
      // The link points at a game this index doesn't hold yet: it is rebuilt when Pages redeploys, a
      // minute or so behind the save itself. A ?save= download is already on its way regardless.
      state.game = state.save = "";
      pending = false;
      stale = true;
      shown = games.filter(matches);
    }
    shown.sort(compare);

    describeTournament();
    rows.textContent = "";
    shown.forEach(function (game) { rows.appendChild(row(game)); });
    markSortedColumn();

    count.textContent = summary(shown.length, games.length);
    if (games.length === 0) {
      say("No games yet. Finish a game with the mod and use the Game Over screen to send one.", false);
    } else if (stale) {
      say(downloading(downloaded, "The list doesn't have that game yet — it appears here a minute " +
                      "or so after the save does. Showing every game."), false);
    } else if (shown.length === 0) {
      say("No game matches that.", false);
    } else if (linked) {
      say(downloading(downloaded,
                      "One game, the one the link points at. Any filter above brings back every game."),
          false);
    } else {
      say("", false);
    }

    if (pending && state.game && shown.length > 0) {
      pending = false;
      var first = rows.querySelector(".download");
      if (first) { first.click(); }
    }
  }

  function matches(game) {
    if (state.save) { return game.file === state.save; }
    if (state.game) { return game.id === state.game; }
    if (state.database !== "all" && game.database !== state.database) { return false; }
    if (state.tournament !== "all" && game.tournament !== state.tournament) { return false; }
    if (state.winner !== "all" && game.winner_side !== state.winner) { return false; }
    if (state.months !== "all") {
      var received = Date.parse(game.received_at_utc);
      // A date we can't read is shown rather than hidden: it is still a real game.
      if (!isNaN(received) && received < monthsAgo(Number(state.months))) { return false; }
    }
    if (!state.search) { return true; }
    // The names as shown too, so searching "AI" finds the games the computer played.
    var shownNames = ((game.hunters || "") + " " + (game.dracula || "") + " " +
                      displayNames(game.hunters) + " " + displayNames(game.dracula)).toLowerCase();
    return shownNames.indexOf(state.search) >= 0;
  }

  function compare(a, b) {
    // The name columns sort by what the table shows, so the computer's games sit together under "AI".
    var shown = state.key === "hunters" || state.key === "dracula";
    var left = shown ? displayNames(a[state.key]) : a[state.key];
    var right = shown ? displayNames(b[state.key]) : b[state.key];
    var order;
    if (NUMBERS.indexOf(state.key) >= 0) {
      order = (left === null || left === undefined ? -Infinity : left) -
              (right === null || right === undefined ? -Infinity : right);
    } else {
      order = String(left === null || left === undefined ? "" : left)
        .localeCompare(String(right === null || right === undefined ? "" : right), undefined,
                       { sensitivity: "base" });
    }
    // Ties fall back to the id, which starts with the receipt time, so the order is stable.
    if (order === 0) { order = String(a.id || "").localeCompare(String(b.id || "")); }
    return state.descending ? -order : order;
  }

  function row(game) {
    var tr = document.createElement("tr");

    var date = cell(tr, stamp(game.received_at_utc));
    date.title = "Your time: " + local(game.received_at_utc) + "\nUpload " + (game.id || "") +
      (game.ended_at_utc ? "\nGame ended (player's clock): " + game.ended_at_utc : "") +
      (game.game_type ? "\n" + game.game_type + " game" : "") +
      (game.action_count ? "\n" + game.action_count + " actions" : "");

    // Names are the one column allowed to wrap: several hunters make a long line.
    names(tr, game.hunters);
    names(tr, game.dracula);

    var winner = cell(tr, "");
    var side = document.createElement("span");
    side.className = game.winner_side === "Hunters" ? "side-hunters" : "side-dracula";
    side.textContent = game.winner_side || "";
    side.title = displayNames(game.winner_players) + " beat " + displayNames(game.loser_players);
    winner.appendChild(side);

    number(tr, game.influence);
    number(tr, text(game.dracula_health) + "/" + text(game.dracula_max_health));
    number(tr, game.days_completed);
    number(tr, game.score);

    var database = cell(tr, "");
    var tag = document.createElement("span");
    tag.className = "tag " + (game.database || "");
    tag.textContent = game.database || "";
    if (game.tournament) { tag.title = game.tournament; }
    database.appendChild(tag);

    // Everything needed to replay the game: the mod version and config code, which the save does not
    // carry, and the rules it was set up with, which it does.
    var mod = cell(tr, (game.mod_version || "?") + " · " + (game.config_code || "?"));
    mod.title = "Mod version " + (game.mod_version || "?") + ", config code " +
      (game.config_code || "?") + ", save version " + text(game.save_version) +
      "\nAdvanced rules: " + (game.advanced_rules || "?") +
      "\nHouse rules: " + (game.house_rules || "none beyond the standard ones") +
      "\nMod house rules: " + (game.mod_house_rules || "none");
    if (game.has_ai) {
      var ai = document.createElement("span");
      ai.className = "ai";
      ai.textContent = " (AI)";
      ai.title = "A seat was played by the computer";
      mod.appendChild(ai);
    }

    var download = cell(tr, "");
    download.className = "download-col";
    download.appendChild(button(game));
    return tr;
  }

  function names(tr, players) {
    var td = cell(tr, displayNames(players));
    td.className = "names";
    // The names the seats were played under are still there for anyone who wants them.
    if (players && td.textContent !== players) { td.title = "In the game: " + players; }
    return td;
  }

  function displayNames(players) {
    var shown = [];
    String(players || "").split(NAME_JOIN).forEach(function (name) {
      var label = CHARACTER_NAMES.indexOf(name.trim()) >= 0 ? AI_NAME : name.trim();
      if (label && shown.indexOf(label) < 0) { shown.push(label); }
    });
    return shown.join(NAME_JOIN);
  }

  function parameter(name) {
    // The Discord announcements link here as .../?save=<file>; ?game=<upload id> works too.
    var pattern = new RegExp("[?&#]" + name + "=([^&]*)");
    var found = pattern.exec(window.location.search || "") || pattern.exec(window.location.hash || "");
    if (!found) { return ""; }
    try {
      return decodeURIComponent(found[1]).trim();
    } catch (error) {
      return found[1].trim();
    }
  }

  function downloading(file, words) {
    // Browsers can stop a download nobody clicked for, so never let the page look like it did nothing.
    return file ? "Downloading " + file + ". If your browser stopped that, use the Save button. " + words
      : words;
  }

  function button(game) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "download";
    element.textContent = "Save";
    element.title = game.file || "";
    element.addEventListener("click", function () { download(game.file, element); });
    return element;
  }

  function download(file, element) {
    if (!file) { return; }
    if (element) {
      element.disabled = true;
      element.classList.remove("failed");
      element.textContent = "…";
    }
    fetch(CONFIG.rawBase + encodeURIComponent(file), { cache: "no-store" })
      .then(function (answer) {
        if (!answer.ok) { throw new Error("HTTP " + answer.status); }
        return answer.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = file;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Revoked late: some browsers cancel the save if the URL dies too soon.
        setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
        if (!element) { return; }
        element.textContent = "Saved";
        setTimeout(function () { element.textContent = "Save"; element.disabled = false; }, 2500);
      })
      .catch(function (error) {
        if (!element) {
          say(file + " could not be downloaded (" + error.message + "). It may not have been filed " +
              "yet; try again in a minute.", true);
          return;
        }
        element.textContent = "Failed";
        element.classList.add("failed");
        element.disabled = false;
        element.title = file + " could not be downloaded: " + error.message;
      });
  }

  function markSortedColumn() {
    Array.prototype.forEach.call(document.querySelectorAll(".sort"), function (header) {
      var sorted = header.getAttribute("data-key") === state.key;
      header.classList.toggle("asc", sorted && !state.descending);
      header.classList.toggle("desc", sorted && state.descending);
      header.parentNode.setAttribute(
        "aria-sort", sorted ? (state.descending ? "descending" : "ascending") : "none");
    });
  }

  function describeTournament() {
    // The chosen tournament's format and dates, in UTC like the table's Date column.
    var info = document.getElementById("tournament-info");
    var tournament = findTournament(state.tournament);
    info.hidden = !tournament || !!(state.game || state.save);
    if (info.hidden) { return; }
    info.textContent = [tournament.format,
                        stamp(tournament.start_utc) + " to " +
                        (tournament.end_utc ? stamp(tournament.end_utc) : "now (ongoing)") + " UTC"]
      .filter(Boolean).join(" · ");
  }

  function summary(shown, total) {
    if (total === 0) { return "No games yet."; }
    var word = total === 1 ? "game" : "games";
    if (shown === total) { return total + " " + word + "."; }
    return shown + " of " + total + " " + word + ".";
  }

  function say(words, isError) {
    message.textContent = words;
    message.hidden = !words;
    message.classList.toggle("error", !!isError);
  }

  // Small helpers ---------------------------------------------------------

  function cell(tr, words) {
    var td = document.createElement("td");
    td.appendChild(document.createTextNode(words));
    tr.appendChild(td);
    return td;
  }

  function number(tr, value) {
    var td = cell(tr, text(value));
    td.className = "num";
    return td;
  }

  function text(value) {
    return value === null || value === undefined || value === "" ? "?" : String(value);
  }

  // What was not standard about a game, in one short line. The table doesn't show it — the Mod column's
  // tooltip has the rules in full — but the Discord integration wants exactly this.
  window.fodSummariseRules = summarise;

  function summarise(game) {
    var changed = [game.mod_house_rules, game.house_rules].filter(function (part) {
      return part && part !== "unknown";
    }).join("; ");
    if (changed) { return changed; }
    if (game.house_rules === "unknown" || game.advanced_rules === "unknown") { return "?"; }
    return "Standard";
  }

  function monthsAgo(months) {
    var when = new Date();
    when.setMonth(when.getMonth() - months);
    return when.getTime();
  }

  function stamp(iso) {
    // "2026-09-17T21:37:44.163Z" -> "2026-09-17 21:37", still UTC: the file names use this time.
    if (!iso) { return "?"; }
    return String(iso).slice(0, 10) + " " + String(iso).slice(11, 16);
  }

  function local(iso) {
    // The same moment in whatever timezone the browser is in, named so nobody has to guess.
    var when = new Date(iso);
    if (!iso || isNaN(when.getTime())) { return stamp(iso); }
    var zone = "";
    try {
      zone = " " + Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (error) {
      zone = "";
    }
    return when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) + zone;
  }
})();
