"""Search the library by meaning: "songs about the blood of Jesus", "easter".

    embedder   the bundled sentence model (e5-small-v2, int8 ONNX), one per process
    index      every song as one whole-song vector plus one per passage,
               cached on disk and refreshed as songs change

The UI parses the query (filters, occasions — `src/lib/smartSearch`) and
sends only the texts to search for; this package ranks songs against them.
Measured on a real 326-song library (research/spikes/semantic_search): themes
0.16 -> 0.46 precision@10 over keyword search, occasions 0.00 -> 0.60.
"""
