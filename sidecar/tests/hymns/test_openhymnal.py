"""The Open Hymnal index, the cache, and the request handlers.

No network: `_fetch` is replaced with a tiny site — three index pages and one
score, in the shapes openhymnal.org serves them.
"""

# ruff: noqa: E501 — fixtures keep the real files' long lines intact.

from __future__ import annotations

import json

import pytest

from auto_presenter import server_hymns
from auto_presenter.hymns import openhymnal
from auto_presenter.protocol import GetHymnRequest, SearchHymnsRequest

GENINDEX = """
<tr><td colspan="8"><p class="western">When Peace Like a River see It Is Well With My Soul</p></td></tr>
<tr valign="top">
<td><p class="title"><a href="javascript:new_window('Lyrics/It_Is_Well-Ville_Du_Havre.html')">It Is Well With My Soul</a></p></td>
<td><p class="author">Spafford, H.</p></td><td><p class="tune">Ville Du Havre</p></td></tr>
<tr valign="top">
<td><p class="title"><a href="javascript:new_window('Lyrics/Great_Is_Thy_Faithfulness-Faithfulness.html')">Great Is Thy Faithfulness</a></p></td>
<td><p class="author">Chisholm, T.</p></td><td><p class="tune">Faithfulness</p></td></tr>
<tr valign="top">
<td><p class="title"><a href="javascript:new_window('Lyrics/Author_of_All_Life-Bryn_Calfaria.html')">Author of All Life</a></p></td>
<td><p class="author">Dumont, B.</p></td><td><p class="tune">Bryn Calfaria</p></td></tr>
"""

ALLLYRICS = """
<h3>It Is Well With My Soul (also known as When Peace Like a River)</h3>
<p>
1. When peace, like a river, attendeth my way, When sorrows like sea billows roll;
</p><p><i>Words: Horatio G. Spafford, 1873.<br/>Music: 'Ville Du Havre' Philip P. Bliss, 1876.<br/>copyright: public domain.  This score is a part of the Open Hymnal Project, 2010 Revision.</i></p>
<hr/>
<h3>Great Is Thy Faithfulness</h3>
<p>
1. Great is Thy faithfulness, O God my Father; There is no shadow of turning with Thee;
</p><p><i>Words: Thomas O. Chisholm, 1923.<br/>copyright: public domain.  This score is a part of the Open Hymnal Project, 2025 Revision.</i></p>
<hr/>
<h3>Author of All Life</h3>
<p>
1. Author of all life, Creator of all things,
</p><p><i>Words: Brian J. Dumont, 2009.<br/>copyright: Words: Copyright 2009, Brian J. Dumont. These lyrics may be freely reproduced or published for Christian worship, provided they are not altered, and this notice is on each copy.</i></p>
"""

PEOPLE = """
<p>Chisholm, Thomas Obediah (1866-1960)</p><p>Great Is Thy Faithfulness</p><p>(as author)</p>
<p>Spafford, Horatio Gates (1828-1888)</p><p>It Is Well With My Soul</p><p>(as author)</p>
"""

SCORE = """\
%OHMETRICAL 11 8 11 9
%OHAUTHOR Spafford, Horatio Gates (1828-1888)
%OHTRANSLATOR none
X: 1
T: It Is Well With My Soul
C: Words: Horatio G. Spafford, 1873.  Music and Setting: 'It Is Well' Philip P. Bliss, 1876.
C: copyright: public domain.  This score is a part of the Open Hymnal Project, 2010 Revision.
K: D
[V: S1V1] A | A2 G F | F2 E F
w: 1.~When peace, like a ri- ver, at- ten- * deth my way, When sor- rows like sea bil- lows roll; What- ev- er my lot, Thou has taught me to say, It is well, it is well, with my soul.
"""

SITE = {
    "genindex.html": GENINDEX,
    "alllyrics.html": ALLLYRICS,
    "people.html": PEOPLE,
    "Abc/It_Is_Well-Ville_Du_Havre.abc": SCORE,
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    fetched: list[str] = []

    def fake_fetch(path: str, timeout: float = 30.0) -> bytes:
        fetched.append(path)
        if path not in SITE:
            raise OSError(f"404 {path}")
        return SITE[path].encode()

    monkeypatch.setattr(openhymnal, "_fetch", fake_fetch)
    monkeypatch.setattr(openhymnal, "_cache_path", lambda: tmp_path / "index.json")
    return fetched


def test_index_carries_status_first_line_and_other_titles(site):
    entries, _ = openhymnal.load_index()
    by_title = {e.title: e for e in entries}
    well = by_title["It Is Well With My Soul"]
    assert well.status == "public_domain"
    assert well.first_line.startswith("When peace, like a river")
    assert "When Peace Like a River" in well.also_known_as
    assert by_title["Great Is Thy Faithfulness"].status == "public_domain_us"
    assert by_title["Author of All Life"].status == "free_for_worship"


def test_search_matches_other_titles_first_lines_and_authors(site):
    entries, _ = openhymnal.load_index()
    assert [e.title for e in openhymnal.search(entries, "peace like a river")] == [
        "It Is Well With My Soul"
    ]
    assert [e.title for e in openhymnal.search(entries, "chisholm")] == [
        "Great Is Thy Faithfulness"
    ]
    assert len(openhymnal.search(entries, "")) == 3


def test_the_index_is_cached_and_used_offline(site, monkeypatch):
    openhymnal.load_index()
    assert len(site) == 3
    openhymnal.load_index()
    assert len(site) == 3  # fresh cache: no fetch

    def offline(path: str, timeout: float = 30.0) -> bytes:
        raise OSError("offline")

    monkeypatch.setattr(openhymnal, "_fetch", offline)
    entries, _ = openhymnal.load_index(refresh=True)  # can't refresh: stale beats nothing
    assert len(entries) == 3


def test_offline_with_no_cache_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(openhymnal, "_cache_path", lambda: tmp_path / "none.json")
    monkeypatch.setattr(openhymnal, "_fetch", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        openhymnal.load_index()


def test_fetch_hymn_refuses_anything_but_a_hymn_name(site):
    with pytest.raises(ValueError):
        openhymnal.fetch_hymn("../../etc/passwd")
    assert site == []


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


async def test_search_and_get_handlers(site):
    ws = _FakeWS()
    await server_hymns._handle_search_hymns(ws, SearchHymnsRequest(id=1, query="well"))
    result = ws.sent[0]["results"][0]
    assert result["hymn_id"] == "It_Is_Well-Ville_Du_Havre"
    assert result["status_label"] == "Public domain"

    await server_hymns._handle_get_hymn(ws, GetHymnRequest(id=2, hymn_id=result["hymn_id"]))
    hymn = ws.sent[1]["hymn"]
    assert hymn["body_text"].startswith("[verse 1]\nWhen peace, like a river, attendeth my way,\n")
    assert hymn["copyright"] == "Words: Horatio G. Spafford, 1873. Public domain."
    assert hymn["author"] == "Horatio Gates Spafford"
    assert hymn["source_url"] == "http://openhymnal.org/Lyrics/It_Is_Well-Ville_Du_Havre.html"


def test_a_us_only_hymn_says_so_in_its_notice():
    score = (
        "%OHAUTHOR Chisholm, Thomas Obediah (1866-1960)\nX: 1\nT: Great Is Thy Faithfulness\n"
        "C: Words: Thomas O. Chisholm, 1923.\nC: copyright: public domain.\nK: C\n"
        "[V: S1V1] C D E\nw: Great is Thy faith- ful- ness\n"
    )
    hymn = openhymnal.hymn_to_song("Great_Is_Thy_Faithfulness", score)
    assert hymn.status == "public_domain_us"
    assert hymn.copyright == "Words: Thomas O. Chisholm, 1923. Public domain in the US."


async def test_handlers_report_offline_as_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(openhymnal, "_cache_path", lambda: tmp_path / "none.json")

    def offline(path: str, timeout: float = 30.0) -> bytes:
        raise OSError("offline")

    monkeypatch.setattr(openhymnal, "_fetch", offline)
    ws = _FakeWS()
    await server_hymns._handle_search_hymns(ws, SearchHymnsRequest(id=1))
    assert "Couldn't reach the Open Hymnal" in ws.sent[0]["error"]
    await server_hymns._handle_get_hymn(ws, GetHymnRequest(id=2, hymn_id="It_Is_Well"))
    assert "Couldn't reach the Open Hymnal" in ws.sent[1]["error"]
