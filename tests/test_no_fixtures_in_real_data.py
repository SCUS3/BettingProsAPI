import glob
import os
import re

import pytest

from nbaprops.rules import ledger as L

FIXTURE = re.compile(r"^(?:[A-Z]|x|test|fixture)$")


def test_real_ledger_has_no_fixture_players():
    bad = [(r["date"], r["strategy"], r["player"]) for r in L._read(L.LEDGER) if FIXTURE.match(r["player"].strip())]
    assert bad == []


def test_real_notes_have_no_fixture_rows():
    bad = []
    for f in glob.glob(os.path.join(L.HERE, "bp_top10_*.md")):
        for ln in open(f):
            cells = [c.strip() for c in ln.split("|")]
            if len(cells) > 3 and cells[1].isdigit() and FIXTURE.match(cells[2]):
                bad.append((os.path.basename(f), ln.strip()[:60]))
    assert bad == []


def test_tests_cannot_write_the_real_ledger():
    with pytest.raises(RuntimeError):
        L._write(L.LEDGER, L._read(L.LEDGER), L.LEDGER_FIELDS)
