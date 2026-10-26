# Hivegrid design language

Every surface follows these rules. Use the shared components instead of drawing your own:
- **Python TUIs:** the "design components" section of `bin/hivegrid_ui.py`.
- **Lua:** `config/theme.lua`.

A screenshot that breaks a rule here is a bug.

## Tokens

The palette comes from the theme preset: `config/theme.lua` (`P`, `T`) and `PRESETS` in `bin/hivegrid_settings_core.py`. TUIs receive it from the config. Never hardcode a hex value; blends of tokens (`U.mix`) are fine.

- **Surfaces, darkest to lightest:**

  | Token | Used for |
  |---|---|
  | `deep` | header band, panel title row |
  | `base` | terminals, panel bodies |
  | `surface` | docked chrome (bar, sidebar view, dock), overlay cards |
  | `pill` | header pills, secondary buttons |
  | `raised` | selection, active tab, neutral buttons |
  | `track` | gauge background |

- **Lines:**
  - `T.split` between terminals.
  - `border` between chrome and the grid.
  - Title-row and table rules use `U.rule_color(pal, bg)` = mix(bg, `edge_hi`, .45).
  - `edge_hi` for overlay and modal frames.
- **Text:**
  - `bright`: selected items, headings.
  - `text`: body, and the keys in footers.
  - `muted`: labels, second lines, the labels in footers.
  - `dim`: ages, paths, disabled items, "—".
- **Accent:** the ⬡ title glyph and word, the ▎ selection marker, primary buttons (accent background, `deep` text, bold), the selected row's number, links. `cyan` marks secondary identifiers (tab numbers, git branches).
- **Meaning colours:** `green` ok, `amber` warn, `red` error, everywhere: git dots, gauges, status lines.
- **Mode pills:** BROADCAST is red; RESIZE, PANE and ACCOUNT are amber; ZOOM is shown while a pane is zoomed. Keys go next to the pill when space allows.

## Agent states (one table)

The table is `U.STATES` in `bin/hivegrid_ui.py`; its Lua twin is `theme.STATES` in `config/theme.lua`, and a test keeps the two equal. The dock, hive, picker, agents panel and header logo all use it.

| Kind | Glyph | Label | Colour |
|---|---|---|---|
| working | ↻ (spinner) | working | accent |
| busy | ◔ | busy | amber |
| attention | ? | needs you | violet |
| done | ✓ | done | green |
| idle | · | idle | grey |
| exited | ○ | exited | muted |
| bell | ! | bell | red |
| shell | › | shell | dim |

A shell at its prompt is `shell`. `exited` appears only after an agent ran in that pane.

## Components (`bin/hivegrid_ui.py`)

- **`title_row`:** ` ⬡ Name · context` (accent bold, then muted), right-aligned counts and chips, and a rule on the next row. Docked chrome draws it on its own background; panels draw it on `deep`.
- **`chip`:** tab strips and buttons, with 1 cell between chips.
  - Active: `raised`, `bright`, bold.
  - Hover: fades 45% toward `raised`.
  - Inactive: `muted`.
  - Button kinds: `button`, `primary`, `disabled`.
- **`list_row`:** fixed columns:
  - x=0: the ▎ marker when selected;
  - x=2: the state glyph;
  - x=4: the number;
  - then the name, bold when selected;
  - meta right-aligned;
  - an optional second line in `muted`, indented to the name.

  The selected row is a `raised` slab plus the marker plus `bright` text. The slab ends at the table or content width, never at the window edge. Row actions (`Action`, e.g. the dock's ✕) show only on the selected or hovered row; never stack buttons on several rows.
- **Hover:** `U.Hover` handles it: the background fades toward `raised` at 45%, 150 ms in and 200 ms out, with a hand pointer over clickables.
- **`footer`:** always the last row. Keys in `text`, labels in `muted`, two spaces between pairs, and pairs drop from the end when narrow (put `q close` early). Status messages go on the row above with `status_line` (ok, warn, error).
- **`empty_state`:** a `text` headline, a `muted` explanation that wraps (never truncated), and the one action key in the footer.
- **Layout:**
  - Spacing: 1 cell of inner padding, 2 cells between groups, 1 blank row before a section heading.
  - Wide panels cap their content with `content_box` (about 110 to 160 columns, centred); below about 170 columns they use a single column.
  - Dense lists shade every second row (`STRIPE`) once a blank spacer row no longer fits.
- **Overlays and pickers:** a `surface` card with an `edge_hi` frame, a title row, a filter field, list rows and a footer. `bin/hivegrid_picker.py` is the one picker. Never use WezTerm's stock `InputSelector` except as the stock-WezTerm fallback.

## Text

- **Paths:** `U.home_path` (`~` for home, no trailing slash). Shorten them with `fit_text(..., "path")`, which keeps the last components.
- **Glyphs:** geometric Unicode only in text UIs. Nerd Font icons appear only in the bar (as images) and in yazi. No emoji in chrome; strip omp's.
- **Panes:** call a pane by its dock number (`3`, or `tab·number` across tabs), never by its internal pane id.
- **Toasts:** always titled "Hivegrid".

## Chrome panes (fork)

Programs that are window chrome set these user vars (OSC 1337 `SetUserVar`) so the Hivegrid build treats them as chrome:

| User var | Effect |
|---|---|
| `hivegrid_no_focus` | a click does not take the keyboard focus |
| `hivegrid_no_dim` | `inactive_pane_hsb` does not apply |
| `hivegrid_fixed_width` | keeps its columns on resizes and splits |
| `hivegrid_chrome` | stays visible while a grid pane is zoomed |

They also set the pane's default background to their own colour with OSC 11, so the window padding beside them matches. `wezterm/HIVEGRID.md` documents each one.
