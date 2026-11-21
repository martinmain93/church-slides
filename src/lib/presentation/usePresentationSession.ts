/**
 * The service-level plumbing `PresentationView` and `OperatorView` both need
 * around their own `usePresentationController` instance: applying AI/voice
 * decisions, recording bookkeeping, last-session persistence, and the global
 * keyboard shortcuts. Extracted after the two views had drifted to nearly
 * verbatim copies of ~150 lines of this — the same duplication
 * `shouldApplyDecision` was already pulled out of once, for the rest of it.
 *
 * Each view still owns its own `usePresentationController` (different
 * contexts — an immutable setlist vs. one the operator can mutate live) and
 * its own rendering; this only covers the wiring that doesn't differ between
 * them.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { shouldApplyDecision, useAiDecisions, useLiveAiMode } from "@/lib/aiDecisions";
import { aiOnMode, type AIMode } from "@/lib/aiPrefs";
import {
  exitFullscreen,
  isFullscreen,
  toggleFullscreen,
} from "@/lib/presentation/fullscreen";
import {
  resolveDisplayMode,
  slideAtWord,
  slideOffsets,
  trackFromMessage,
  trackWordAt,
  type DisplayMode,
  type DisplayPref,
  type FlowAnchor,
  type FlowTrack,
} from "@/lib/presentation/flow";
import type { PresentationActions } from "@/lib/presentation/hook";
import type { BlankMode } from "@/lib/presentation/state";
import {
  PRESENTATION_KEYMAP,
  matchKey,
  type PresentationAction,
} from "@/lib/presentation/keymap";
import {
  clearLastSession,
  saveLastSession,
} from "@/lib/presentation/persistence";
import {
  useRecordingSession,
  type RecordingSessionApi,
} from "@/lib/recordingClient";
import {
  heardLine,
  isSurprisingSongJump,
  JUMP_HOLD_WORDS,
  type JumpHoldApi,
} from "@/lib/presentation/jumpHold";
import { aiAnchor, aiKinds, aiMayLeave, type ItemKind } from "@/lib/serviceItems";
import { buildSetlistContent } from "@/lib/setlistContent";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { recordUsage, ServiceTally } from "@/lib/usage";
import { addSetlistItem, getSetlist, markSetlistUsed, type SetlistDetail } from "@/lib/tauri";
import type { SetlistContent } from "@/types/generated";

const NO_KINDS: readonly ItemKind[] = [];

export interface PresentationSessionOptions {
  setlistId: string;
  /** Pre-tokenized slides for the matcher; omit for transcript-only sessions. */
  setlistContent?: SetlistContent | null;
  /** Save the service's audio + slide timeline to disk. The live AI runs
   * either way — this only decides whether a recording is kept. */
  recordingEnabled: boolean;
  itemIdx: number;
  slideIdx: number;
  jumpTo: (itemIdx: number, slideIdx: number) => void;
  /**
   * How many songs the live presentation context currently has. Used to
   * delay jumping to an imported library song until the operator view has
   * actually grown to include it — `jumpTo` clamps to the current context,
   * so firing it before the refresh lands on the last *old* song.
   */
  itemCount?: number;
  /** Persist-and-rebuild the running setlist after an off-setlist import. */
  onSetlistMutated?: (detail: SetlistDetail) => void;
  /**
   * The AI mode the session starts in. Defaults to on (`aiOnMode`); the
   * operator view starts with the AI off and lets the operator switch it on.
   */
  initialAiMode?: AIMode;
  /** Slide vs continuous; `auto` follows the AI. The views pass the stored
   * preference (itself `auto` by default); omitted, the session keeps slides. */
  displayPref?: DisplayPref;
  /** The resolved slides of an item, for placing the AI's word track. */
  slidesFor?: (itemIdx: number) => { words: string[] }[] | undefined;
  /** Which view runs the service, for usage statistics. */
  screen?: "operator" | "single";
  /** Each item's kind, in order. Over a passage or text item the AI waits
   * on the next song (`serviceItems.aiAnchor`); omitted, every item is a song. */
  itemKinds?: readonly ItemKind[];
  /**
   * Single-screen view with a leader's screen: AI jumps to another song wait
   * for the leader (`jumpHold.ts`). Omitted, every AI move applies at once.
   */
  jumpHold?: JumpHoldApi;
  /** An item's title and slide count, for a held jump. */
  songFor?: (itemIdx: number) => { title: string; slideCount: number } | undefined;
}

export interface PresentationSessionApi {
  recording: RecordingSessionApi;
  /** The AI mode to show/reason about live — see `useLiveAiMode`. */
  liveAiMode: AIMode;
  displayMode: DisplayMode;
  /** What a continuous display should follow. Always set, in either mode, so
   * switching to continuous mid-song starts from the current slide. */
  flowAnchor: FlowAnchor;
}

/**
 * Recording session + AI/voice decision application + last-session
 * persistence, all keyed off the current `(itemIdx, slideIdx)`.
 */
export function usePresentationSession(
  opts: PresentationSessionOptions,
): PresentationSessionApi {
  const {
    setlistId, setlistContent, recordingEnabled, itemIdx, slideIdx, jumpTo,
    itemCount = 0, onSetlistMutated, initialAiMode: requestedAiMode,
    displayPref = "slide", slidesFor, screen = "single", itemKinds: shownKinds = NO_KINDS,
    jumpHold, songFor,
  } = opts;
  const jumpHoldRef = useRef(jumpHold);
  jumpHoldRef.current = jumpHold;
  const songForRef = useRef(songFor);
  songForRef.current = songFor;
  const setlistContentRef = useRef(setlistContent);
  setlistContentRef.current = setlistContent;
  // A passage is followed like a song as it is read aloud; a text item still
  // has the AI wait on the next song (docs/done/SERVICE_ITEMS.md phase 2).
  const itemKinds = useMemo(() => aiKinds(shownKinds, true), [shownKinds]);
  const itemKindsRef = useRef(itemKinds);
  itemKindsRef.current = itemKinds;

  // Snapshot at mount — re-running the session on every render would tear
  // down and reopen the WS. `useLiveAiMode` below is what tracks a live
  // switch from Settings; this is only the value the session *starts* at.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately read once
  const initialAiMode = useMemo(() => requestedAiMode ?? aiOnMode(), []);
  const liveAiMode = useLiveAiMode(initialAiMode);
  const recording = useRecordingSession({
    setlistId,
    enabled: true,
    record: recordingEnabled,
    setlistContent,
    // Live, so an engine restarted mid-service comes back in the mode the
    // AI was in, at the slide that's up. Only read at (re)start.
    aiMode: liveAiMode,
    position: aiAnchor(itemKinds, { itemIdx, slideIdx }) ?? { itemIdx, slideIdx },
  });
  // For the import listener below, subscribed once per setlist.
  const recordingRef = useRef(recording);
  recordingRef.current = recording;

  const lastReportedPosition = useRef<{ itemIdx: number; slideIdx: number } | null>(null);
  //: Tracks the most recent position applied from an AI decision so the
  //: recording-notify effect below can tag it `source="ai"` (and not
  //: double-fire it as a manual override).
  const lastAiPosition = useRef<{ itemIdx: number; slideIdx: number } | null>(null);
  //: The same, for a position applied from a *spoken* request.
  const lastVoicePosition = useRef<{ itemIdx: number; slideIdx: number } | null>(
    null,
  );

  // A passage the AI is following scrolls, whatever the display setting: a
  // reader keeps reading with the next verses already in view.
  const readingNow = shownKinds[itemIdx] === "scripture" && liveAiMode === "active";
  const displayMode = readingNow ? "continuous" : resolveDisplayMode(displayPref, liveAiMode);

  useAiDecisions({
    onDecision: (decision) => {
      if (!shouldApplyDecision(decision, liveAiMode)) return;
      // In continuous mode the AI moves the column through its word track;
      // the slide engine's switches would fight it. Voice still applies —
      // it is a human asking for a place.
      if (decision.source === "ai" && displayMode === "continuous") return;
      const pos = { itemIdx: decision.itemIdx, slideIdx: decision.slideIdx };
      if (
        decision.source === "ai" &&
        !aiMayLeave(itemKindsRef.current, positionRef.current.itemIdx, pos)
      ) {
        return;
      }
      const hold = jumpHoldRef.current;
      if (decision.source === "ai" && hold?.active()) {
        const target = songForRef.current?.(pos.itemIdx);
        if (target && heldSongJump(hold, pos.itemIdx, `item:${pos.itemIdx}`, target.title, heardWords.current, () => {
          lastAiPosition.current = pos;
          jumpToRef.current(pos.itemIdx, pos.slideIdx);
        })) {
          return;
        }
      }
      // Anything else that moves the screen replaces a jump still waiting.
      hold?.drop();
      // Claimed by its source, so the recording says what moved the screen.
      // A spoken move used to be left unclaimed and saved as "manual",
      // which hid every voice request among the operator's clicks.
      if (decision.source === "ai") lastAiPosition.current = pos;
      else lastVoicePosition.current = pos;
      jumpTo(decision.itemIdx, decision.slideIdx);
    },
  });

  // The leader said no to a held jump: tell the AI the screen stayed put, as
  // a hand on the keyboard would, so it re-anchors here.
  const stayHere = () => {
    const anchor = aiAnchor(itemKindsRef.current, positionRef.current);
    if (anchor) recordingRef.current.notifySlideChange({ ...anchor, source: "manual" });
  };
  /** Offer a jump to another song to the leader; true when it now waits. */
  const heldSongJump = (
    hold: JumpHoldApi,
    toItemIdx: number,
    key: string,
    title: string,
    heard: readonly string[],
    go: () => void,
    stop: () => void = stayHere,
  ): boolean => {
    const from = positionRef.current;
    const here = songForRef.current?.(from.itemIdx);
    if (!isSurprisingSongJump(from, toItemIdx, here?.slideCount ?? 0)) return false;
    return hold.offer({ key, title, words: heardLine(heard), go, stop });
  };
  // The last few words heard, for the leader to see why a jump waits.
  const heardWords = useRef<string[]>([]);
  useEffect(() => {
    if (!jumpHold) return;
    let cancelled = false;
    let unsub = () => {};
    void getSharedConnection()
      .then((conn) => {
        if (cancelled) return;
        unsub = conn.onMessage("transcript_partial", (msg) => {
          if (!msg.is_final) return;
          const words = (msg.words ?? []).map((w) => w.text);
          heardWords.current = heardWords.current.concat(words).slice(-JUMP_HOLD_WORDS);
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      unsub();
    };
  }, [jumpHold !== undefined]); // eslint-disable-line react-hooks/exhaustive-deps

  const heldSongJumpRef = useRef(heldSongJump);
  heldSongJumpRef.current = heldSongJump;

  // Something else moved the screen while a jump waited: that move wins.
  useEffect(() => {
    jumpHoldRef.current?.drop();
  }, [itemIdx, slideIdx]);

  // Off-setlist import: persist first, jump once the context has the new item.
  const pendingImport = useRef<{ itemIdx: number; slideIdx: number; source: "ai" | "voice" } | null>(null);
  const onSetlistMutatedRef = useRef(onSetlistMutated);
  onSetlistMutatedRef.current = onSetlistMutated;
  const jumpToRef = useRef(jumpTo);
  jumpToRef.current = jumpTo;
  const liveAiModeRef = useRef(liveAiMode);
  liveAiModeRef.current = liveAiMode;
  const displayModeRef = useRef(displayMode);
  displayModeRef.current = displayMode;
  const slidesForRef = useRef(slidesFor);
  slidesForRef.current = slidesFor;
  const positionRef = useRef({ itemIdx, slideIdx });
  positionRef.current = { itemIdx, slideIdx };

  // --- continuous mode ---------------------------------------------------
  //
  // The column follows the AI's track while one is live, and the slide the
  // operator (or a spoken request, or an import) picked otherwise. The
  // ordinary (item, slide) position is kept in step with the track, tagged as
  // the AI's, so the operator grid, band screens and recording all still see
  // slides; a position change the track did *not* cause is someone taking
  // over, and re-anchors the column on that slide.
  const [flowAnchor, setFlowAnchor] = useState<FlowAnchor>({
    kind: "slide",
    itemIdx,
    slideIdx,
  });
  const lastFlowPosition = useRef<{ itemIdx: number; slideIdx: number } | null>(null);

  const syncSlideToTrack = (track: FlowTrack) => {
    const slides = slidesForRef.current?.(track.itemIdx);
    if (!slides) return;
    const pos = {
      itemIdx: track.itemIdx,
      slideIdx: slideAtWord(slideOffsets(slides), trackWordAt(track, Date.now())),
    };
    lastFlowPosition.current = pos;
    const cur = positionRef.current;
    if (cur.itemIdx === pos.itemIdx && cur.slideIdx === pos.slideIdx) return;
    lastAiPosition.current = pos;
    jumpToRef.current(pos.itemIdx, pos.slideIdx);
  };
  const syncRef = useRef(syncSlideToTrack);
  syncRef.current = syncSlideToTrack;

  useEffect(() => {
    let cancelled = false;
    let unsub = () => {};
    void getSharedConnection().then((conn) => {
      if (cancelled) return;
      unsub = conn.onMessage("flow_position", (msg) => {
        if (displayModeRef.current !== "continuous") return;
        if (liveAiModeRef.current !== "active") return;
        const track = trackFromMessage(msg, Date.now());
        if (!slidesForRef.current?.(track.itemIdx)) return;
        setFlowAnchor({ kind: "track", track });
        syncRef.current(track);
      });
    }).catch(() => {
      // No sidecar: nothing to follow, and the column follows the slides.
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, []);

  // Between updates the track keeps moving, and can carry the column onto
  // the next slide; keep the slide position in step with it.
  useEffect(() => {
    if (flowAnchor.kind !== "track") return;
    const track = flowAnchor.track;
    const id = window.setInterval(() => syncRef.current(track), 250);
    return () => window.clearInterval(id);
  }, [flowAnchor]);

  // Leaving continuous mode, or the AI stepping back, hands the column back
  // to the slides so a later switch starts where the screen is.
  useEffect(() => {
    if (displayMode === "continuous" && liveAiMode === "active") return;
    setFlowAnchor((prev) =>
      prev.kind === "slide" ? prev : { kind: "slide", ...positionRef.current },
    );
  }, [displayMode, liveAiMode]);

  useEffect(() => {
    const f = lastFlowPosition.current;
    if (f !== null && f.itemIdx === itemIdx && f.slideIdx === slideIdx) return;
    // Forget the track's last slide, or coming back to it by hand later would
    // read as the track's doing and leave the column where it was.
    lastFlowPosition.current = null;
    setFlowAnchor({ kind: "slide", itemIdx, slideIdx });
  }, [itemIdx, slideIdx]);

  useEffect(() => {
    let cancelled = false;
    let unsub = () => {};
    void getSharedConnection().then((conn) => {
      if (cancelled) return;
      unsub = conn.onMessage("import_library_song", (msg) => {
        if (msg.source !== "voice" && liveAiModeRef.current !== "active") return;
        const runImport = () => void (async () => {
          pendingImport.current = {
            itemIdx: msg.item_idx,
            slideIdx: msg.slide_idx,
            // `source` carries a default on the wire, so codegen types it
            // optional; fall back to the same default the Pydantic model
            // declares rather than widening this ref's type.
            source: msg.source ?? "ai",
          };
          try {
            const current = await getSetlist(setlistId);
            const already = current?.items.some((i) => i.song?.id === msg.song_id);
            if (!already) {
              await addSetlistItem(setlistId, msg.song_id);
            }
            const next = await getSetlist(setlistId);
            if (next) {
              onSetlistMutatedRef.current?.(next);
              // The sidecar appended the song to its own index, but its song
              // follower and its ear for the other songs' openings are built
              // from the setlist it's sent. Without this they never learned
              // the new song, and on it "the name of Jesus" switched the
              // screen to another song's "Jesus the name above" (2026-10-17).
              if (!already) {
                recordingRef.current.updateSetlistContent(buildSetlistContent(next), msg.item_idx);
              }
            }
          } catch (err) {
            console.error("import_library_song persist failed:", err);
          }
        })();
        const hold = jumpHoldRef.current;
        if (msg.source !== "voice" && hold?.active()) {
          const held = heldSongJumpRef.current(
            hold,
            msg.item_idx,
            `song:${msg.song_id}`,
            msg.title,
            (msg.query ?? "").split(/\s+/),
            runImport,
            () => {
              // Not added: the AI had already put it in its own copy of the
              // set, so send it the set as it really is.
              const content = setlistContentRef.current;
              if (content) recordingRef.current.updateSetlistContent(content, positionRef.current.itemIdx);
              stayHere();
            },
          );
          if (held) return;
        }
        hold?.drop();
        runImport();
      });
    }).catch(() => {
      // No AI engine (or it isn't up): nothing will ask to import a song, and
      // presenting by hand doesn't need it.
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, [setlistId]);

  useEffect(() => {
    const pending = pendingImport.current;
    if (!pending) return;
    if (itemCount <= pending.itemIdx) return;
    pendingImport.current = null;
    if (pending.source === "ai") {
      lastAiPosition.current = { itemIdx: pending.itemIdx, slideIdx: pending.slideIdx };
    } else {
      // A spoken request is human intent that did not come from the keyboard.
      // Tagging it keeps the display log able to answer "what moved the
      // screen?", which is the first question of every live diagnosis.
      lastVoicePosition.current = {
        itemIdx: pending.itemIdx,
        slideIdx: pending.slideIdx,
      };
    }
    jumpToRef.current(pending.itemIdx, pending.slideIdx);
  }, [itemCount, itemIdx]);

  // Mark the setlist as used so it pops to the top of Recent on Home.
  useEffect(() => {
    void markSetlistUsed(setlistId).catch(() => {
      // non-fatal in browser dev mode
    });
  }, [setlistId]);

  // Persist last-session snapshot on every state change.
  useEffect(() => {
    saveLastSession({ setlistId, itemIdx, slideIdx });
  }, [setlistId, itemIdx, slideIdx]);

  // Clear the snapshot on graceful exit.
  useEffect(() => {
    return () => {
      clearLastSession();
    };
  }, []);

  // Usage statistics (sent only if the user agreed; see `usage.ts`).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- one tally per service
  const tally = useMemo(() => new ServiceTally(screen), []);
  useEffect(() => {
    tally.aiMode(liveAiMode !== "off");
  }, [tally, liveAiMode]);
  useEffect(() => {
    if (displayMode === "continuous") tally.displayContinuous();
  }, [tally, displayMode]);
  useEffect(() => {
    return () => {
      const props = tally.finish();
      if (props) void recordUsage("service", props);
    };
  }, [tally]);

  // Mirror state changes to the active recording (if any). Deduplicate so
  // this only fires when the position actually changes. AI-applied
  // positions are tagged `source="ai"` so the sink records the AI as the
  // originator and the director doesn't loop the change back through
  // `following_human`.
  useEffect(() => {
    const last = lastReportedPosition.current;
    if (last !== null && last.itemIdx === itemIdx && last.slideIdx === slideIdx) {
      return;
    }
    const first = last === null;
    lastReportedPosition.current = { itemIdx, slideIdx };
    const at = (r: { itemIdx: number; slideIdx: number } | null) =>
      r !== null && r.itemIdx === itemIdx && r.slideIdx === slideIdx;
    // "manual" is the fallback, so anything not claimed by the AI or by a
    // spoken request reads as a hand on the keyboard. Claiming precisely is
    // what lets a display log answer "what moved the screen?".
    const source = at(lastAiPosition.current)
      ? "ai"
      : at(lastVoicePosition.current)
        ? "voice"
        : "manual";
    // Over a passage or text item the AI is told the next song's start, so
    // it waits there; the recording's display log therefore shows that too.
    const anchor = aiAnchor(itemKindsRef.current, { itemIdx, slideIdx });
    if (anchor) recording.notifySlideChange({ ...anchor, source });
    // The opening slide isn't a change anyone made.
    tally.slideShown(itemIdx, first ? null : source);
  }, [recording, itemIdx, slideIdx, tally]);

  return { recording, liveAiMode, displayMode, flowAnchor };
}

/**
 * `actions` whose moves — next/previous slide or song, jump to a slide —
 * also lift a blank screen: an operator moving the slide means "show this".
 * For the keyboard and the slide grid only; the AI and the band leader move
 * on under a blank and leave it blank.
 */
export function useLiftBlankOnMove(
  actions: PresentationActions,
  blankMode: BlankMode,
): PresentationActions {
  const blankRef = useRef(blankMode);
  blankRef.current = blankMode;
  return useMemo(() => {
    const lifting =
      <A extends unknown[]>(move: (...args: A) => void) =>
      (...args: A) => {
        const blank = blankRef.current;
        if (blank !== "none") actions.toggleBlank(blank);
        move(...args);
      };
    return {
      ...actions,
      nextSlide: lifting(actions.nextSlide),
      prevSlide: lifting(actions.prevSlide),
      nextSong: lifting(actions.nextSong),
      prevSong: lifting(actions.prevSong),
      jumpTo: lifting(actions.jumpTo),
    };
  }, [actions]);
}

/**
 * The global keyboard shortcuts (`PRESENTATION_KEYMAP`) both views bind to
 * `actions` the same way. `onDispatch`, if given, fires before the action
 * itself runs; it's read from a ref internally so callers don't need to
 * memoize it.
 */
export function usePresentationKeymap(
  actions: PresentationActions,
  onExit: () => void,
  onDispatch?: (action: PresentationAction) => void,
): void {
  const dispatchRef = useRef(onDispatch);
  dispatchRef.current = onDispatch;

  useEffect(() => {
    function dispatch(action: PresentationAction) {
      dispatchRef.current?.(action);
      switch (action) {
        case "nextSlide":
          actions.nextSlide();
          break;
        case "prevSlide":
          actions.prevSlide();
          break;
        case "nextSong":
          actions.nextSong();
          break;
        case "prevSong":
          actions.prevSong();
          break;
        case "toggleBlankBlack":
          actions.toggleBlank("black");
          break;
        case "toggleBlankWhite":
          actions.toggleBlank("white");
          break;
        case "toggleFullscreen":
          void toggleFullscreen();
          break;
        case "exitFullscreenOrPresentation":
          // First Esc exits fullscreen; second exits presentation.
          if (isFullscreen()) {
            void exitFullscreen();
          } else {
            onExit();
          }
          break;
        case "toggleDebug":
          // The AppShell-level debug panel listens for the same key combo
          // independently, so this case is a no-op here.
          break;
      }
    }

    function onKey(ev: KeyboardEvent) {
      // Don't hijack typing inside form fields.
      const target = ev.target;
      if (target instanceof HTMLElement) {
        if (
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable
        ) {
          return;
        }
      }
      const binding = matchKey(PRESENTATION_KEYMAP, ev);
      if (!binding) return;
      ev.preventDefault();
      dispatch(binding.action);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // `dispatchRef` is a ref, read fresh on every call, so it isn't a dependency.
  }, [actions, onExit]);
}
