/**
 * What another operator asks of this computer, wherever the app is: a setlist
 * they're sending (review and import it, or decline), or a request to copy
 * setlists from here (allow or not). Mounted once, in the app shell.
 */

import { useCallback, useState } from "react";
import { ArrowDownToLine, ShieldQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SyncReviewDialog } from "@/components/peerSync/SyncReviewDialog";
import { useTauriEvent } from "@/lib/peerSync/usePeers";
import {
  incomingPeerOffer,
  PEER_ACCESS_EVENT,
  PEER_OFFER_EVENT,
  respondToPeerAccess,
  respondToPeerOffer,
  type PeerAccessEvent,
  type PeerOfferEvent,
  type SetlistBundle,
  type SyncApplySummary,
} from "@/lib/tauri";

type Prompt = ({ kind: "offer" } & PeerOfferEvent) | ({ kind: "access" } & PeerAccessEvent);

export function summaryMessage(s: SyncApplySummary): string {
  const parts = [];
  if (s.songs_created) parts.push(`${s.songs_created} new song${s.songs_created === 1 ? "" : "s"}`);
  if (s.songs_replaced) parts.push(`${s.songs_replaced} updated`);
  if (s.songs_reused) parts.push(`${s.songs_reused} already here`);
  if (!s.setlist_id) return `Library copied — ${parts.join(", ") || "no songs"}.`;
  return `Saved “${s.setlist_name}” — ${parts.join(", ") || "no songs"}. It's in your setlists on Home.`;
}

export function IncomingSync({ onImported, onError }: { onImported: (message: string) => void; onError: (message: string) => void }) {
  const [queue, setQueue] = useState<Prompt[]>([]);
  const [review, setReview] = useState<{ offerId: string; fromName: string; bundle: SetlistBundle } | null>(null);

  const onOffer = useCallback((p: PeerOfferEvent) => setQueue((q) => [...q, { kind: "offer", ...p }]), []);
  const onAccess = useCallback((p: PeerAccessEvent) => setQueue((q) => [...q, { kind: "access", ...p }]), []);
  useTauriEvent(PEER_OFFER_EVENT, onOffer);
  useTauriEvent(PEER_ACCESS_EVENT, onAccess);

  const current = queue[0];
  const next = () => setQueue((q) => q.slice(1));

  if (review) {
    return (
      <SyncReviewDialog
        bundle={review.bundle}
        fromName={review.fromName}
        onCancel={() => {
          void respondToPeerOffer(review.offerId, false);
          setReview(null);
        }}
        onImported={(summary) => {
          void respondToPeerOffer(review.offerId, true);
          setReview(null);
          onImported(summaryMessage(summary));
        }}
      />
    );
  }
  if (!current) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" role="dialog" aria-modal="true" data-testid="peer-prompt">
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-2xl">
        {current.kind === "offer" ? (
          <>
            <div className="flex items-center gap-2 font-semibold">
              <ArrowDownToLine className="size-5" />
              {current.library ? `${current.from.name} is sending you their library` : `${current.from.name} is sending you a setlist`}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              {current.library ? "Their whole library" : `“${current.setlist_name}”`} — {current.song_count} song
              {current.song_count === 1 ? "" : "s"}. You'll see which songs are new and which you already have before anything is
              saved.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void respondToPeerOffer(current.offer_id, false);
                  next();
                }}
              >
                Decline
              </Button>
              <Button
                size="sm"
                onClick={async () => {
                  next();
                  try {
                    const { bundle, from } = await incomingPeerOffer(current.offer_id);
                    setReview({ offerId: current.offer_id, fromName: from.name, bundle });
                  } catch (e) {
                    onError(e instanceof Error ? e.message : String(e));
                  }
                }}
              >
                Review
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 font-semibold">
              <ShieldQuestion className="size-5" />
              {current.from.name} wants to copy from this computer
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              If you allow it, that computer can see this computer's setlists and copy any of them, or the whole song library, until
              Church Slides is closed here. Nothing on this computer changes.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void respondToPeerAccess(current.request_id, false);
                  next();
                }}
              >
                Don't allow
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  void respondToPeerAccess(current.request_id, true);
                  next();
                }}
              >
                Allow
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
