import { render, screen, cleanup, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockHealth = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/pipelineHealth", () => ({
  usePipelineHealth: () => mockHealth(),
}));

const mockEngine = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/engineTelemetry", () => ({
  useEngineTelemetry: () => mockEngine(),
}));

import { PerfTab } from "@/components/debug/PerfTab";

const healthy = {
  lagSeconds: 0.2,
  droppedChunks: 0,
  healthy: true,
  cpuPercent: 35,
  rssMb: 240,
  asrMs: 320,
  matcherMs: 7.1,
  scorerMs: 0,
  scorerSpans: 0,
};

const engine = {
  secondsRemaining: 4.0,
  leadTimeMs: 3920,
  advancing: false,
  targetSlideIdx: 2,
  position: 0.35,
  wordsOnSlide: 12,
  rateWps: 1.15,
  latencyMedianMs: 2850,
  latencyP90Ms: 8780,
  latencySamples: 418,
  endingProbability: null,
  endingProbabilityBar: 0.7,
  statesOnSlide: 12,
  stalledSeconds: null,
  stallAfterSeconds: 16,
  leadLagMs: 2810,
  leadGapMs: 1110,
  targetMarginMs: 0,
  slidesAhead: 1,
  pendingItemIdx: null,
  pendingItemObservations: 0,
};

afterEach(() => {
  cleanup();
  mockHealth.mockReset();
  mockHealth.mockReturnValue(null);
  mockEngine.mockReset();
  mockEngine.mockReturnValue(null);
});

describe("PerfTab", () => {
  it("reports cost while a session runs", () => {
    mockHealth.mockReturnValue(healthy);
    render(<PerfTab />);
    expect(screen.getByText("320 ms")).toBeInTheDocument();
    expect(screen.getByText("7.1 ms")).toBeInTheDocument();
    expect(screen.getByText("35 %")).toBeInTheDocument();
    expect(screen.getByText("240 MB")).toBeInTheDocument();
  });

  it("warns before the ASR budget is blown, not after", () => {
    // Blowing the budget means audio gets discarded, so the useful moment to
    // say something is while there's still headroom to act on.
    mockHealth.mockReturnValue({ ...healthy, asrMs: 800 });
    render(<PerfTab />);
    expect(screen.getByText(/smaller model/i)).toBeInTheDocument();
  });

  it("stays quiet when there is comfortable headroom", () => {
    mockHealth.mockReturnValue({ ...healthy, asrMs: 300 });
    render(<PerfTab />);
    expect(screen.queryByText(/smaller model/i)).toBeNull();
  });

  it("calls out discarded audio, which is never normal", () => {
    mockHealth.mockReturnValue({ ...healthy, droppedChunks: 42, healthy: false });
    render(<PerfTab />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });
});

describe("PerfTab engine readout", () => {
  it("shows the countdown, tempo and lag together", () => {
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue(engine);
    render(<PerfTab />);

    expect(screen.getByText(/^4\.0 s$/)).toBeInTheDocument();
    expect(screen.getByText("1.15 w/s")).toBeInTheDocument();
    expect(screen.getByText("2.9 s")).toBeInTheDocument();
    // The tail is the part a median hides — 2.85 s median against an 8.78 s
    // p90 was the real measurement that motivated reporting both.
    expect(screen.getByText(/p90 8780 ms over 418 words/)).toBeInTheDocument();
  });

  it("reports a real block only when the engine itself saw the countdown elapse", () => {
    // The engine's own seconds_remaining is under the lead time and it still
    // refused to step: that is genuinely `order_is_predictive` saying no.
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({ ...engine, secondsRemaining: 0.2, advancing: false });
    render(<PerfTab />);
    expect(screen.getByText(/order is not predictive here/i)).toBeInTheDocument();
  });

  it("does not blame order when only the local ticker ran past the threshold", () => {
    // The regression this exists for, and it only reproduces once time has
    // actually passed. The countdown ticks at 10 Hz; belief updates arrive
    // only as words do. Between them the displayed number dips under the lead
    // time while the engine's last word on `advancing` predates the crossing —
    // an innocent "not yet", which the panel reported as `order_is_predictive`
    // refusing to step. Singing straight through a song in order, that
    // accusation is simply false.
    vi.useFakeTimers();
    try {
      mockHealth.mockReturnValue(healthy);
      mockEngine.mockReturnValue({
        ...engine,
        leadTimeMs: 1500,
        secondsRemaining: 3.0, // engine: still well outside the 1.5 s lead
        advancing: false,
      });
      const { container } = render(<PerfTab />);
      expect(container.textContent).toMatch(/3\.0 s/);

      // No new belief update; only the local ticker moves. 2 s later the
      // displayed countdown is ~1.0 s, under the 1.5 s lead.
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(container.textContent).toMatch(/1\.0 s/);
      expect(container.textContent).not.toMatch(/order is not predictive/i);
      // It should say what is really happening instead.
      expect(container.textContent).toMatch(/waiting on the next words/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the lead as its parts, not just a total", () => {
    // The lag warning this replaces is now structurally impossible — the lag
    // is a term in the lead. What matters instead is which term moved.
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue(engine);
    render(<PerfTab />);
    expect(screen.getByText("3.9 s")).toBeInTheDocument();
    expect(screen.getByText(/lag 2\.8s \+ gap 1\.1s \+ margin 0\.0s/)).toBeInTheDocument();
  });

  it("says when it is reaching past a slide too short to hit", () => {
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({ ...engine, slidesAhead: 2 });
    render(<PerfTab />);
    expect(screen.getByText(/Projecting 2 slides ahead/)).toBeInTheDocument();
  });

  it("says nothing rather than zero before anything has been measured", () => {
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({
      ...engine,
      secondsRemaining: null,
      rateWps: null,
      latencyMedianMs: null,
      latencySamples: 0,
    });
    render(<PerfTab />);
    // A zeroed lag reads as "instant", which is the opposite of the truth.
    expect(screen.queryByText("0.0 s")).toBeNull();
    expect(screen.getAllByText("—").length).toBe(3);
  });

  it("still reports the engine while the audio session is silent", () => {
    // Pipeline health only flows while a session runs; the engine's own
    // numbers come from the matcher and must not be gated behind it.
    mockHealth.mockReturnValue(null);
    mockEngine.mockReturnValue(engine);
    render(<PerfTab />);
    expect(screen.getByText("1.15 w/s")).toBeInTheDocument();
  });
});

describe("PerfTab engine internals", () => {
  it("shows how split the belief is, not just the countdown", () => {
    // A countdown cannot distinguish "nearly ready" from "half the belief says
    // this slide is ending and half says it barely started".
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({ ...engine, endingProbability: 0.42 });
    render(<PerfTab />);
    expect(screen.getByText("42 %")).toBeInTheDocument();
    expect(screen.getByText(/share of 12 states/)).toBeInTheDocument();
  });

  it("says when there is no distribution to weigh", () => {
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({
      ...engine, endingProbability: 1.0, statesOnSlide: 1,
    });
    render(<PerfTab />);
    expect(screen.getByText(/no distribution to weigh/)).toBeInTheDocument();
  });

  it("counts a freeze forming, before the watchdog fires", () => {
    // The display stalling is invisible until it has already stalled; this is
    // the same event in slow motion.
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({ ...engine, stalledSeconds: 11.2 });
    render(<PerfTab />);
    expect(screen.getByText("11.2 s")).toBeInTheDocument();
    expect(screen.getByText(/overridden at 16 s/)).toBeInTheDocument();
  });

  it("surfaces a song change being held at the cross-song bar", () => {
    // Previously this state was invisible and the jump simply happened.
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue({
      ...engine, pendingItemIdx: 2, pendingItemObservations: 1,
    });
    render(<PerfTab />);
    expect(screen.getByText("item 2 (1)")).toBeInTheDocument();
  });

  it("stays out of the way when none of it is happening", () => {
    mockHealth.mockReturnValue(healthy);
    mockEngine.mockReturnValue(engine);
    render(<PerfTab />);
    expect(screen.queryByText(/Belief says ending/)).toBeNull();
    expect(screen.queryByText(/Screen ignoring the belief/)).toBeNull();
    expect(screen.queryByText(/Another song proposed/)).toBeNull();
  });

  it("shows the score follower's pass time beside ASR and the matcher", () => {
    mockHealth.mockReturnValue({ ...healthy, scorerMs: 42, scorerSpans: 7 });
    render(<PerfTab />);
    expect(screen.getByText("Score follower per pass")).toBeInTheDocument();
    expect(screen.getByText("42 ms")).toBeInTheDocument();
    expect(screen.getByText(/7 spans/)).toBeInTheDocument();
  });

  it("says when the score follower isn't running", () => {
    mockHealth.mockReturnValue(healthy);
    render(<PerfTab />);
    expect(screen.getByText(/not running/)).toBeInTheDocument();
  });
});
