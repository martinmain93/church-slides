/**
 * "Send feedback": a message to the developers, from the sidebar or
 * Settings. It goes to Sentry's feedback inbox (`src-tauri/src/feedback.rs`),
 * tied to this install so it sits next to any error report from the same
 * computer. When it can't be sent (no internet), the same words can go by
 * email instead.
 */

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Mail, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { getSettings, openLink, sendFeedback, type FeedbackKind } from "@/lib/tauri";
import { cn } from "@/lib/utils";

/** Fire this on `window` to open the dialog from anywhere. A `detail` of
 * `{ message }` starts it as a problem report with that text. */
export const OPEN_FEEDBACK_EVENT = "auto-presenter:open-feedback";

export function openFeedback(message?: string) {
  window.dispatchEvent(new CustomEvent(OPEN_FEEDBACK_EVENT, { detail: message ? { message } : undefined }));
}

const FEEDBACK_EMAIL = "martinmain93@gmail.com";

const KINDS: { kind: FeedbackKind; label: string; placeholder: string }[] = [
  { kind: "problem", label: "Something's wrong", placeholder: "What happened, and what did you expect? When during the service was it?" },
  { kind: "idea", label: "An idea", placeholder: "What would make Church Slides better for your church?" },
  { kind: "praise", label: "It worked well", placeholder: "What went well? It helps to know what to keep." },
  { kind: "other", label: "Something else", placeholder: "Anything you'd like the developers to know." },
];

type Status =
  | { kind: "editing" }
  | { kind: "sending" }
  | { kind: "sent" }
  | { kind: "failed"; message: string };

/** Listens for `OPEN_FEEDBACK_EVENT` and shows the dialog. Mount once. */
export function FeedbackHost() {
  const [open, setOpen] = useState<{ message: string } | null>(null);
  useEffect(() => {
    const onOpen = (e: Event) => setOpen({ message: (e as CustomEvent<{ message?: string } | undefined>).detail?.message ?? "" });
    window.addEventListener(OPEN_FEEDBACK_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_FEEDBACK_EVENT, onOpen);
  }, []);
  return open ? <FeedbackDialog initialMessage={open.message} onClose={() => setOpen(null)} /> : null;
}

export function FeedbackDialog({ initialMessage = "", onClose }: { initialMessage?: string; onClose: () => void }) {
  const [kind, setKind] = useState<FeedbackKind>("problem");
  const [message, setMessage] = useState(initialMessage);
  const [contact, setContact] = useState("");
  const [includeLog, setIncludeLog] = useState(true);
  const [status, setStatus] = useState<Status>({ kind: "editing" });
  const textRef = useRef<HTMLTextAreaElement>(null);

  // Start from the contact given for error reports, if any.
  useEffect(() => {
    getSettings()
      .then((s) => setContact((c) => c || s.report_contact || ""))
      .catch(() => undefined);
    textRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      ev.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const current = KINDS.find((k) => k.kind === kind) ?? { label: "Feedback", placeholder: "" };
  const canSend = message.trim().length > 0 && status.kind !== "sending";

  async function send() {
    setStatus({ kind: "sending" });
    try {
      await sendFeedback({ kind, message, contact, includeLog: kind === "problem" && includeLog });
      setStatus({ kind: "sent" });
    } catch (err) {
      setStatus({ kind: "failed", message: err instanceof Error ? err.message : String(err) });
    }
  }

  function emailInstead() {
    const body = `${current.label}\n\n${message.trim()}${contact.trim() ? `\n\nFrom: ${contact.trim()}` : ""}`;
    void openLink(`mailto:${FEEDBACK_EMAIL}?body=${encodeURIComponent(body)}`);
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-title"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-full w-full max-w-lg flex-col overflow-y-auto rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        {status.kind === "sent" ? (
          <div className="space-y-3 py-4 text-center">
            <CheckCircle2 className="mx-auto size-8 text-emerald-500" />
            <h2 id="feedback-title" className="text-base font-semibold">
              Thank you
            </h2>
            <p className="text-sm text-muted-foreground">
              Your feedback has been sent.
              {contact.trim() ? " If there's a question about it, you'll hear back." : ""}
            </p>
            <Button type="button" onClick={onClose}>
              Close
            </Button>
          </div>
        ) : (
          <>
            <h2 id="feedback-title" className="flex items-center gap-2 text-base font-semibold">
              <MessageSquare className="size-4" />
              Send feedback
            </h2>
            <p className="mb-4 text-xs text-muted-foreground">
              Goes straight to the person who makes Church Slides. Every message is read.
            </p>

            <div className="mb-3 flex flex-wrap gap-1.5" role="radiogroup" aria-label="Kind of feedback">
              {KINDS.map((k) => (
                <button
                  key={k.kind}
                  type="button"
                  role="radio"
                  aria-checked={kind === k.kind}
                  onClick={() => setKind(k.kind)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs transition-colors",
                    kind === k.kind
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {k.label}
                </button>
              ))}
            </div>

            <Textarea
              ref={textRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={current.placeholder}
              rows={6}
              maxLength={5000}
              aria-label="Your feedback"
            />

            <label htmlFor="feedback-contact" className="mt-3 block text-sm font-medium">
              Your email or church <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="feedback-contact"
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              placeholder="So you can get a reply"
              className="mt-1"
            />

            {kind === "problem" && (
              <label className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={includeLog}
                  onChange={(e) => setIncludeLog(e.target.checked)}
                />
                <span>
                  Include the app&rsquo;s recent log, which helps find the cause. It can include song
                  titles and lyrics and device names, but never audio or what was said.
                </span>
              </label>
            )}

            {status.kind === "failed" && (
              <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                <p>{status.message}</p>
                <p className="mt-1 text-muted-foreground">
                  You can try again when you&rsquo;re online, or send it by email instead.
                </p>
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
              {status.kind === "failed" && (
                <Button type="button" variant="outline" onClick={emailInstead} disabled={!message.trim()}>
                  <Mail className="mr-1.5 size-4" />
                  Email instead
                </Button>
              )}
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="button" onClick={() => void send()} disabled={!canSend}>
                {status.kind === "sending" && <Loader2 className="mr-1.5 size-4 animate-spin" />}
                {status.kind === "sending" ? "Sending…" : status.kind === "failed" ? "Try again" : "Send"}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
