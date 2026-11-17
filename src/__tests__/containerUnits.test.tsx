import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useContainerUnits } from "@/lib/containerUnits";

// jsdom has no layout, so drive the fallback's ResizeObserver by hand. The
// equivalence it relies on — 1cqw is 1% of the observed content-box width —
// was checked against native `cqw` in a real browser at four widths.
let observed: ((width: number) => void) | null = null;
class FakeResizeObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe() {
    observed = (width) =>
      this.cb([{ contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  disconnect() {
    observed = null;
  }
}

function Probe({ onUnits }: { onUnits: (s: string) => void }) {
  const { ref, cqw } = useContainerUnits<HTMLDivElement>(true);
  onUnits(cqw(5));
  return <div ref={ref} />;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useContainerUnits fallback", () => {
  it("converts cqw to px from the container's measured width", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    let last = "";
    render(<Probe onUnits={(s) => (last = s)} />);
    act(() => observed?.(1184));
    expect(last).toBe("59.2px");
  });

  it("uses vw until the container has a real width, never 0px", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    let last = "";
    render(<Probe onUnits={(s) => (last = s)} />);
    act(() => observed?.(0));
    expect(last).toBe("5vw");
  });
});
