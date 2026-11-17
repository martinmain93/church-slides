"""Pooling helper for labelling: songs whose slides hit any probe regex, with hit counts + snippets."""
import re
import sys

import corpus

PROBES = {
    "H1": r"victor|victory|overc[oa]me|conquer|triumph|defeated|won the|champion|death.*sting|grave.*(lost|no)",
    "H2": r"\bblood\b|wash(ed)? .*white|crimson|lamb (that was )?slain|sin.*stain",
    "H3": r"faithful|never fail|unchanging|steadfast|promise",
    "H4": r"\bcross\b|calvary|crucif|nail|\btree\b|died for|on that hill",
    "H5": r"\bheal|healer|sick|broken.*made whole|restore",
    "H6": r"surrender|i give (it all|my(self)?)|lay (it|my life) down|all to (you|jesus)|on the altar|take my life",
    "H7": r"holy spirit|spirit (come|break|fall|move)|fire|fall (afresh|on us)|breathe|pour out|rain down|wind",
    "H8": r"reckless|chas(e|es|ing)|pursu|leave the ninety|came (looking|for me)|ran to me|found me",
    "H9": r"storm|trial|valley|fire|waves|even when|though .*(fall|fail)|trust|when the .*(dark|hard)|fear",
    "H10": r"child(ren)? of god|son|daughter|adopt|father|no longer slave|family|who you say i am",
    "H11": r"heaven|eternity|forever|glory|throne|crown|that day|face to face",
    "H12": r"creation|stars|mountains|oceans|sun|earth|rocks cry|heavens declare|all creatures|trees",
    "H13": r"battle|fight|enemy|war|weapon|chains|prison|break every",
    "H14": r"\bname\b|no other name|jesus.*name|name of jesus|beautiful name",
    "O1": r"risen|rose|\brise\b|grave|tomb|resurrect|alive|third day|death.*(lost|defeated|sting)|stone (was )?rolled|easter",
    "O2": r"christmas|manger|bethlehem|noel|born|newborn|shepherds?|angels?|virgin|emmanuel|immanuel|wise men|star",
    "O3": r"\bcross\b|calvary|crucif|died|suffer|nail|thorn|it is finished|wounds|pierced|lamb",
    "O4": r"bread|cup|body|broken for|table|remember|blood|wine",
    "O5": r"come.*(lord|jesus)|emmanuel|ransom captive|waiting|longing|maranatha|even so|come thou|prepare|light .*darkness|hope",
    "O6": r"holy spirit|spirit|fire|wind|pour out|rain|tongues|power|breath",
}


def run(qid, minhits=1):
    pat = re.compile(PROBES[qid], re.I)
    rows = []
    for s in corpus.load_songs():
        hits = [sl for sl in s["slides"] if pat.search(sl)]
        if len(hits) >= minhits:
            rows.append((len(hits), s["title"], hits[0][:110]))
    rows.sort(reverse=True)
    for n, t, h in rows:
        print(f"{n:2d} | {t} | {h}")


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
