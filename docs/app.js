/* The search site. Loads index.json (built from data/games.csv) and renders one filterable,
   sortable table. No framework, no build step: edit this file and push.

   Downloads go through fetch + a Blob URL because the `download` attribute is ignored on a
   cross-origin link — a plain link to raw.githubusercontent.com would show the JSON in the tab
   instead of saving a .fod. */

(function () {
  "use strict";

  var CONFIG = window.FOD_CONFIG || {};
  var NUMBERS = ["influence", "dracula_health", "dracula_max_health", "days_completed", "score",
                 "save_version", "action_count"];

  var games = [];
  var state = {
    search: "",
    database: "all",
    winner: "all",
    key: "received_at_utc",
    descending: true
  };

  var rows = document.getElementById("rows");
  var count = document.getElementById("count");
  var message = document.getElementById("message");

  // Loading ---------------------------------------------------------------

  // no-store: the index changes whenever a game is uploaded, and a stale copy looks like a bug.
  fetch("index.json", { cache: "no-store" })
    .then(function (answer) {
      if (!answer.ok) { throw new Error("HTTP " + answer.status); }
      return answer.json();
    })
    .then(function (index) {
      games = (index && index.games) || [];
      var generated = document.getElementById("generated");
      if (generated && index && index.generated) {
        generated.textContent = "index built " + stamp(index.generated);
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
    render();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
    chip.addEventListener("click", function () {
      var group = chip.getAttribute("data-filter");
      state[group] = chip.getAttribute("data-value");
      Array.prototype.forEach.call(
        document.querySelectorAll('.chip[data-filter="' + group + '"]'),
        function (other) { other.classList.toggle("is-on", other === chip); });
      render();
    });
  });

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
    shown.sort(compare);

    rows.textContent = "";
    shown.forEach(function (game) { rows.appendChild(row(game)); });
    markSortedColumn();

    count.textContent = summary(shown.length, games.length);
    if (games.length === 0) {
      say("No games yet. Finish a game with the mod and use the Game Over screen to send one.", false);
    } else if (shown.length === 0) {
      say("No game matches that.", false);
    } else {
      say("", false);
    }
  }

  function matches(game) {
    if (state.database !== "all" && game.database !== state.database) { return false; }
    if (state.winner !== "all" && game.winner_side !== state.winner) { return false; }
    if (!state.search) { return true; }
    var names = ((game.hunters || "") + " " + (game.dracula || "")).toLowerCase();
    return names.indexOf(state.search) >= 0;
  }

  function compare(a, b) {
    var left = a[state.key];
    var right = b[state.key];
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
    date.title = "Upload " + (game.id || "") +
      (game.ended_at_utc ? "\nGame ended (player's clock): " + game.ended_at_utc : "") +
      (game.game_type ? "\n" + game.game_type + " game" : "") +
      (game.action_count ? "\n" + game.action_count + " actions" : "");

    // Names are the one column allowed to wrap: several hunters make a long line.
    cell(tr, game.hunters || "").className = "names";
    cell(tr, game.dracula || "").className = "names";

    var winner = cell(tr, "");
    var side = document.createElement("span");
    side.className = game.winner_side === "Hunters" ? "side-hunters" : "side-dracula";
    side.textContent = game.winner_side || "";
    side.title = (game.winner_players || "") + " beat " + (game.loser_players || "");
    winner.appendChild(side);

    number(tr, game.influence);
    number(tr, text(game.dracula_health) + "/" + text(game.dracula_max_health));
    number(tr, game.days_completed);
    number(tr, game.score);

    var database = cell(tr, "");
    var tag = document.createElement("span");
    tag.className = "tag " + (game.database || "");
    tag.textContent = game.database || "";
    database.appendChild(tag);

    var mod = cell(tr, game.mod_version || "");
    mod.title = "Config code " + (game.config_code || "?") + ", save version " +
      text(game.save_version) + "\nA save replays correctly only with the same mod version and config code.";
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

  function button(game) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "download";
    element.textContent = "Save";
    element.title = game.file || "";
    element.addEventListener("click", function () { download(game, element); });
    return element;
  }

  function download(game, element) {
    if (!game.file) { return; }
    element.disabled = true;
    element.classList.remove("failed");
    element.textContent = "…";
    fetch(CONFIG.rawBase + encodeURIComponent(game.file), { cache: "no-store" })
      .then(function (answer) {
        if (!answer.ok) { throw new Error("HTTP " + answer.status); }
        return answer.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = game.file;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Revoked late: some browsers cancel the save if the URL dies too soon.
        setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
        element.textContent = "Saved";
        setTimeout(function () { element.textContent = "Save"; element.disabled = false; }, 2500);
      })
      .catch(function (error) {
        element.textContent = "Failed";
        element.classList.add("failed");
        element.disabled = false;
        element.title = game.file + " could not be downloaded: " + error.message;
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

  function stamp(iso) {
    // "2026-09-17T21:37:44.163Z" -> "2026-09-17 21:37" (UTC, as stored; no local-time surprises).
    if (!iso) { return "?"; }
    return String(iso).slice(0, 10) + " " + String(iso).slice(11, 16);
  }
})();
