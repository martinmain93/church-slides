/**
 * Operator-side projection controller.
 *
 * Wraps the imperative `projection.ts` helpers in a React hook the operator
 * view consumes: open/close the audience window, toggle its fullscreen, push
 * the current slide whenever it changes, and answer the audience window's
 * "send me the current state" request after it (re)opens.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  closeAudienceWindow,
  emitSlide,
  isAudienceOpen,
  onAudienceRequest,
  openAudienceWindow,
  toggleAudienceFullscreen,
  type SlidePayload,
} from "@/lib/presentation/projection";

export interface ProjectionApi {
  open: boolean;
  /** The audience window is fullscreen on a monitor of its own. */
  placed: boolean;
  openAudience: () => Promise<void>;
  closeAudience: () => Promise<void>;
  toggleFullscreen: () => Promise<void>;
}

export function useAudienceProjection(
  payload: SlidePayload,
  /** Open the audience window at mount, fullscreen on a spare monitor if
   * there is one — nobody should have to find a button to start projecting. */
  { autoOpen = false }: { autoOpen?: boolean } = {},
): ProjectionApi {
  const [open, setOpen] = useState(false);
  const [placed, setPlaced] = useState(false);

  // Latest payload, so the request-state handler always re-emits what's
  // current rather than a value captured when the listener was registered.
  const latestPayload = useRef(payload);
  latestPayload.current = payload;

  // Reconcile with any audience window that survived a reload — or open one.
  useEffect(() => {
    let cancelled = false;
    void isAudienceOpen().then(async (already) => {
      if (cancelled) return;
      if (already || !autoOpen) {
        setOpen(already);
        return;
      }
      try {
        const fullscreen = await openAudienceWindow(true);
        if (cancelled) return;
        setOpen(true);
        setPlaced(fullscreen);
      } catch (err) {
        console.error("could not open the audience screen:", err);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, at mount
  }, []);

  // Push the current slide to the audience window on every change.
  useEffect(() => {
    if (!open) return;
    void emitSlide(payload);
  }, [payload, open]);

  // Re-send current state when the audience window asks (e.g. just opened).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void onAudienceRequest(() => {
      void emitSlide(latestPayload.current);
    }).then((un) => {
      unlisten = un;
    });
    return () => unlisten?.();
  }, []);

  // Tear the window down if the operator view unmounts (service ends).
  useEffect(() => {
    return () => {
      void closeAudienceWindow();
    };
  }, []);

  const openAudience = useCallback(async () => {
    setPlaced(await openAudienceWindow(true));
    setOpen(true);
  }, []);

  const closeAudience = useCallback(async () => {
    await closeAudienceWindow();
    setOpen(false);
    setPlaced(false);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    await toggleAudienceFullscreen();
  }, []);

  return { open, placed, openAudience, closeAudience, toggleFullscreen };
}
