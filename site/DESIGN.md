# Breathalyzer site: design direction

**Direction: "Sheikah stone at night."** Dark slate surfaces, glowing cyan circuit traces taken from the icon, and
one warm orange node. Like the icon, orange appears once per screen at most: the main action (Download / Press Play).
The signature shape is the icon's **chamfered octagon**: buttons, cards and screenshot frames have cut corners
instead of round ones. Big, calm, high-contrast text first; decoration never sits behind words.

## Palette (from `assets/breathalyzer.svg`)

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `--bg` | `#0b1013` | `#eef3f5` | page |
| `--bg-2` | `#11181c` | `#e2e9ec` | alternate section band |
| `--surface` | `#172026` | `#ffffff` | cards |
| `--surface-2` | `#222c33` (icon slate `#242d33`) | `#e6edf0` | chips, placeholders |
| `--rim` | `#5f6e77` (icon rim `#56646d`) | `#6b7a83` | borders (3:1+ on bg) |
| `--text` | `#f1f6f8` | `#0c1317` | body text (15:1+) |
| `--muted` | `#c3cfd5` | `#33434c` | secondary text (8:1+) |
| `--cyan` | `#4ee6f5` | `#4ee6f5` | traces, glows, fills (icon cyan) |
| `--cyan-ink` | `#4ee6f5` | `#004f5c` | cyan text and links (7:1+) |
| `--cyan-line` | `#4ee6f5` | `#007c88` | lines and dots that carry meaning: rims, bullets, rings, eyebrow lines (3:1+) |
| `--orange` | `#ff9a3c` | `#ff9a3c` | the one orange node: primary button fill |
| `--orange-ink` | `#ff9a3c` | `#a34700` | orange text ("Press Play.") |
| `--on-orange` | `#170b00` | `#170b00` | text on orange (9:1+) |
| `--focus` | `#ffc58f` | `#a33f00` | focus ring, 4px |

All text pairs pass WCAG AA, nearly all pass AAA. The page follows the computer's light or dark setting (dark when it
has none); the toggle saves the reader's own choice in this browser.

Windows High Contrast (forced colours): the cut-corner shapes are backgrounds, which forced colours removes, so a
`@media (forced-colors: active)` block gives buttons, cards, frames and step numbers real borders and hides the traces.

## Type

- Fira Sans (self-hosted from `assets/fonts`, SIL OFL): Medium 500 for body, SemiBold 600 for headings.
  Fallback: `system-ui, "Segoe UI", Roboto, sans-serif`.
- Root size 125% (1rem = 20px with browser defaults, and it grows with the reader's own browser setting).
- Scale (major third, 1.25): small 0.9rem (18px, the floor: nothing smaller) / body 1rem (20px) /
  lead 1.25rem (25px) / h3 1.5rem (30px) / h2 clamp(2rem..2.75rem) / h1 1.8rem..4.25rem (each of its three lines stays one line, 320px up).
- Line height 1.6 for text, 1.1 for headings; lines max ~34em long.

## Layout grid

- Mobile first: one column, 16px side gutters, no horizontal scroll (checked at 320px).
- Container max 1160px; gutters 24px from 720px, 40px from 1040px.
- From 1040px: the hero is 8/4 (text / icon) and feature rows are a 12-column grid with a 7/5 split (screenshot / text), alternating sides.
  Download cards: 1 column, then 3 columns from 1040px. Steps: 1 column, then 3 from 900px.
- Sections are separated by ~6rem of space and a labelled "trace" eyebrow (`01 Features`) instead of rules.
- FAQ from 1040px: 4/8, the heading on the left (sticky), the questions right.
- Trailer: a one-line strip after Features until the video exists (TODO in index.html says how to bring the full
  section and the hero's "Watch the trailer" button back).
- Honesty: anything in the beta that was not checked in the game yet carries a `New` badge (`.new`).

## Motion

- Hero: the circuit traces draw in once on load (stroke-dashoffset, 1.4s); the orange node pulses 3 times and stops.
  The background traces show from 1040px only, where they stay clear of the words; phones get one glow behind the icon.
- Scroll: sections rise 16px and fade in once (IntersectionObserver, 500ms); each eyebrow's trace line grows.
- Hover/focus: cards lift 2px and their rim turns cyan.
- `prefers-reduced-motion: reduce`: no animation or transitions at all; everything is visible at once.
  Without JS everything is visible too (only `html.js` hides content before it reveals).
- No parallax, no autoplay video, nothing that loops forever.

## Accessibility rules

- Focus: 4px `--focus` outline, 3px offset, on every link, button and summary; skip link first.
- Touch targets 48px+. Alt text on every screenshot; decorative art is `aria-hidden`.
- Real headings in order, `<details>` FAQ, landmark elements, `lang="en"`.
- No trackers, no analytics, no external requests: fonts, images and script are all local.
