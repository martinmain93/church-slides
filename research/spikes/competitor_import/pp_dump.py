import sys, json, sqlite3, loro
db, kind = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
for scope, path, snap in con.execute("select scope, core_path, snapshot from workspace_streams where kind=?", (kind,)):
    doc = loro.LoroDoc(); doc.import_(snap)
    print("== scope", scope, path)
    print(json.dumps(doc.get_deep_value(), indent=1, default=str)[:int(sys.argv[3]) if len(sys.argv)>3 else 3000])
