# Church Slides brand

The mark is a pointed stained-glass window with Y tracery, a lyric slide in the middle,
and a faint projector beam rising from the foot of the window to the slide.

## Files

| File | Use |
|---|---|
| `church-slides-mark.svg` | The mark on dark backgrounds. |
| `church-slides-mark-light.svg` | The mark with slightly deeper glass, so it holds its edge on light backgrounds. Also used in the website header. |
| `church-slides-lockup.svg` | Mark and wordmark, cream lettering, for dark backgrounds. |
| `church-slides-lockup-light.svg` | Mark and wordmark, plum lettering, for light backgrounds. |
| `../src-tauri/icons/app-icon-source.svg` | The app icon: the mark on a plum rounded square, on the macOS icon grid. Every file in `src-tauri/icons` is generated from it with `pnpm tauri icon`. |

The tracery lines in the marks and lockups are cut through (an SVG mask), so the
background shows in them; put the mark straight on the page, not on a tile. The
lockups' lettering is outlined, so they don't need the font installed.

## Type

The wordmark is **Fraunces** SemiBold (600), letter-spacing -0.01em. It's a Google Font
under the SIL Open Font License. The app bundles it via `@fontsource/fraunces` (Tailwind
class `font-brand`); the website self-hosts it in `site/fonts/`.

## Colours

| Role | Hex |
|---|---|
| Plum (icon background, ink on light) | `#221322` |
| Slide | `#FFF6E4` |
| Slide text | `#2A1A14` |
| Top pane | `#FFE7A0` → `#F6C453` |
| Left pane | `#F4B45A` → `#E2605E` |
| Right pane | `#FAD062` → `#F09A3A` |
| Lower pane | `#E4572E` → `#8E2D5C` |
| Beam | white at 26% opacity |

The light mark's deeper glass: top `#F4BC45` → `#EC962C`, left `#EE9447` → `#CF4A58`,
right `#F2B53E` → `#E47E2A`, lower `#DC4B2B` → `#7A2450`.
