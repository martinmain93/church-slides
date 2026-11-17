/**
 * Tiny status pill shown on the presentation view.
 *
 * Three visible states:
 *   - `idle`              — gray, "AI: idle"
 *   - `tracking`          — green pulse, "AI: tracking" (active mode)
 *                           or "AI: shadow" (shadow mode, decisions
 *                           logged but slides not advanced)
 *   - `uncertain`         — amber pulse, "AI: searching / holding slide"
 *                           (Phase 7 lost mode: it knows it's lost and is
 *                           re-localizing, deliberately not guessing)
 *   - `following_human`   — blue, "AI: following you" with a fading
 *                           cooldown bar
 *
 * In `off` mode the pill is an invitation — "Turn on AI tracking" — since
 * the operator view starts every service with the AI off.
 *
 * A service session that failed to start (a microphone that won't open,
 * say) gets an amber "couldn't start" with the reason and a retry, rather
 * than "AI: starting" for the rest of the service.
 *
 * Before any of that, whether live AI can run here at all
 * (`useAiAvailability`): no engine, no downloaded model, or a download that
 * failed offline each get their own calm state, with the reason on hover.
 * None of them stop the operator presenting by hand, and the pill must never
 * read like a crash.
 *
 * Clicking the pill switches the AI off, or back on (Active, or Shadow if a
 * developer chose it in the debug panel). In the operator view (`operator`)
 * it opens a menu instead: Assist (the operator drives, the AI points out
 * where the band went — docs/done/AI_ASSISTED_MODE.md), Auto, or off. This is the
 * only on/off switch. The switch
 * goes to the sidecar live; the pill then redraws from the status the
 * sidecar broadcasts back, so it never claims a mode the AI isn't in.
 *
 * On a computer that can't comfortably run speech recognition itself
 * (`useMachinePower`), switching the AI on says so once, pointing at Groq's
 * online speech; and if the engine then actually falls behind (dropped audio
 * or a growing lag in `pipeline_health`), it says that once too. Groq turning
 * requests away (its rate limit, `asr_refused`) is said once as well: the AI
 * then hears nothing, and nothing else on screen would show why.
 */

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { availabilitySummary, useAiAvailability } from "@/lib/aiAvailability";
import { setAiMode, useAiStatus } from "@/lib/aiDecisions";
import { aiOnMode, operatorOnMode, saveOperatorOnMode } from "@/lib/aiPrefs";
import { Menu, MenuItem } from "@/components/ui/Menu";
import type { AIMode } from "@/lib/aiPrefs";
import { useMachinePower } from "@/lib/machinePower";
import { type PipelineHealth, usePipelineHealth } from "@/lib/pipelineHealth";
import { useSessionStartFailure } from "@/lib/recordingClient";
import { useOptionalToast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const COOLDOWN_SECONDS = 5;

/** Where the operator switches to cloud speech. */
const WHERE = "Settings → AI that follows the songs";
/** Falling behind: this much audio discarded since the AI came on (about 2 s
 * of 100 ms chunks), or this far behind the band. */
const BEHIND_DROPPED_CHUNKS = 20;
const BEHIND_LAG_SECONDS = 3;

/** Each warning once per app run: a service is no time for repeats. */
const warned = { slow: false, behind: false, refused: false };
export function resetSlowComputerWarnings() {
  warned.slow = false;
  warned.behind = false;
  warned.refused = false;
}

/** At or below this charge the computer slows itself down, charger or not
 * (sidecar `pipeline/power.py`). */
const LOW_BATTERY_PERCENT = 10;

/** What to say when the AI falls behind. The computer's own power saving is
 * the likelier cause on a capable machine, and its fix is not cloud speech.
 * Seen live: a Mac run down to a critical battery stayed four times slower
 * for a while after it was plugged in. */
export function behindMessage(health: PipelineHealth): string {
  const late = "The AI is falling behind the band, so slides may change late. ";
  if (health.lowPowerMode) {
    return (
      late +
      "This computer is in Low Power Mode, which slows the AI down a lot. Turn it off " +
      "(System Settings → Battery on a Mac)."
    );
  }
  if (health.batteryPercent != null && health.batteryPercent <= LOW_BATTERY_PERCENT) {
    return (
      late +
      `This computer's battery is very low (${health.batteryPercent}%), and it slows itself ` +
      "down to save power" +
      (health.onBattery
        ? ". Plug it in and give it a few minutes to charge."
        : ", even on the charger. Give it a few minutes to charge.")
    );
  }
  return late + `Groq's online speech keeps up on slower computers (${WHERE}).`;
}

export function AIStatusPill({ operator = false }: {
  /** The operator view: clicking opens Assist / Auto / Off instead of
   * switching on and off, and "on" means `operatorOnMode()`. */
  operator?: boolean;
} = {}) {
  const status = useAiStatus();
  const { availability } = useAiAvailability();
  const startFailure = useSessionStartFailure();
  const [now, setNow] = useState(() => Date.now());
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const power = useMachinePower();
  const toast = useOptionalToast();
  const health = usePipelineHealth();
  const [droppedAtStart, setDroppedAtStart] = useState<number | null>(null);
  const aiOn = status != null && status.mode !== "off";
  // With the AI off nothing needs the microphone yet, so a session that
  // couldn't open one stays quiet — a permanent amber pill on a computer with
  // no mic, for an AI nobody switched on, read like a crash. It surfaces when
  // the operator turns the AI on, or straight away for a start that was on.
  const [failureSurfaced, setFailureSurfaced] = useState(false);
  useEffect(() => {
    if (!startFailure) setFailureSurfaced(false);
  }, [startFailure]);
  const aiOffNow = status ? status.mode === "off" : startFailure?.aiMode === "off";
  const showStartFailure = startFailure != null && (!aiOffNow || failureSurfaced);

  // Count dropped audio from when the AI came on, not from the session start.
  useEffect(() => {
    if (!aiOn) setDroppedAtStart(null);
    else if (droppedAtStart === null && health) setDroppedAtStart(health.droppedChunks);
  }, [aiOn, health, droppedAtStart]);

  useEffect(() => {
    if (!aiOn || !health || !power || !toast || warned.behind || droppedAtStart === null) return;
    const dropped = health.droppedChunks - droppedAtStart;
    if (dropped >= BEHIND_DROPPED_CHUNKS || health.lagSeconds >= BEHIND_LAG_SECONDS) {
      warned.behind = true;
      toast.show(behindMessage(health), { duration: 20000 });
    }
  }, [aiOn, health, power, toast, droppedAtStart]);

  useEffect(() => {
    if (!aiOn || !health?.asrRefused || !toast || warned.refused) return;
    warned.refused = true;
    toast.show(
      "Groq's online speech is turning requests away (its usage limit), so the AI can't hear " +
        `the band. Switch to speech on this computer (${WHERE}), or follow by hand.`,
      { duration: 30000 },
    );
  }, [aiOn, health, toast]);

  // Forget a failed switch after a few seconds.
  useEffect(() => {
    if (!error) return;
    const id = window.setTimeout(() => setError(null), 4000);
    return () => window.clearTimeout(id);
  }, [error]);

  async function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    // Hand focus back to the page: a focused button would swallow the space
    // bar and arrow keys the operator drives slides with.
    e.currentTarget.blur();
    const turningOn = !status || status.mode === "off";
    await choose(turningOn ? (operator ? operatorOnMode() : aiOnMode()) : "off");
  }

  // A computer that passed only Assist's bar: the AI never changes slides
  // itself here, so "on" is Assist (and outside the operator view, nothing).
  const assistOnly = availability.kind === "ready" && availability.assistOnly === true;

  async function choose(requested: AIMode) {
    const target: AIMode = assistOnly && requested !== "off" ? "assist" : requested;
    if (startFailure && aiOffNow && target !== "off") {
      // Turning the AI on can't work without the session it runs in: say why
      // (with the retry) instead of asking the engine for a mode it can't
      // reach.
      setFailureSurfaced(true);
      return;
    }
    if (!status || switching || target === status.mode) return;
    setSwitching(true);
    setError(null);
    const turningOn = status.mode === "off";
    if (operator && target !== "off") saveOperatorOnMode(target);
    try {
      await setAiMode(target);
      // A computer the machine check passed needs no guess about its speed.
      const checked = availability.kind === "ready" && availability.checked === true;
      if (turningOn && power && power.level !== "good" && !checked && toast && !warned.slow) {
        warned.slow = true;
        toast.show(`${power.summary} (${WHERE}.)`, { duration: 15000 });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSwitching(false);
    }
  }


  // Tick once per second while the pill is in `following_human` so the
  // remaining-cooldown bar updates.
  useEffect(() => {
    if (status?.state !== "following_human") return;
    const id = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(id);
  }, [status?.state]);

  // The operator view opens a menu; elsewhere the pill switches on and off.
  const renderPill = (onPillClick: (e: React.MouseEvent<HTMLButtonElement>) => void) => {
    if (error) {
      return (
        <Pill className="border-destructive/50 bg-destructive/15 text-red-100" title={error} onClick={onPillClick}>
          AI: couldn&rsquo;t switch — tap to retry
        </Pill>
      );
    }

    if (availability.kind === "engine_unavailable") {
      return (
        <Pill
          className="border-border bg-background/80 text-muted-foreground"
          title={`${availabilitySummary(availability)}\n\nYou're presenting by hand — slides move when you move them.`}
        >
          AI unavailable
        </Pill>
      );
    }
    if (availability.kind === "too_slow" || (assistOnly && !operator)) {
      return (
        <Pill
          className="border-border bg-background/80 text-muted-foreground"
          title={`${availabilitySummary(availability)}\n\nYou're presenting by hand — slides move when you move them.`}
        >
          AI unavailable on this computer
        </Pill>
      );
    }
    if (availability.kind === "checking_machine") {
      return (
        <Pill
          className="border-sky-500/40 bg-sky-500/10 text-sky-100"
          title={`${availabilitySummary(availability)} Presenting by hand works in the meantime.`}
        >
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-sky-300 opacity-75" />
            <span className="relative inline-flex size-1.5 rounded-full bg-sky-300" />
          </span>
          AI: checking this computer
        </Pill>
      );
    }
    if (availability.kind === "check_unavailable") {
      return (
        <Pill
          className="border-amber-500/40 bg-amber-500/15 text-amber-100"
          title={`${availabilitySummary(availability)}\n\nSettings can check again.`}
        >
          AI: needs a check
        </Pill>
      );
    }
    if (availability.kind === "downloading") {
      return (
        <Pill
          className="border-sky-500/40 bg-sky-500/10 text-sky-100"
          title={`${availabilitySummary(availability)} Presenting by hand works in the meantime.`}
        >
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-sky-300 opacity-75" />
            <span className="relative inline-flex size-1.5 rounded-full bg-sky-300" />
          </span>
          AI: downloading model
          {availability.mbDone !== null && availability.mb > 0
            ? ` ${Math.min(99, Math.round((100 * availability.mbDone) / availability.mb))}%`
            : ""}
        </Pill>
      );
    }
    if (availability.kind === "download_failed") {
      return (
        <Pill
          className="border-amber-500/40 bg-amber-500/15 text-amber-100"
          title={`${availabilitySummary(availability)}\n\nSettings can download it when you're online.`}
        >
          AI: needs internet
        </Pill>
      );
    }

    // Ahead of `status`: a session that failed to start leaves the last AI
    // status it broadcast behind (the director reports in as soon as it is
    // built, before the microphone opens), and that stale status hid the
    // failure behind "Turn on AI tracking" — which could not work.
    if (startFailure && showStartFailure) {
      return (
        <Pill
          className="border-amber-500/40 bg-amber-500/15 text-amber-100"
          title={
            `The AI couldn't start: ${startFailure.message}\n\n` +
            "If it's the microphone, check it's plugged in and chosen in Settings → Audio input. " +
            "You're presenting by hand in the meantime. Click to try again."
          }
          onClick={(e) => {
            e.currentTarget.blur();
            startFailure.retry();
          }}
        >
          AI: couldn&rsquo;t start — tap to retry
        </Pill>
      );
    }

    if (!status && !startFailure) {
      return (
        <div className="rounded-full border border-border bg-background/80 px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          AI: starting
        </div>
      );
    }

    // A quiet start failure with no status yet reads as the AI being off,
    // which it is.
    const { mode, state } = status ?? { mode: "off" as const, state: "idle" as const };
    if (mode === "off") {
      return (
        <Pill
          className="border-violet-400/60 bg-violet-500/15 text-violet-100 shadow-[0_0_12px_rgba(139,92,246,0.35)] hover:bg-violet-500/30"
          title={
            "The AI is off — you're driving the slides. Click to let the AI follow the singing and change slides for you. Click again any time to turn it off." +
            (availability.kind === "needs_download"
              ? `\n\n${availabilitySummary(availability)}`
              : "")
          }
          onClick={onPillClick}
          busy={switching}
        >
          <Sparkles className="size-3" />
          <span>{switching ? "Turning on…" : "Turn on AI tracking"}</span>
        </Pill>
      );
    }

    // Shadow mode never drives slides — make that unmistakable regardless of the
    // underlying idle/tracking state, since operators kept expecting auto-advance.
    if (mode === "shadow") {
      return (
        <Pill
          className="border-amber-500/40 bg-amber-500/15 text-amber-100"
          title="Shadow mode: the AI is watching and logging, but will NOT change slides. Switch to Active in the debug panel's Tunables tab to let it drive. Click to turn the AI off."
          onClick={onPillClick}
          busy={switching}
        >
          <span>AI: shadow</span>
          <span className="ml-1 text-[9px] font-normal normal-case opacity-80">
            watching · won't move slides
          </span>
        </Pill>
      );
    }

    // Assist never moves the slides; it points. Its state underneath
    // (tracking, searching...) is the engine's business, not the operator's.
    if (mode === "assist") {
      return (
        <Pill
          className="border-yellow-400/50 bg-yellow-400/15 text-yellow-100"
          title={
            "Assist: you change the slides. The AI listens and highlights the slide the band is on " +
            "when the screen hasn't followed. Click to change how the AI helps."
          }
          onClick={onPillClick}
          busy={switching}
        >
          <span className="relative flex size-1.5">
            <span className="relative inline-flex size-1.5 rounded-full bg-yellow-300" />
          </span>
          <span>AI: assisting</span>
        </Pill>
      );
    }

    // Lost mode holds the current slide rather than guessing. Say so — a frozen
    // display otherwise reads as a crash, and the operator's reasonable response
    // is to take over at the exact moment the tracker was about to recover.
    if (state === "uncertain") {
      return (
        <Pill
          className="border-amber-400/50 bg-amber-400/20 text-amber-100"
          title="The AI has lost its place and is searching for it. It's holding the current slide on purpose rather than guessing — it should re-anchor within a few seconds. Advance manually if you'd rather not wait, or click to turn the AI off."
          onClick={onPillClick}
          busy={switching}
        >
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-amber-300 opacity-75" />
            <span className="relative inline-flex size-1.5 rounded-full bg-amber-300" />
          </span>
          <span>AI: searching</span>
          <span className="ml-1 text-[9px] font-normal normal-case opacity-80">
            holding slide
          </span>
        </Pill>
      );
    }

    if (state === "following_human") {
      // We don't know absolute wall-clock from the server; approximate
      // the cooldown via the last override timestamp the server reported.
      // For the bar we just animate from full to empty over COOLDOWN_SECONDS.
      const elapsed = Math.min(
        COOLDOWN_SECONDS,
        Math.max(0, (now - (status?.lastOverrideTSeconds ?? now / 1000) * 1000) / 1000),
      );
      const remaining = Math.max(0, 1 - elapsed / COOLDOWN_SECONDS);
      return (
        <Pill
          className="border-sky-500/40 bg-sky-500/15 text-sky-100"
          title="You moved the slide, so the AI is following you for a moment. Click to turn the AI off."
          onClick={onPillClick}
          busy={switching}
        >
          <span>AI: following you</span>
          <div className="ml-2 h-0.5 w-10 overflow-hidden rounded bg-sky-900/50">
            <div
              className="h-full bg-sky-300 transition-[width] duration-200"
              style={{ width: `${remaining * 100}%` }}
            />
          </div>
        </Pill>
      );
    }

    // Active mode from here down.
    if (state === "tracking") {
      return (
        <Pill
          className="border-emerald-500/40 bg-emerald-500/15 text-emerald-100"
          title="The AI is following the lyrics and changing slides. Click to turn it off and drive the slides yourself."
          onClick={onPillClick}
          busy={switching}
        >
          <span className="relative flex size-2 items-center justify-center">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-70" />
            <span className="relative inline-flex size-1.5 rounded-full bg-emerald-300" />
          </span>
          <span>AI: tracking</span>
        </Pill>
      );
    }

    // Active but no decision yet — waiting to recognize the lyrics.
    return (
      <Pill
        className="border-emerald-500/30 bg-emerald-500/10 text-emerald-200/90"
        title="The AI is on, waiting to recognise the lyrics before it changes slides. Click to turn it off."
        onClick={onPillClick}
        busy={switching}
      >
        AI: ready
      </Pill>
    );
  };

  if (!operator) return renderPill(toggle);
  const mode = status?.mode ?? "off";
  return (
    <Menu
      align="right"
      trigger={(toggleMenu) =>
        renderPill((e) => {
          e.currentTarget.blur();
          if (startFailure && aiOffNow) {
            setFailureSurfaced(true);
            return;
          }
          toggleMenu();
        })
      }
    >
      <MenuItem
        checked={mode === "assist"}
        hint="You change the slides. The AI points out where the band has gone if the screen hasn't followed."
        onSelect={() => void choose("assist")}
      >
        Assist me
      </MenuItem>
      <MenuItem
        checked={mode === "active" || mode === "shadow"}
        disabled={assistOnly}
        hint={
          assistOnly
            ? "This computer is too slow for the AI to change slides on time. It can still assist."
            : "The AI follows the singing and changes the slides for you."
        }
        onSelect={() => void choose(aiOnMode())}
      >
        Change slides for me
      </MenuItem>
      <MenuItem
        checked={mode === "off"}
        hint="No listening. You drive the slides."
        onSelect={() => void choose("off")}
      >
        AI off
      </MenuItem>
    </Menu>
  );
}

function Pill({
  className,
  children,
  title,
  onClick,
  busy,
}: {
  className?: string;
  children: React.ReactNode;
  title?: string;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  busy?: boolean;
}) {
  const classes = cn(
    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[10px] uppercase tracking-wide",
    className,
  );
  if (!onClick) {
    return (
      <div className={classes} role="status" aria-live="polite" title={title}>
        {children}
      </div>
    );
  }
  return (
    <button
      type="button"
      className={cn(classes, "cursor-pointer transition-colors disabled:cursor-wait disabled:opacity-70")}
      aria-live="polite"
      title={title}
      onClick={onClick}
      disabled={busy}
      data-testid="ai-toggle"
    >
      {children}
    </button>
  );
}
