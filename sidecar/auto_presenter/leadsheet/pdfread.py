"""Vector drawings and text spans from a PDF page, with pdfminer.six (MIT).

The lead-sheet reader (`omr.py`) was developed against PyMuPDF, which is
AGPL and cannot ship in the app. This module gives it the same two views of
a page, built from pdfminer's content-stream interpreter:

* `drawings(page)` — one entry per path-painting operation, like PyMuPDF's
  `page.get_drawings()`: `items` (("l", p1, p2), ("c", p1, c1, c2, p2) or
  ("re", rect)), `rect` (the bounding box of the path's points), `fill`
  (non-None when filled) and `width` (stroke width when stroked).
  Coordinates are PyMuPDF's: points, origin top-left, y down.
* `spans(page)` — one entry per text-showing operation, like the spans of
  PyMuPDF's `page.get_text("dict")`: `text`, `font` (base font name, subset
  prefix dropped), `size`, `bbox`.

Music notation from engravers such as Verovio draws each glyph as its own
path and each lyric syllable with its own text operation, so per-operation
granularity is what the reader needs. pdfminer reports a filled-and-stroked
path once per painting it sees; a fill and a separate stroke of the same
outline are two entries here, as they are in PyMuPDF whenever it does not
merge them, and the reader only ever reads the filled one.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pdfminer.converter import PDFLayoutAnalyzer
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.utils import apply_matrix_pt


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class _Interpreter(PDFPageInterpreter):
    """pdfminer's interpreter with two things PyMuPDF gets right:

    * `re` stays a rectangle instead of becoming four lines;
    * `gs` applies the graphics-state dictionary's line width (`/LW`).
      pdfminer leaves `gs` unimplemented, and SongSelect sets every stroke
      width that way, so without this every staff line has width 1."""

    def init_resources(self, resources):
        super().init_resources(resources)
        from pdfminer.pdftypes import dict_value

        self._extgstate = dict_value(resources.get("ExtGState")) if resources else {}

    def do_gs(self, name):
        from pdfminer.pdftypes import dict_value

        key = getattr(name, "name", name)
        d = dict_value(self._extgstate.get(key)) if self._extgstate else {}
        if "LW" in d:
            self.graphicstate.linewidth = float(d["LW"])

    def do_re(self, x, y, w, h):
        self.curpath.append(("re", float(x), float(y), float(w), float(h)))


class _Recorder(PDFLayoutAnalyzer):
    def __init__(self, rsrc: PDFResourceManager, page_height: float):
        super().__init__(rsrc, pageno=1, laparams=None)
        self.h = page_height
        self.drawings: list[dict] = []
        self.spans: list[dict] = []
        self._chars: list = []

    # -- paths
    def _pt(self, x: float, y: float) -> Point:
        px, py = apply_matrix_pt(self.ctm, (x, y))
        return Point(px, self.h - py)

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        items: list[tuple] = []
        pts: list[Point] = []
        start = cur = None
        for seg in path:
            op = seg[0]
            if op == "m":
                start = cur = self._pt(seg[1], seg[2])
                pts.append(cur)
            elif op == "l":
                p = self._pt(seg[1], seg[2])
                items.append(("l", cur, p))
                pts.append(p)
                cur = p
            elif op in ("c", "v", "y"):
                xs = [float(v) for v in seg[1:]]
                ctrl = [self._pt(xs[i], xs[i + 1]) for i in range(0, len(xs), 2)]
                if op == "v":  # first control point is the current point
                    ctrl = [cur, *ctrl]
                elif op == "y":  # second control point is the end point
                    ctrl = [ctrl[0], ctrl[1], ctrl[1]]
                items.append(("c", cur, ctrl[0], ctrl[1], ctrl[2]))
                pts.extend(ctrl)
                cur = ctrl[2]
            elif op == "h":
                if (
                    cur is not None
                    and start is not None
                    and abs(cur.x - start.x) + abs(cur.y - start.y) > 1e-3
                ):
                    items.append(("l", cur, start))
                cur = start
            elif op == "re":
                x, y, w, hh = seg[1:]
                corners = [
                    self._pt(x, y),
                    self._pt(x + w, y),
                    self._pt(x + w, y + hh),
                    self._pt(x, y + hh),
                ]
                r = Rect(
                    min(p.x for p in corners),
                    min(p.y for p in corners),
                    max(p.x for p in corners),
                    max(p.y for p in corners),
                )
                items.append(("re", r))
                pts.extend(corners)
                start = cur = corners[0]
        if not pts:
            return
        a, b, c, d = self.ctm[:4]
        scale = (abs(a * d - b * c)) ** 0.5
        self.drawings.append(
            {
                "items": items,
                "rect": Rect(
                    min(p.x for p in pts),
                    min(p.y for p in pts),
                    max(p.x for p in pts),
                    max(p.y for p in pts),
                ),
                "fill": True if fill else None,
                "width": gstate.linewidth * scale if stroke else None,
            }
        )

    # -- text: one span per text-showing operation and font
    def render_string(self, textstate, seq, ncs, graphicstate):
        self._chars = []
        super().render_string(textstate, seq, ncs, graphicstate)
        if self._chars:
            self._flush(textstate)

    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
        from pdfminer.layout import LTChar

        try:
            text = font.to_unichr(cid)
        except Exception:  # pdfminer raises when a font has no mapping for the cid
            text = ""
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            font.char_width(cid),
            font.char_disp(cid),
            ncs,
            graphicstate,
        )
        # pdfminer makes every glyph box exactly one font size tall; PyMuPDF
        # spans run from the font's descender to its ascender. A music font's
        # are far apart (Leipzig: about 3 em), and the reader joins a chord
        # letter to its sharp or flat only when their boxes overlap.
        em = font.get_ascent() - font.get_descent()
        item.top = item.y0 + item.size * em if em > 0 else item.y1
        self._chars.append(item)
        return item.adv

    def _flush(self, textstate):
        chars = self._chars
        name = chars[0].fontname
        name = name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name
        x0 = min(ch.x0 for ch in chars)
        x1 = max(ch.x1 for ch in chars)
        y0 = min(ch.y0 for ch in chars)
        y1 = max(ch.top for ch in chars)
        span = {
            "text": "".join(ch.get_text() for ch in chars),
            "font": name,
            "size": max(ch.size for ch in chars),
            "bbox": (x0, self.h - y1, x1, self.h - y0),
        }
        # Some generators (SongSelect's, via IronPdf) draw every letter with
        # its own text operation, and a space as a space glyph. PyMuPDF joins
        # glyphs that touch on one baseline into one span; so do we, or a
        # syllable reads "L e t". Glyphs of the same font and size on the same
        # baseline that meet, or overlap by a kerning pair's amount ("Wo"), or
        # sit a hair apart (a comma), are one span; a word gap is many times
        # wider.
        prev = self.spans[-1] if self.spans else None
        if (
            prev is not None
            and prev["font"] == span["font"]
            and abs(prev["size"] - span["size"]) < 0.05
            and abs(prev["bbox"][3] - span["bbox"][3]) < 0.3
            and -0.3 * span["size"] < span["bbox"][0] - prev["bbox"][2] < 0.1 * span["size"]
        ):
            prev["text"] += span["text"]
            b0, b1 = prev["bbox"], span["bbox"]
            prev["bbox"] = (
                min(b0[0], b1[0]),
                min(b0[1], b1[1]),
                max(b0[2], b1[2]),
                max(b0[3], b1[3]),
            )
        else:
            self.spans.append(span)


@dataclass
class Page:
    drawings: list[dict]
    spans: list[dict]
    width: float
    height: float


def read_pages(pdf: bytes) -> list[Page]:
    """Every page's drawings and text spans."""
    rsrc = PDFResourceManager()
    out: list[Page] = []
    for page in PDFPage.get_pages(BytesIO(pdf)):
        x0, y0, x1, y1 = page.mediabox
        dev = _Recorder(rsrc, y1)
        _Interpreter(rsrc, dev).process_page(page)
        out.append(Page(dev.drawings, dev.spans, x1 - x0, y1 - y0))
    return out
