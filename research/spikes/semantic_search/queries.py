"""The test set. Relevance labels were written BEFORE any new system ran (pooled from
keyword probes + reading lyrics). A song is relevant only if it's substantially about
the theme (chorus / several slides) or an obvious pick for the occasion."""

HYMNS = {
    "Abide With Me", "All Creatures of Our God and King", "Amazing Grace", "Angels We Have Heard On High",
    "Away In A Manger (Mueller)", "Before The Throne Of God Above", "Doxology", "Great Is Thy Faithfulness",
    "Hark The Herald Angels Sing (Mendelssohn)", "Holy Holy Holy (Nicaea)", "How Great Thou Art",
    "HowGreatThouArtPaulBaloche", "I Have Decided To Follow Jesus", "I Surrender All (I Surrender All (All To Jesus))",
    "It Is Well With My Soul", "It Is Well With My Soul (2)", "Jesus Paid It All", "Jesus Loves Me",
    "Joy To The World (Antioch)", "Just As I Am", "Nothing But The Blood (Plainfield)", "O Come All Ye Faithful",
    "O Come O Come Emmanuel (Veni Emmanuel)", "O Holy Night", "Silent Night", "Silent Night (2)",
    "Turn Your Eyes Upon Jesus", "What A Friend We Have In Jesus (Blaenwern)", "Crown Him (Majesty)",
    "My Tribute", "You Are My Vision", "Jesus Lover Of My Soul", "How Deep The Father's Love For Us",
    "In Christ Alone",
}

CROSS = ["The Wonderful Cross", "At The Cross (Love Ran Red)", "You came for criminals and every pharisee",
         "Once Again", "O Praise The Name (Anástasis)", "Glorious Day (Living He Loved Me)",
         "How Deep The Father's Love For Us", "Jesus Paid It All", "Beautiful",
         "No Other Like You (We Will Exalt You)", "Reason", "Reason – Martin Main"]
SPIRIT = ["Holy Spirit", "Spirit Break Out", "Fall Afresh", "Set A Fire", "Consuming Fire", "Light The Fire",
          "Open Up The Heavens", "We Are Hungry - Chris Quilala", "Here As In Heaven", "Let it Echo (Heaven Fall)",
          "Here For You", "Shekinah", "Build Your Kingdom Here"]

# kind: title | lyric | theme | occasion | filter
# gold parse: hymn (bool|None), key (str|None), played (None | ("not_in_last", n) | ("in_last", n)),
#             occasion (str|None), theme (bool: is there leftover meaning to search)
Q = [
    # ---- titles: the target must be #1 (alternatives accepted)
    dict(id="T1", q="Amazing grace", kind="title", rel=["Amazing Grace", "Amazing Grace (My Chains Are Gone)"]),
    dict(id="T2", q="10,000 reasons", kind="title", rel=["10,000 Reasons (Bless The Lord)"]),
    dict(id="T3", q="oceans", kind="title", rel=["Oceans (Where Feet May Fail)"]),
    dict(id="T4", q="how great thou art", kind="title", rel=["How Great Thou Art", "HowGreatThouArtPaulBaloche"]),
    dict(id="T5", q="reckless love", kind="title", rel=["Reckless Love", "RECKLESS LOVE - BETHEL"]),
    dict(id="T6", q="what a beautiful name", kind="title", rel=["What A Beautiful Name", "What A Beautiful Name It Is - Hillsong"]),
    dict(id="T7", q="king of my heart", kind="title", rel=["King Of My Heart"]),
    dict(id="T8", q="in christ alone", kind="title", rel=["In Christ Alone"]),
    # ---- remembered lyric lines
    dict(id="L1", q="wretch like me", kind="lyric", rel=["Amazing Grace", "Amazing Grace (My Chains Are Gone)"]),
    dict(id="L2", q="song about walking on water trusting god", kind="theme", rel=["Oceans (Where Feet May Fail)"]),
    dict(id="L3", q="song about praising god every morning", kind="theme", rel=["10,000 Reasons (Bless The Lord)"]),
    dict(id="L4", q="hope in life and death", kind="theme", rel=["In Christ Alone"]),
    dict(id="L5", q="song about god parting the red sea", kind="theme", rel=["No Longer Slaves", "No Longer Slaves - Helsers"]),
    # ---- themes
    dict(id="H1", q="Songs about victory in Jesus", kind="theme", rel=[
        "Victor's Crown", "Champion", "Overcome", "Desert Song", "Unstoppable God", "Shout Unto God", "Shout It Out",
        "Death Where Is Your Sting", "I Will Rise", "God Is Able", "Resurrecting", "This We Know",
        "Forever (We Sing Hallelujah)", "Surrounded (Fight My Battles)", "Fight My Battles", "This is how I Fight my Battles",
        "Raise A Hallelujah", "I Raise A Hallelujah", "Crown Him (Majesty)", "Great Things", "Great Things - Phil Wickham",
        "To Our God", "Happy Day"]),
    dict(id="H2", q="the blood of Jesus", kind="theme", rel=[
        "The Blood", "Nothing But The Blood (Plainfield)", "Just As I Am", "This is how I Fight my Battles",
        "Surrounded (Fight My Battles)", "My Tribute", "Jesus Paid It All", "Clean", "At The Cross (Love Ran Red)",
        "Overcome", "Thank you", "Thank you (2)", "Majesty", "Fight My Battles"]),
    dict(id="H3", q="God's faithfulness", kind="theme", rel=[
        "Great Is Thy Faithfulness", "Faithful To The End", "Faithful Til The End - Bethel", "Faithful One", "He Is Faithful",
        "Your love never fails", "Your Love Never Fails - Jesus Culture", "Unchanging", "The Steadfast Love", "Do It Again",
        "Yes And Amen", "Yes and Amen - Housefires", "One Thing Remains", "Whom Shall I fear"]),
    dict(id="H4", q="songs about the cross", kind="theme", rel=CROSS),
    dict(id="H5", q="healing", kind="theme", rel=["Healer", "Heal Our Land", "This is what it looks like"]),
    dict(id="H6", q="surrendering everything to God", kind="theme", rel=[
        "I Surrender All (I Surrender All (All To Jesus))", "I Give Myself Away", "Everything And Nothing Less",
        "Arms Open Wide", "All On The Altar", "Nothing I Hold Onto", "My Heart Is Yours", "Jesus All For Jesus",
        "I Have Decided To Follow Jesus", "Here's My Heart", "Spirit Lead Me"]),
    dict(id="H7", q="asking the Holy Spirit to come", kind="theme", rel=SPIRIT),
    dict(id="H8", q="God's love chasing after us", kind="theme", rel=["Reckless Love", "RECKLESS LOVE - BETHEL", "Relentless", "You Found Me"]),
    dict(id="H9", q="God fights our battles", kind="theme", rel=[
        "Surrounded (Fight My Battles)", "Fight My Battles", "This is how I Fight my Battles", "Another In The Fire",
        "Raise A Hallelujah", "I Raise A Hallelujah", "Whom Shall I fear", "Break Every Chain", "Call Upon The Lord"]),
    # ---- occasions
    dict(id="O1", q="songs for easter", kind="occasion", rel=[
        "Resurrecting", "Forever (We Sing Hallelujah)", "I Will Rise", "Death Where Is Your Sting", "O Praise The Name (Anástasis)",
        "Glorious Day (Living He Loved Me)", "Thank you", "Thank you (2)", "Happy Day", "Crown Him (Majesty)", "My Redeemer Lives",
        "Victor's Crown", "This is what it looks like", "In Christ Alone", "All Hail King Jesus", "Shout Hosanna",
        "Mighty To Save", "Shout It Out"]),
    dict(id="O2", q="christmas songs", kind="occasion", rel=[
        "Hark The Herald Angels Sing (Mendelssohn)", "Angels We Have Heard On High", "O Come All Ye Faithful", "Silent Night",
        "Silent Night (2)", "O Holy Night", "Away In A Manger (Mueller)", "Glory in the Highest", "Joy To The World (Antioch)",
        "Joy To The World (Unspeakable Joy)", "Joy To The World / Our God Saves", "O Come O Come Emmanuel (Veni Emmanuel)",
        "He Shall Reign Forevermore"]),
    dict(id="O3", q="good friday", kind="occasion", rel=CROSS + ["Nothing But The Blood (Plainfield)", "The Blood"]),
    dict(id="O4", q="songs for communion", kind="occasion", rel=[
        "Jesus Messiah", "Surrounded (Fight My Battles)", "Fight My Battles", "This is how I Fight my Battles", "The Blood",
        "Nothing But The Blood (Plainfield)", "Jesus Paid It All", "O Come To The Altar", "At The Cross (Love Ran Red)",
        "How Deep The Father's Love For Us", "The Wonderful Cross"]),
    dict(id="O5", q="pentecost", kind="occasion", rel=SPIRIT),
    # ---- filters (+ theme); gold parse checked separately, retrieval scored on the filtered set
    dict(id="F1", q="Hymns in the key of E about the blood of Jesus", kind="filter",
         gold=dict(hymn=True, key="E", played=None, occasion=None, theme=True)),
    dict(id="F2", q="Good songs for easter that we haven't played in the last 3 services", kind="filter",
         gold=dict(hymn=None, key=None, played=("not_in_last", 3), occasion="easter", theme=False)),
    dict(id="F3", q="hymns about grace", kind="filter", gold=dict(hymn=True, key=None, played=None, occasion=None, theme=True)),
    dict(id="F4", q="songs in G", kind="filter", gold=dict(hymn=None, key="G", played=None, occasion=None, theme=False)),
    dict(id="F5", q="christmas hymns", kind="filter", gold=dict(hymn=True, key=None, played=None, occasion="christmas", theme=False)),
    dict(id="F6", q="worship songs we haven't played in a while", kind="filter",
         gold=dict(hymn=None, key=None, played=("not_in_last", None), occasion=None, theme=False)),
    dict(id="F7", q="songs in the key of A about God's goodness", kind="filter",
         gold=dict(hymn=None, key="A", played=None, occasion=None, theme=True)),
    dict(id="F8", q="what did we play last sunday", kind="filter",
         gold=dict(hymn=None, key=None, played=("in_last", 1), occasion=None, theme=False)),
    dict(id="F9", q="songs in Bb we played recently", kind="filter",
         gold=dict(hymn=None, key="Bb", played=("in_last", None), occasion=None, theme=False)),
    dict(id="F10", q="advent hymns", kind="filter", gold=dict(hymn=True, key=None, played=None, occasion="advent", theme=False)),
]

# ---- HELD OUT: written after the rule parser, never used to tune it. Parse-only.
# played n=None means any n accepted (vague phrasing). alt = also-acceptable values.
def G(hymn=None, key=None, played=None, occasion=None, theme=False, **alt):
    return dict(hymn=hymn, key=key, played=played, occasion=occasion, theme=theme, alt=alt)

HOLDOUT = [
    ("hymns we haven't done in over a month", G(hymn=True, played=("not_in_last", None))),
    ("anything in F#", G(key="F#")),
    ("easter hymns in C", G(hymn=True, key="C", occasion="easter")),
    ("songs about grace we sang last week", G(played=("in_last", 1), theme=True)),
    ("what haven't we played for 6 weeks", G(played=("not_in_last", 6))),
    ("christmas carols", G(occasion="christmas", hymn_alt=True)),
    ("songs in D minor about lament", G(key="Dm", theme=True)),
    ("a song for baptism sunday", G(occasion="baptism")),
    ("old hymns about heaven", G(hymn=True, theme=True)),
    ("upbeat songs in A that we didn't play in the last two services", G(key="A", played=("not_in_last", 2), theme=True)),
    ("good friday songs not used recently", G(occasion="good friday", played=("not_in_last", None))),
    ("songs in E flat", G(key="Eb")),
    ("communion songs in G", G(occasion="communion", key="G")),
    ("praise songs about God's power", G(theme=True)),
    ("songs we did last sunday about love", G(played=("in_last", 1), theme=True)),
    ("slow songs for remembrance sunday", G(occasion="remembrance", theme=True)),
    ("advent songs we haven't played this year", G(occasion="advent", played=("not_in_last", None))),
    ("hymn about the cross in Bb", G(hymn=True, key="Bb", theme=True)),
    ("songs about the resurrection", G(theme=True, occasion_alt="easter")),
    ("In Christ Alone", G(theme=True)),
    ("Be Thou My Vision in D", G(key="D", theme=True)),
    ("thanksgiving songs in C we haven't played in 3 weeks", G(occasion="thanksgiving", key="C", played=("not_in_last", 3))),
    ("Hymns", G(hymn=True)),
    ("A mighty fortress", G(theme=True)),
    ("songs to open the service with", G(theme=True)),
]
