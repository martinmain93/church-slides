/**
 * Plain text out of the RTF ProPresenter stores for each slide's text box.
 *
 * Only what slide text needs: paragraph and line breaks, `\'hh` (Windows-1252)
 * and `\uN` escapes. Destination groups (font and colour tables, `{\*…}`) are
 * skipped whole; every other control word is dropped.
 */

const CP1252_HIGH =
  "€\u0081‚ƒ„…†‡ˆ‰Š‹Œ\u008dŽ\u008f" +
  "\u0090‘’“”•–—˜™š›œ\u009džŸ";

function cp1252(byte: number): string {
  return byte >= 0x80 && byte <= 0x9f ? CP1252_HIGH[byte - 0x80]! : String.fromCharCode(byte);
}

/** Groups whose text is not shown. */
const SKIP_DESTINATIONS = new Set(["fonttbl", "colortbl", "stylesheet", "info", "pict", "expandedcolortbl"]);

export function rtfToText(rtf: string): string {
  if (!rtf.trimStart().startsWith("{\\rtf")) return rtf;
  let out = "";
  // One entry per open group: is its text skipped?
  const skipping: boolean[] = [false];
  let ucSkip = 1; // characters after \uN that stand in for it
  let pendingSkip = 0;
  let i = 0;
  const skip = () => skipping[skipping.length - 1]!;
  const emit = (s: string) => {
    if (pendingSkip > 0) {
      pendingSkip -= 1;
      return;
    }
    if (!skip()) out += s;
  };

  while (i < rtf.length) {
    const c = rtf[i]!;
    if (c === "{") {
      skipping.push(skip());
      i += 1;
      // `{\*\dest …}` and the known destinations are never text.
      const rest = rtf.slice(i, i + 24);
      const word = /^\\([a-z]+)/.exec(rest)?.[1];
      if (rest.startsWith("\\*") || (word && SKIP_DESTINATIONS.has(word))) skipping[skipping.length - 1] = true;
      continue;
    }
    if (c === "}") {
      if (skipping.length > 1) skipping.pop();
      i += 1;
      continue;
    }
    if (c === "\\") {
      const next = rtf[i + 1];
      if (next === "\n" || next === "\r") {
        emit("\n"); // "\<newline>" is a line break inside a paragraph
        i += 2;
        continue;
      }
      if (next === "\\" || next === "{" || next === "}") {
        emit(next);
        i += 2;
        continue;
      }
      if (next === "'") {
        emit(cp1252(parseInt(rtf.slice(i + 2, i + 4), 16)));
        i += 4;
        continue;
      }
      if (next === "~") {
        emit(" ");
        i += 2;
        continue;
      }
      const m = /^\\([a-zA-Z]+)(-?\d+)? ?/.exec(rtf.slice(i));
      if (!m) {
        i += 2;
        continue;
      }
      i += m[0].length;
      const [, word, arg] = m;
      if (word === "par" || word === "line") emit("\n");
      else if (word === "tab") emit("\t");
      else if (word === "uc") ucSkip = Number(arg ?? 1);
      else if (word === "u" && arg !== undefined) {
        const code = Number(arg);
        emit(String.fromCharCode(code < 0 ? code + 65536 : code));
        pendingSkip = ucSkip;
      }
      continue;
    }
    if (c === "\n" || c === "\r") {
      i += 1; // raw newlines in RTF are formatting only
      continue;
    }
    emit(c);
    i += 1;
  }
  return out.replace(/[ \t]+\n/g, "\n").trim();
}
