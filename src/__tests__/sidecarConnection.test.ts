import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

const connectSidecar = vi.fn();
const getSidecarPort = vi.fn();

vi.mock("@/lib/ws", () => ({ connectSidecar: (p: number) => connectSidecar(p) }));
vi.mock("@/lib/tauri", () => ({ getSidecarPort: () => getSidecarPort() }));

import {
  getSharedConnection,
  closeSharedConnection,
} from "@/lib/sidecarConnection";

function fakeConn() {
  return { port: 1, status: "open", close: vi.fn() } as unknown as Awaited<
    ReturnType<typeof getSharedConnection>
  >;
}

beforeEach(() => {
  connectSidecar.mockReset();
  getSidecarPort.mockReset();
  getSidecarPort.mockResolvedValue(1420);
});

afterEach(() => {
  closeSharedConnection();
});

describe("getSharedConnection", () => {
  it("opens exactly one connection shared across many callers", async () => {
    const conn = fakeConn();
    connectSidecar.mockResolvedValue(conn);

    const [a, b, c] = await Promise.all([
      getSharedConnection(),
      getSharedConnection(),
      getSharedConnection(),
    ]);

    expect(a).toBe(conn);
    expect(b).toBe(conn);
    expect(c).toBe(conn);
    // Only one underlying socket was opened despite three callers.
    expect(connectSidecar).toHaveBeenCalledTimes(1);

    // A later caller still reuses the same connection.
    expect(await getSharedConnection()).toBe(conn);
    expect(connectSidecar).toHaveBeenCalledTimes(1);
  });

  it("retries (does not cache the failure) when the first connect fails", async () => {
    connectSidecar.mockRejectedValueOnce(new Error("sidecar offline"));
    await expect(getSharedConnection()).rejects.toThrow("sidecar offline");

    const conn = fakeConn();
    connectSidecar.mockResolvedValue(conn);
    expect(await getSharedConnection()).toBe(conn);
    expect(connectSidecar).toHaveBeenCalledTimes(2);
  });

  it("closeSharedConnection closes the socket and forces a fresh connect", async () => {
    const first = fakeConn();
    connectSidecar.mockResolvedValue(first);
    await getSharedConnection();

    closeSharedConnection();
    // Let the deferred close() run.
    await Promise.resolve();
    expect(first.close).toHaveBeenCalled();

    const second = fakeConn();
    connectSidecar.mockResolvedValue(second);
    expect(await getSharedConnection()).toBe(second);
    expect(connectSidecar).toHaveBeenCalledTimes(2);
  });
});
