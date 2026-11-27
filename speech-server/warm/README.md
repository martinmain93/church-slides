# Warm-up audio

`hymn.s16le.gz`: 40 s (from 0:15) of "Holy, Holy, Holy, Lord God Almighty"
(tune Nicaea), track 5 of the *Army and Navy Hymnal, vol. 2*, LibriVox,
public domain:
https://www.archive.org/download/armynavyhymnal2_1405_librivox/armyandnavyhymnal2_05_various_64kb.mp3

16 kHz mono, signed 16-bit little-endian, gzipped. The image build streams
it through the engine so the GPU kernels are compiled before anyone
connects (`app.py`, `_compile_kernels`).

    ffmpeg -ss 15 -t 40 -i armyandnavyhymnal2_05_various_64kb.mp3 -ac 1 -ar 16000 -f s16le - | gzip -9 > hymn.s16le.gz
