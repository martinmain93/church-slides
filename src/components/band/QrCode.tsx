/** A QR code for a band screen link, with a caption. */

import { useMemo } from "react";
import qrcode from "qrcode-generator";

export function QrCode({ text, label, large = false }: { text: string; label: string; large?: boolean }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  return (
    <figure className="flex flex-col items-center gap-1">
      <div
        className={`${large ? "w-64" : "w-36"} rounded bg-white p-1 [&>svg]:h-auto [&>svg]:w-full`}
        role="img"
        aria-label={`QR code for ${label}`}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      <figcaption className="text-xs text-muted-foreground">{label}</figcaption>
    </figure>
  );
}
