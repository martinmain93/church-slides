import sys, json, sqlite3, loro
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
snap = con.execute("select snapshot from workspace_streams where kind='Presentation' and core_path=?", (sys.argv[2],)).fetchone()[0]
d = loro.LoroDoc(); d.import_(snap); v = d.get_deep_value()
json.dump(v, open(sys.argv[3], "w"), indent=1, default=str)
def walk(o, p="", depth=0):
    if depth > 5: return
    if isinstance(o, dict):
        for k, x in o.items(): walk(x, f"{p}.{k}", depth+1)
    elif isinstance(o, list):
        print(f"{p}[{len(o)}]"); 
        if o: walk(o[0], p+"[0]", depth+1)
    else: print(p, "=", str(o)[:90])
walk(v)
