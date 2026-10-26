-- Hivegrid palette and small drawing helpers shared by every module.
-- Dark navy base; green/amber/red stay semantic for state, blue is the accent.
local wezterm = require 'wezterm'

local M = {}

M.P = {
  deep = '#070d19', -- header band, darker than the terminal so the bar reads as a recessed frame
  base = '#0b1220', -- terminal background
  surface = '#111b30', -- chips, inactive tabs
  pill = '#15244a', -- body of a status pill, lighter than the header so pills read as raised
  raised = '#1d3a70', -- active tab, the pill that needs the eye
  track = '#1f2d4d', -- empty part of a gauge
  border = '#1e2a45',
  edge_hi = '#4a6fb0', -- lit edge of a raised shape
  edge_lo = '#0c1630', -- shaded edge
  text = '#c9d6ee',
  bright = '#eaf1ff',
  muted = '#8a9cc2',
  dim = '#7487ad', -- the dimmest text allowed on a pill
  accent = '#4f9dff',
  cyan = '#38bdf8',
  green = '#34d399',
  amber = '#fbbf24',
  red = '#f87171',
  violet = '#a78bfa',
  grey = '#8190ad',
}

-- Terminal and tab bar colours that are not part of the palette proper.
M.T = {
  selection_bg = '#1f3f73',
  split = '#1a2b4d',
  scrollbar_thumb = '#22324f',
  button_hover = '#1b2f55', -- window button and "+" button hover
  tab_hover = '#18274a',
  tab_edge = '#04080f',
  ansi = { '#243252', '#f87171', '#34d399', '#fbbf24', '#4f9dff', '#c084fc', '#38bdf8', '#c9d6ee' },
  brights = { '#5b6b8f', '#fca5a5', '#6ee7b7', '#fcd34d', '#7dbaff', '#d8b4fe', '#7dd3fc', '#eaf1ff' },
}

-- The colour theme chosen in `hivegrid-panel settings` replaces these defaults, key by key.
do
  local S = require('settings')(nil, wezterm)
  for k, v in pairs(S.theme_palette()) do
    if M.P[k] then M.P[k] = v end
  end
  for k, v in pairs(S.theme_terminal()) do
    if M.T[k] then M.T[k] = v end
  end
end

-- The agent state table, shared with bin/hivegrid_ui.py (STATES): keep both in step. `key`
-- names the palette colour, `color` is it resolved for the current theme, `rank` sorts by how
-- much a state needs the eye (1 = most).
M.STATES = {}
for kind, s in pairs {
  attention = { '?', 'needs you', 'violet', 1 },
  bell = { '!', 'bell', 'red', 2 },
  done = { '✓', 'done', 'green', 3 },
  busy = { '◔', 'busy', 'amber', 4 },
  working = { '↻', 'working', 'accent', 5 },
  idle = { '·', 'idle', 'grey', 6 },
  exited = { '○', 'exited', 'muted', 7 },
  other = { '›', 'running', 'dim', 8 },
  shell = { '›', 'shell', 'dim', 9 },
} do
  M.STATES[kind] = { glyph = s[1], text = s[2], key = s[3], color = M.P[s[3]], rank = s[4] }
end

-- Linear blend of two '#rrggbb' colours, t in [0, 1].
function M.lerp(a, b, t)
  local function ch(h, i) return tonumber(h:sub(i, i + 1), 16) end
  local out = '#'
  for i = 2, 6, 2 do
    out = out .. string.format('%02x', math.floor(ch(a, i) + (ch(b, i) - ch(a, i)) * t + 0.5))
  end
  return out
end

-- Draws `pieces` ({ fg, text, bold }) as one run whose background blends from `left`
-- to `right` across its characters: a horizontal gradient is the only shading a
-- one-row tab can show. `first_bg` overrides the first character's background.
function M.gradient(pieces, left, right, first_bg)
  local n = 0
  for _, p in ipairs(pieces) do n = n + (utf8.len(p.text) or #p.text) end
  local items, i = {}, 0
  for _, p in ipairs(pieces) do
    items[#items + 1] = { Foreground = { Color = p.fg } }
    items[#items + 1] = { Attribute = { Intensity = p.bold and 'Bold' or 'Normal' } }
    for _, c in utf8.codes(p.text) do
      i = i + 1
      items[#items + 1] = {
        Background = { Color = (i == 1 and first_bg) or M.lerp(left, right, (i - 0.5) / n) },
      }
      items[#items + 1] = { Text = utf8.char(c) }
    end
  end
  return items
end

-- Cells a wezterm.format item list occupies (every glyph used here is one cell wide).
function M.width(items)
  local n = 0
  for _, it in ipairs(items) do
    if type(it) == 'table' and it.Text then n = n + (utf8.len(it.Text) or #it.Text) end
  end
  return n
end

-- Rounded cap glyphs (Powerline half circles, drawn by WezTerm itself).
M.CAP_L, M.CAP_R = '\u{e0b6}', '\u{e0b4}'

-- Slim progress bar `cells` wide: `fill` over a dimmer track, one ▬ per cell. It is a
-- font glyph, so it sits on the same line as the digits (the box-drawing lines are
-- scaled to the whole pill and ride above them), and no text ever sits on the bar.
-- The cell the fill edge falls in takes a blend of fill and track in proportion, so
-- nothing straddles the boundary. `bg` is the colour the bar sits on; the track is a
-- shade lighter than it.
function M.meter(pct, cells, fill, bg)
  local track = M.lerp(bg, M.P.edge_hi, 0.48)
  local filled = math.max(0, math.min(100, pct)) / 100 * cells
  if pct > 0 and filled < 0.5 then filled = 0.5 end
  local items = { { Background = { Color = bg } } }
  local run, run_color = 0, nil
  local function flush()
    if run == 0 then return end
    items[#items + 1] = { Foreground = { Color = run_color } }
    items[#items + 1] = { Text = string.rep('▬', run) }
    run = 0
  end
  for k = 1, cells do
    local share = math.max(0, math.min(1, filled - (k - 1)))
    local c = share >= 0.95 and fill or (share <= 0.05 and track or M.lerp(track, fill, share))
    if c ~= run_color then flush() end
    run_color = c
    run = run + 1
  end
  flush()
  return items
end

-- A small rounded chip with `text` on `color` (dark text), sitting on background `bg`:
-- the "login" flag of a quota account.
function M.chip(text, color, bg)
  return {
    { Foreground = { Color = color } }, { Background = { Color = bg } }, { Text = M.CAP_L },
    { Foreground = { Color = M.P.deep } }, { Background = { Color = color } },
    { Attribute = { Intensity = 'Bold' } }, { Text = text },
    { Attribute = { Intensity = 'Normal' } },
    { Foreground = { Color = color } }, { Background = { Color = bg } }, { Text = M.CAP_R },
  }
end

-- Thin-line gauge `cells` wide for picker rows (picker.lua column segments): `fill` for the
-- used part in half-cell steps (━ and ╸), `track` for the rest. Returns { { text, color } }.
function M.line(pct, cells, fill, track)
  local halves = math.floor(math.max(0, math.min(100, pct)) / 100 * cells * 2 + 0.5)
  if pct > 0 and halves == 0 then halves = 1 end
  local full, odd = halves // 2, halves % 2
  local segs = {}
  if full + odd > 0 then segs[#segs + 1] = { text = string.rep('━', full) .. string.rep('╸', odd), color = fill } end
  local rest = cells - full - odd
  if rest > 0 then segs[#segs + 1] = { text = string.rep('━', rest), color = track } end
  return segs
end

-- Appends every item of `src` to `dst`.
function M.extend(dst, src)
  for _, it in ipairs(src) do dst[#dst + 1] = it end
  return dst
end

-- True when this WezTerm knows the Hivegrid fork's `Click` format item; stock WezTerm
-- rejects it as an unknown FormatItem, so older binaries get plain, unclickable text.
M.has_click = pcall(wezterm.format, { { Click = 'probe' } })

-- Makes `items` a clickable region reported to the `status-click` event as `id`. A
-- ResetAttributes inside `items` ends the region, so it is re-opened after each one.
function M.click(id, items)
  if not M.has_click then return items end
  local out = { { Click = id } }
  for i, it in ipairs(items) do
    out[#out + 1] = it
    if it == 'ResetAttributes' and i < #items then out[#out + 1] = { Click = id } end
  end
  out[#out + 1] = 'ClickEnd'
  return out
end

-- A rounded pill: cap, one cell of padding, `items`, padding, cap. Caps take `bg` as
-- foreground on the bar; the body paints `bg` behind every item, so the body is
-- re-asserted after each ResetAttributes in `items`.
function M.pill(items, bg)
  local out = {
    { Foreground = { Color = bg } }, { Text = M.CAP_L },
    { Background = { Color = bg } }, { Text = ' ' },
  }
  for _, it in ipairs(items) do
    out[#out + 1] = it
    if it == 'ResetAttributes' then out[#out + 1] = { Background = { Color = bg } } end
  end
  out[#out + 1] = { Text = ' ' }
  out[#out + 1] = 'ResetAttributes'
  out[#out + 1] = { Foreground = { Color = bg } }
  out[#out + 1] = { Text = M.CAP_R }
  out[#out + 1] = 'ResetAttributes'
  return out
end

-- One continuous rounded pill holding `parts` (item lists) with a thin divider between
-- neighbours. Each part leaves the background at `bg` when it ends.
function M.group(parts, bg)
  local body = {}
  local divider = { { Foreground = { Color = M.lerp(bg, M.P.edge_hi, 0.55) } }, { Text = ' │ ' } }
  for i, part in ipairs(parts) do
    if i > 1 then M.extend(body, divider) end
    M.extend(body, part)
  end
  return M.pill(body, bg)
end

-- The mark drawn by the Hivegrid WezTerm fork (option tab_bar_brand): the app icon's
-- seven hexagons, centre in the accent, the ring in the agent state colours of the dock and
-- the hive (theme.STATES: done, working, needs you, working, idle, working), so the logo
-- speaks the one state vocabulary and follows the theme. Sizes in pixels at 96 dpi.
local BRAND_RING = { 'done', 'working', 'attention', 'working', 'idle', 'working' }
local function ring(t, toward)
  local out = {}
  for i, kind in ipairs(BRAND_RING) do out[i] = M.lerp(M.STATES[kind].color, toward, t) end
  return out
end
M.BRAND_MARK = {
  enabled = true,
  size = 30,
  margin_left = 10,
  margin_right = 8,
  colors = {
    center = M.P.accent,
    center_outline = M.lerp(M.P.accent, M.P.bright, 0.5),
    ring = ring(0.3, M.P.deep),
    ring_outline = ring(0.45, M.P.bright),
  },
}

-- The Hivegrid mark for stock WezTerm: the logo's seven hexagons as a row of ⬢ glyphs in
-- the same colours (the ring's states around the accent centre). No text, no pill; air
-- around it so it never touches the first tab. opts.glyph and opts.color switch to a
-- single-colour glyph; opts.pill sits it on a raised pill. Returns wezterm.format items.
local BRAND_CELLS = {}
for i, kind in ipairs { 'done', 'working', 'attention', '', 'idle', 'working', 'working' } do
  BRAND_CELLS[i] = kind == '' and M.P.accent or M.STATES[kind].color
end
function M.brand(opts)
  opts = opts or {}
  local mark = {}
  if opts.glyph or opts.color then
    mark = {
      { Foreground = { Color = opts.color or M.P.accent } },
      { Text = opts.glyph or '\u{f06e1}' },
    }
  else
    for _, c in ipairs(BRAND_CELLS) do
      mark[#mark + 1] = { Foreground = { Color = c } }
      mark[#mark + 1] = { Text = '\u{2b22}' }
    end
  end
  if opts.pill then
    local items = M.pill(mark, M.P.raised)
    items[#items + 1] = { Text = ' ' }
    return items
  end
  local items = { { Text = ' ' } }
  M.extend(items, mark)
  items[#items + 1] = { Text = '  ' }
  return items
end

return M
