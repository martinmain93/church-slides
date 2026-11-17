import { describe, expect, it } from "vitest";

import {
  activeMarkerAt,
  clampTime,
  clampZoom,
  formatTime,
  fractionToTime,
  insertMarker,
  isDirty,
  isOutsideView,
  MAX_ZOOM,
  MIN_ZOOM,
  newMarkerId,
  nextMarkerAt,
  removeMarker,
  retargetMarker,
  retimeMarker,
  slideLabel,
  slidePreview,
  sortMarkers,
  timeToFraction,
  timeToViewFraction,
  toEvents,
  toMarkers,
  viewFractionToTime,
  viewWindow,
} from "@/lib/recordings/timeline";
import type { GroundTruthEvent, SetlistItemSnapshot } from "@/types/generated";

function gt(t: number, item = 0, slide = 0): GroundTruthEvent {
  return { t_seconds: t, item_idx: item, slide_idx: slide };
}

describe("time <-> position conversions", () => {
  it("clamps to [0, duration]", () => {
    expect(clampTime(-5, 10)).toBe(0);
    expect(clampTime(15, 10)).toBe(10);
    expect(clampTime(4, 10)).toBe(4);
    expect(clampTime(5, 0)).toBe(0);
  });

  it("round-trips time <-> fraction", () => {
    expect(timeToFraction(5, 10)).toBe(0.5);
    expect(fractionToTime(0.5, 10)).toBe(5);
    expect(timeToFraction(5, 0)).toBe(0);
  });
});

describe("marker reducers", () => {
  const markers = toMarkers([gt(5, 0, 1), gt(1, 0, 0)]);

  it("toMarkers assigns ids; toEvents strips them and sorts", () => {
    expect(markers.every((m) => typeof m.id === "string")).toBe(true);
    expect(toEvents(markers).map((e) => e.t_seconds)).toEqual([1, 5]);
  });

  it("sortMarkers orders by time without mutating input", () => {
    const sorted = sortMarkers(markers);
    expect(sorted.map((m) => m.t_seconds)).toEqual([1, 5]);
    expect(markers.map((m) => m.t_seconds)).toEqual([5, 1]);
  });

  it("insertMarker keeps chronological order", () => {
    const added = insertMarker(markers, {
      id: newMarkerId(),
      t_seconds: 3,
      item_idx: 0,
      slide_idx: 9,
    });
    expect(added.map((m) => m.t_seconds)).toEqual([1, 3, 5]);
  });

  it("removeMarker drops by id", () => {
    const id = markers[0]!.id;
    expect(removeMarker(markers, id).some((m) => m.id === id)).toBe(false);
  });

  it("retimeMarker clamps and re-sorts", () => {
    const id = markers.find((m) => m.t_seconds === 5)!.id;
    const out = retimeMarker(markers, id, 0.2, 10);
    expect(out.map((m) => m.t_seconds)).toEqual([0.2, 1]);
    const clamped = retimeMarker(markers, id, 999, 10);
    expect(clamped.find((m) => m.id === id)!.t_seconds).toBe(10);
  });

  it("retargetMarker changes item/slide only", () => {
    const id = markers[0]!.id;
    const out = retargetMarker(markers, id, 2, 3);
    const m = out.find((x) => x.id === id)!;
    expect([m.item_idx, m.slide_idx]).toEqual([2, 3]);
    expect(m.t_seconds).toBe(markers[0]!.t_seconds);
  });
});

describe("isDirty", () => {
  const original = [gt(1, 0, 0), gt(5, 0, 1)];

  it("is false for an unchanged round-trip", () => {
    expect(isDirty(original, toMarkers(original))).toBe(false);
  });

  it("is true after retime, retarget, add, or remove", () => {
    const base = toMarkers(original);
    const id = base[0]!.id;
    expect(isDirty(original, retimeMarker(base, id, 2, 10))).toBe(true);
    expect(isDirty(original, retargetMarker(base, id, 1, 1))).toBe(true);
    expect(
      isDirty(
        original,
        insertMarker(base, { id: newMarkerId(), t_seconds: 3, item_idx: 0, slide_idx: 0 }),
      ),
    ).toBe(true);
    expect(isDirty(original, removeMarker(base, id))).toBe(true);
  });
});

describe("slideLabel", () => {
  const content: SetlistItemSnapshot[] = [
    { item_idx: 0, song_id: "s0", slides: [[], ["there", "is", "none"]], slide_sections: [] },
    { item_idx: 1, song_id: "s1", slides: [["holy", "holy"]], slide_sections: [] },
  ];

  it("builds 1-based title + token preview", () => {
    const label = slideLabel(content, 0, 1);
    expect(label.title).toBe("Item 1 · Slide 2");
    expect(label.preview).toBe("there is none");
  });

  it("degrades gracefully for out-of-range indices", () => {
    const label = slideLabel(content, 9, 9);
    expect(label.title).toBe("Item 10 · Slide 10");
    expect(label.preview).toBe("");
  });
});

describe("slidePreview", () => {
  const slides = [[], ["there", "is", "none", "like", "you", "no", "one"]];

  it("joins the first N tokens of a slide", () => {
    expect(slidePreview(slides, 1, 3)).toBe("there is none");
    expect(slidePreview(slides, 1)).toBe("there is none like you no");
  });

  it("returns '' for blank or out-of-range slides", () => {
    expect(slidePreview(slides, 0)).toBe("");
    expect(slidePreview(slides, 5)).toBe("");
  });
});

describe("formatTime", () => {
  it("formats M:SS.s", () => {
    expect(formatTime(0)).toBe("0:00.0");
    expect(formatTime(5.2)).toBe("0:05.2");
    expect(formatTime(65.5)).toBe("1:05.5");
    expect(formatTime(-3)).toBe("0:00.0");
  });
});

describe("activeMarkerAt / nextMarkerAt", () => {
  const markers = [
    { id: "a", t_seconds: 10, item_idx: 0, slide_idx: 1 },
    { id: "b", t_seconds: 20, item_idx: 0, slide_idx: 2 },
    { id: "c", t_seconds: 30, item_idx: 1, slide_idx: 1 },
  ];

  it("returns null before the first transition", () => {
    expect(activeMarkerAt(markers, 0)).toBeNull();
    expect(activeMarkerAt(markers, 9.99)).toBeNull();
  });

  it("returns the latest marker at or before the playhead", () => {
    expect(activeMarkerAt(markers, 10)?.id).toBe("a");
    expect(activeMarkerAt(markers, 19.9)?.id).toBe("a");
    expect(activeMarkerAt(markers, 20)?.id).toBe("b");
    expect(activeMarkerAt(markers, 999)?.id).toBe("c");
  });

  it("prefers the last marker when several share a timestamp", () => {
    const tied = [
      { id: "x", t_seconds: 5, item_idx: 0, slide_idx: 1 },
      { id: "y", t_seconds: 5, item_idx: 0, slide_idx: 2 },
    ];
    expect(activeMarkerAt(tied, 5)?.id).toBe("y");
  });

  it("finds the next marker strictly after the playhead", () => {
    expect(nextMarkerAt(markers, 0)?.id).toBe("a");
    expect(nextMarkerAt(markers, 10)?.id).toBe("b");
    expect(nextMarkerAt(markers, 29.5)?.id).toBe("c");
    expect(nextMarkerAt(markers, 30)).toBeNull();
  });

  it("handles an empty timeline", () => {
    expect(activeMarkerAt([], 12)).toBeNull();
    expect(nextMarkerAt([], 12)).toBeNull();
  });
});

describe("zoom viewport", () => {
  it("shows the whole recording at zoom 1", () => {
    const v = viewWindow(300, 1, 150);
    expect(v.start).toBe(0);
    expect(v.end).toBe(300);
    expect(v.span).toBe(300);
  });

  it("centres the window on the given time", () => {
    const v = viewWindow(300, 4, 150);
    expect(v.span).toBe(75);
    expect(v.start).toBeCloseTo(112.5);
    expect(v.end).toBeCloseTo(187.5);
  });

  it("butts against the start and end instead of centring", () => {
    const atStart = viewWindow(300, 4, 5);
    expect(atStart.start).toBe(0);
    expect(atStart.end).toBe(75);

    const atEnd = viewWindow(300, 4, 299);
    expect(atEnd.end).toBe(300);
    expect(atEnd.start).toBe(225);
  });

  it("clamps zoom to its bounds", () => {
    expect(clampZoom(0.1)).toBe(MIN_ZOOM);
    expect(clampZoom(1e6)).toBe(MAX_ZOOM);
    expect(clampZoom(Number.NaN)).toBe(MIN_ZOOM);
    expect(clampZoom(8)).toBe(8);
  });

  it("survives a zero-length recording", () => {
    const v = viewWindow(0, 4, 0);
    expect(v).toEqual({ start: 0, end: 0, span: 0 });
    expect(timeToViewFraction(10, v)).toBe(0);
  });

  it("maps time to fraction and back within the window", () => {
    const v = viewWindow(300, 4, 150); // 112.5 .. 187.5
    expect(timeToViewFraction(112.5, v)).toBeCloseTo(0);
    expect(timeToViewFraction(150, v)).toBeCloseTo(0.5);
    expect(timeToViewFraction(187.5, v)).toBeCloseTo(1);
    expect(viewFractionToTime(0.5, v)).toBeCloseTo(150);
    // Round-trip: a click at 30% must land where the marker is drawn.
    expect(timeToViewFraction(viewFractionToTime(0.3, v), v)).toBeCloseTo(0.3);
  });

  it("reports off-window times outside [0,1] so callers can cull them", () => {
    const v = viewWindow(300, 4, 150);
    expect(timeToViewFraction(0, v)).toBeLessThan(0);
    expect(timeToViewFraction(300, v)).toBeGreaterThan(1);
  });

  it("detects a playhead that has left the window", () => {
    const v = viewWindow(300, 4, 150);
    expect(isOutsideView(150, v)).toBe(false);
    expect(isOutsideView(100, v)).toBe(true);
    expect(isOutsideView(200, v)).toBe(true);
    // Margin pulls the trigger inward so playback re-centres before the edge.
    expect(isOutsideView(185, v, 10)).toBe(true);
  });
});
