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
local EIGHTHS = { '▏', '▎', '▍', '▌', '▋', '▊', '▉' }

-- Rounded progress bar `cells` wide (caps included): `fill` over a rounded track a
-- shade lighter than the pill body `bg`, eighth-cell precision.
function M.gauge(pct, cells, fill, bg)
  local track = M.lerp(bg, M.P.edge_hi, 0.3)
  local inner = cells - 2
  local eighths = math.floor(math.max(0, math.min(100, pct)) / 100 * inner * 8 + 0.5)
  if pct > 0 and eighths == 0 then eighths = 1 end
  local full, part = eighths // 8, eighths % 8
  local items = {
    { Foreground = { Color = eighths > 0 and fill or track } }, { Background = { Color = bg } },
    { Text = M.CAP_L },
  }
  if full > 0 then
    items[#items + 1] = { Background = { Color = fill } }
    items[#items + 1] = { Text = string.rep(' ', full) }
  end
  local rest = inner - full
  if part > 0 then
    items[#items + 1] = { Foreground = { Color = fill } }
    items[#items + 1] = { Background = { Color = track } }
    items[#items + 1] = { Text = EIGHTHS[part] }
    rest = rest - 1
  end
  if rest > 0 then
    items[#items + 1] = { Background = { Color = track } }
    items[#items + 1] = { Text = string.rep(' ', rest) }
  end
  items[#items + 1] = { Foreground = { Color = eighths >= inner * 8 and fill or track } }
  items[#items + 1] = { Background = { Color = bg } }
  items[#items + 1] = { Text = M.CAP_R }
  return items
end

-- Bar `cells` wide (rounded caps included) with `text` laid over it, centred: cells
-- up to the fill carry `fill` as background and dark text, the rest the track and light
-- text, so the text stays legible on both sides of the fill edge. A cell the fill edge
-- cuts through is drawn in eighths when it is blank and flips whole otherwise.
-- Returns wezterm.format items that end on background `bg`.
function M.gauge_text(pct, cells, fill, bg, text, opts)
  opts = opts or {}
  local track = opts.track or M.lerp(bg, M.P.edge_hi, 0.3)
  local on_fill, on_track = opts.on_fill or M.P.deep, opts.on_track or M.P.text
  local inner = cells - 2
  text = text or ''
  if (utf8.len(text) or #text) > inner then text = wezterm.truncate_right(text, inner) end
  local len = utf8.len(text) or #text
  local chars = {}
  for k = 1, inner do chars[k] = ' ' end
  local at = (inner - len) // 2
  for _, c in utf8.codes(text) do
    at = at + 1
    chars[at] = utf8.char(c)
  end
  local eighths = math.floor(math.max(0, math.min(100, pct)) / 100 * inner * 8 + 0.5)
  if pct > 0 and eighths == 0 then eighths = 1 end
  local full, part = eighths // 8, eighths % 8

  local items = {
    { Foreground = { Color = eighths > 0 and fill or track } }, { Background = { Color = bg } },
    { Text = M.CAP_L },
  }
  local run, run_fg, run_bg = '', nil, nil
  local function flush()
    if run == '' then return end
    items[#items + 1] = { Foreground = { Color = run_fg } }
    items[#items + 1] = { Background = { Color = run_bg } }
    items[#items + 1] = { Text = run }
    run = ''
  end
  for k = 1, inner do
    local ch, fg, cbg = chars[k], on_track, track
    if k <= full then
      fg, cbg = on_fill, fill
    elseif k == full + 1 and part > 0 then
      if ch == ' ' then
        ch, fg, cbg = EIGHTHS[part], fill, track
      elseif part >= 4 then
        fg, cbg = on_fill, fill
      end
    end
    if fg ~= run_fg or cbg ~= run_bg then flush() end
    run_fg, run_bg = fg, cbg
    run = run .. ch
  end
  flush()
  items[#items + 1] = { Foreground = { Color = full >= inner and fill or track } }
  items[#items + 1] = { Background = { Color = bg } }
  items[#items + 1] = { Text = M.CAP_R }
  return items
end

-- Thin-line bar for popups: the selector highlights its current row by reversing
-- colours, which turns a half-block bar into a slab but leaves a line readable.
function M.line(pct, cells, fill, track)
  local halves = math.floor(math.max(0, math.min(100, pct)) / 100 * cells * 2 + 0.5)
  if pct > 0 and halves == 0 then halves = 1 end
  local full, odd = halves // 2, halves % 2
  local items = { { Foreground = { Color = fill } } }
  if full > 0 then items[#items + 1] = { Text = string.rep('━', full) } end
  if odd > 0 then items[#items + 1] = { Text = '╸' } end
  local rest = cells - full - odd
  if rest > 0 then
    items[#items + 1] = { Foreground = { Color = track } }
    items[#items + 1] = { Text = string.rep('━', rest) }
  end
  return items
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

-- The Hivegrid mark: the logo's seven hexagons as a row of ⬢ glyphs, centre strong
-- blue and the others coloured by agent state as in the icon (done green, working
-- light blue, needs input light red, waiting grey-blue). No text, no pill; air around it
-- so it never touches the first tab. opts.glyph and opts.color switch to a single-colour
-- glyph; opts.pill sits it on a raised pill. Returns wezterm.format items.
local BRAND_CELLS = {
  '#82f0c3', '#c4e0ff', '#ffbebe', '#1e90ff', '#cddaf0', '#7aa8ee', '#c4e0ff',
}
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
