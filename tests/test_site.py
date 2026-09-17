"""The search site in docs/: that it only reads columns the table actually has.

    python -m pytest

There is no build step and no JavaScript test runner here, so this just guards the seam between the two
halves: a column renamed in fodsave.py would silently blank a site column, and this notices.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fodsave import COLUMNS  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "docs"
APP_JS = (DOCS / "app.js").read_text(encoding="utf-8")


def test_the_site_reads_only_columns_that_exist():
    used = set(re.findall(r"\bgame\.([a-z_]+)\b", APP_JS))
    assert used, "no game fields found in app.js: has the code been rewritten?"
    assert used <= set(COLUMNS), "app.js reads columns games.csv doesn't have: {}".format(
        sorted(used - set(COLUMNS)))


def test_every_sortable_column_exists():
    keys = set(re.findall(r'data-key="([a-z_]+)"', (DOCS / "index.html").read_text(encoding="utf-8")))
    assert keys, "no sortable headers found in index.html"
    assert keys <= set(COLUMNS)


def test_the_download_base_points_at_the_saves_folder():
    config = (DOCS / "config.js").read_text(encoding="utf-8")
    base = re.search(r'rawBase:\s*"([^"]+)"', config).group(1)
    assert base.startswith("https://raw.githubusercontent.com/")
    assert base.endswith("/saves/"), "rawBase needs the trailing slash the site relies on"


def test_the_page_loads_its_own_files():
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "config.js", "app.js"):
        assert name in html, "index.html never loads {}".format(name)
        assert (DOCS / name).exists()
