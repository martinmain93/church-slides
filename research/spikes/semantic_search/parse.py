"""Query -> filters + leftover theme text. Rules + a fixed occasion table; and a local-LLM variant."""
import json
import re
import time

# The only "knowledge" the rule path needs: occasions -> lyric-like phrases to embed.
OCCASIONS = {
    "easter": (r"easter|resurrection sunday", [
        "He is risen from the grave", "the empty tomb, death could not hold Him",
        "Jesus is alive, death is defeated", "on the third day He rose again"]),
    "good friday": (r"good friday", [
        "Jesus died on the cross for me", "nailed to the cross, the Lamb was slain",
        "His blood was shed at Calvary", "the wondrous cross where my Saviour died"]),
    "christmas": (r"christmas|nativity|xmas", [
        "born in a manger in Bethlehem", "angels sing glory to the newborn King",
        "Christ the Saviour is born", "shepherds and wise men came to see the baby"]),
    "advent": (r"advent", [
        "O come Emmanuel, ransom captive Israel", "we wait for the coming of the Lord",
        "prepare the way, the King is coming", "come, Lord Jesus, come"]),
    "palm sunday": (r"palm sunday", ["Hosanna to the King who comes", "blessed is He who comes in the name of the Lord"]),
    "communion": (r"communion|lord'?s supper|eucharist", [
        "His body broken, His blood poured out", "we remember the bread and the cup",
        "a table prepared for me", "washed in the blood of the Lamb"]),
    "pentecost": (r"pentecost", [
        "Holy Spirit come, fall on us", "pour out Your Spirit, set a fire",
        "wind and fire of the Spirit", "Spirit of God, fill this place"]),
    "baptism": (r"baptism|baptis", ["buried with Christ and raised to new life", "I have decided to follow Jesus",
                                     "washed clean, a new creation"]),
    "thanksgiving": (r"thanksgiving|harvest", ["we give thanks for every good gift", "thank You Lord for all You've done"]),
    "remembrance": (r"remembrance|memorial|funeral", ["abide with me, fast falls the eventide", "it is well with my soul",
                                                      "when I see You face to face in heaven"]),
}

KEYS = r"(?:[A-G](?:#|b|♯|♭)?m?)"
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a couple": 2, "a few": 3, "last": 1}

FILLER = re.compile(
    r"\b(songs?|hymns?|music|something|anything|good|great|some|that|which|we|i|you|have|haven'?t|havent|"
    r"not|been|did|do|play(ed|ing)?|sung|sang|about|for|on|of|the|a|an|with|in|any|show|me|find|list|"
    r"what|are|is|worship)\b",
    re.I,
)


def rules(q: str):
    t0 = time.perf_counter()
    s = " " + q.strip() + " "
    out = dict(hymn=None, key=None, played=None, occasion=None)

    m = re.search(rf"\b(?:in (?:the )?key of|key of|in)\s+({KEYS})(?=[\s,.?!])", s)  # case-sensitive key letter
    if m and not re.match(r"in\s+[A-G]\w{2,}", s[m.start():]):
        out["key"] = m.group(1).replace("♯", "#").replace("♭", "b")
        s = s[: m.start()] + " " + s[m.end():]

    if re.search(r"\bhymns?\b", s, re.I):
        out["hymn"] = True

    neg = r"(?:haven'?t|havent|have not|not|didn'?t|did not|never)"
    m = re.search(rf"{neg}\b.*?\b(?:play|sung|sang|done|used).*?(?:last|past)\s+(\d+|one|two|three|four|five|six|a couple|a few)\s+(?:services|sundays|weeks)", s, re.I)
    if m:
        out["played"] = ("not_in_last", int(m.group(1)) if m.group(1).isdigit() else NUM[m.group(1).lower()])
    elif re.search(rf"{neg}\b.*?\b(?:play|sung|sang|done|used).*?(?:in a while|recently|lately|for ages|in ages)", s, re.I):
        out["played"] = ("not_in_last", 4)
    else:
        m = re.search(r"\b(?:play|sung|sang|did)\w*.*?\b(?:last|past)\s+(sunday|week|service|(\d+|two|three|four|a few) (?:services|sundays|weeks))", s, re.I)
        if m:
            n = m.group(2)
            out["played"] = ("in_last", 1 if n is None else (int(n) if n.isdigit() else NUM[n.lower()]))
        elif re.search(r"\b(?:play|sung|sang)\w*\s+(?:recently|lately)", s, re.I):
            out["played"] = ("in_last", 4)

    # strip recency clauses from the leftover text
    s = re.sub(rf"\b(that |which )?{neg}?\s*\b(been )?(play|sung|sang|did|done|used)\w*\b.*$", " ", s, flags=re.I)

    for name, (pat, _) in OCCASIONS.items():
        if re.search(rf"\b(?:{pat})\b", s, re.I):
            out["occasion"] = name
            s = re.sub(rf"\b(?:{pat})\b", " ", s, flags=re.I)
            break

    theme = re.sub(r"\s+", " ", FILLER.sub(" ", re.sub(r"[^\w' ]", " ", s))).strip()
    out["theme"] = theme or None
    out["ms"] = (time.perf_counter() - t0) * 1000
    return out


# ---------------- local LLM variant (Qwen2.5 via MLX) ----------------
LLM_PROMPT = """You turn a church worship leader's song-library search into JSON. Fields:
- "hymn": true if they ask for hymns, else null
- "key": musical key like "E", "Bb", "F#m" if they name one, else null
- "played": null, or {"kind": "not_in_last" or "in_last", "n": number of services}. "haven't played in a while" = not_in_last 4; "recently" = in_last 4; "last sunday" = in_last 1
- "occasion": one of easter, good friday, christmas, advent, palm sunday, communion, pentecost, baptism, thanksgiving, remembrance, or null
- "theme": the remaining topic in a few words (e.g. "blood of Jesus"), or null if there is no topic
Reply with JSON only.

Search: "songs about grace in D"
{"hymn": null, "key": "D", "played": null, "occasion": null, "theme": "grace"}

Search: "christmas songs we haven't sung in 2 services"
{"hymn": null, "key": null, "played": {"kind": "not_in_last", "n": 2}, "occasion": "christmas", "theme": null}

Search: "%s"
"""


class LLMParser:
    def __init__(self, repo):
        from mlx_lm import load
        self.model, self.tok = load(repo)
        self.repo = repo

    def __call__(self, q):
        from mlx_lm import generate
        t0 = time.perf_counter()
        msgs = [{"role": "user", "content": LLM_PROMPT % q}]
        prompt = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        raw = generate(self.model, self.tok, prompt=prompt, max_tokens=80, verbose=False)
        ms = (time.perf_counter() - t0) * 1000
        out = dict(hymn=None, key=None, played=None, occasion=None, theme=None, ms=ms, raw=raw)
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            j = json.loads(m.group(0))
        except Exception:
            out["error"] = True
            return out
        out["hymn"] = True if j.get("hymn") else None
        out["key"] = j.get("key") or None
        p = j.get("played")
        if isinstance(p, dict) and p.get("kind") in ("not_in_last", "in_last"):
            try:
                out["played"] = (p["kind"], int(p.get("n") or 4))
            except Exception:
                out["played"] = (p["kind"], 4)
        occ = (j.get("occasion") or "").lower() or None
        out["occasion"] = occ if occ in OCCASIONS else None
        out["theme"] = j.get("theme") or None
        return out
