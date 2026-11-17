/**
 * `useAudioDevices()` — fetch the list of input-capable audio devices
 * from the Python sidecar.
 *
 * The sidecar enumerates via sounddevice/PortAudio. If sounddevice can't
 * load (missing native lib, no audio subsystem), the sidecar returns an
 * empty list — the UI surfaces "no microphone detected" rather than
 * crashing.
 *
 * In browser dev mode (no Tauri shell), we don't have a sidecar — return
 * empty + an error flag so the picker can suggest "launch the desktop
 * app to configure audio".
 */

import { useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import type {
  AudioInputDeviceInfo,
} from "@/types/generated";

export type AudioDevicesState =
  | { kind: "loading" }
  | { kind: "ready"; devices: AudioInputDeviceInfo[] }
  | { kind: "error"; message: string };

export function useAudioDevices(reloadKey: number = 0): AudioDevicesState {
  const [state, setState] = useState<AudioDevicesState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    requestSidecar({ kind: "list_audio_devices" })
      .then((reply) => {
        if (cancelled) return;
        setState({ kind: "ready", devices: reply.devices });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return state;
}
