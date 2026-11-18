import { useEffect, useState } from "react";

import type { HostedRoomState } from "@/lib/band/hostedRoom";

/** How long the hosted room can be out of reach before saying so (a
 * reconnect usually takes a second or two). */
const QUIET_FOR_MS = 8_000;

/**
 * One quiet line in the operator's top bar, only when the organization's
 * hosted live room can't be reached: the band's devices on this building's
 * network carry on through this computer meanwhile.
 */
export function LiveSetStatus({ hosted }: { hosted: HostedRoomState | undefined }) {
  const down = hosted !== undefined && hosted.connection !== "live";
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!down) {
      setShow(false);
      return;
    }
    const t = setTimeout(() => setShow(true), QUIET_FOR_MS);
    return () => clearTimeout(t);
  }, [down]);
  if (!show) return null;
  return (
    <span
      className="text-xs text-amber-500"
      title="The live room on the internet can't be reached. Band devices on this building's network can connect to this computer directly; everyone moves back when it's reachable again."
      data-testid="live-set-offline"
    >
      Live set offline · band on this network
    </span>
  );
}
