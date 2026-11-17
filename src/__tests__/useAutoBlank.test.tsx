/**
 * The screen blanks while nobody is singing — the engine has said so for a
 * long time, and until this hook nothing on screen listened.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { handlers, conn } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  return {
    handlers,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        handlers.set(kind, [...(handlers.get(kind) ?? []), cb]);
        return () => {};
      },
      onStatus: () => () => {},
      request: async () => ({}),
    },
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));

import { useAutoBlank } from "@/lib/presentation/useAutoBlank";
import { resetSingingState } from "@/lib/singingState";

async function connected() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function singing(blanking: boolean) {
  act(() =>
    (handlers.get("singing_state") ?? []).forEach((h) =>
      h({ singing: !blanking, reason: blanking ? "silence" : "singing", blanking, score: 0.9 }),
    ),
  );
}

beforeEach(async () => {
  await connected();
  act(() => resetSingingState());
});

describe("useAutoBlank", () => {
  it("blanks while the engine says nobody is singing, and comes back", () => {
    const { result } = renderHook(() => useAutoBlank("none", true, false));
    expect(result.current).toEqual({ blankMode: "none", auto: false });
    singing(true);
    expect(result.current).toEqual({ blankMode: "black", auto: true });
    singing(false);
    expect(result.current.blankMode).toBe("none");
  });

  it("only while the AI is driving", () => {
    const { result } = renderHook(() => useAutoBlank("none", false, false));
    singing(true);
    expect(result.current.blankMode).toBe("none");
  });

  it("never over a passage a person put up — reading it aloud is the point", () => {
    const { result } = renderHook(() => useAutoBlank("none", true, true));
    singing(true);
    expect(result.current.blankMode).toBe("none");
  });

  it("the operator's own blank wins, and is not reported as automatic", () => {
    const { result } = renderHook(() => useAutoBlank("white", true, false));
    singing(true);
    expect(result.current).toEqual({ blankMode: "white", auto: false });
  });
});
