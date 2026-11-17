# Icons

Every file in this directory is generated from `app-icon-source.svg`: the
stained-glass window mark on a 1024×1024 canvas, laid out on the macOS icon grid
(an 824px rounded square with a soft shadow). To regenerate after changing it:

```sh
pnpm tauri icon src-tauri/icons/app-icon-source.svg
```

This rewrites the .icns (macOS), .ico (Windows), and the PNG, iOS and Android icons.
