import { describe, expect, it } from "vitest";

import { KNOBS } from "@/components/debug/TunablesTab";
import { DEFAULT_PARTICLE_FILTER_TUNABLES } from "@/types/generated";

describe("tunable knob bounds", () => {
  // The bug this exists for: `lead_time_ms` was capped at 1000 while its
  // default was 1500. A range input clamps silently, so the slider sat at the
  // cap and the first touch of any knob pushed 1000 to the sidecar and
  // persisted it. The engine ran a lead time nobody chose, and the debug panel
  // faithfully reported that wrong value — making it look configured.
  it.each(KNOBS.map((k) => [k.key, k] as const))(
    "%s can express its own default",
    (key, knob) => {
      const value = DEFAULT_PARTICLE_FILTER_TUNABLES[key] as number;
      expect(value).toBeGreaterThanOrEqual(knob.min);
      expect(value).toBeLessThanOrEqual(knob.max);
    },
  );

  it.each(KNOBS.map((k) => [k.key, k] as const))(
    "%s lands on its own default exactly, without rounding",
    (key, knob) => {
      // A step that skips the default is the same failure one level down: the
      // slider cannot return to the shipped value once moved off it.
      const value = DEFAULT_PARTICLE_FILTER_TUNABLES[key] as number;
      const steps = (value - knob.min) / knob.step;
      expect(Math.abs(steps - Math.round(steps))).toBeLessThan(1e-6);
    },
  );

  it("lets slide timing be nudged either side of the line", () => {
    // It is a margin now, not a lead: the engine adds the measured lag itself,
    // so this only has to span the adjustment a person would actually want.
    // Negative has to be reachable — landing slides deliberately late is a
    // legitimate preference, and the old knob could not express it.
    const timing = KNOBS.find((k) => k.key === "target_margin_ms")!;
    expect(timing.min).toBeLessThanOrEqual(-2000);
    expect(timing.max).toBeGreaterThanOrEqual(2000);
    expect(DEFAULT_PARTICLE_FILTER_TUNABLES.target_margin_ms).toBe(0);
  });
});
