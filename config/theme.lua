-- Hivegrid palette and small drawing helpers shared by every module.
-- Dark graphite base; green/amber/red stay semantic for state, blue is the accent.
local wezterm = require 'wezterm'

local M = {}

M.P = {
  deep = '#070a10', -- header band, darker than the terminal so the bar reads as a recessed frame
  base = '#0b0f16', -- terminal background
  surface = '#111722', -- chips, inactive tabs
  pill = '#162030', -- body of a status pill, lighter than the header so pills read as raised
  raised = '#222f45', -- active tab, the pill that needs the eye
  track = '#1c2535', -- empty part of a gauge
  border = '#293345',
  edge_hi = '#4a5d7d', -- lit edge of a raised shape
  edge_lo = '#0a0e15', -- shaded edge
  text = '#e2e8f2',
  bright = '#f5f8fc',
  muted = '#8d9ab0',
  dim = '#7a879d', -- the dimmest text allowed on a pill
  accent = '#78a9ff',
  cyan = '#5cc8f0',
  green = '#4ade80',
  amber = '#f5a524',
  red = '#f87171',
  violet = '#a78bfa',
  grey = '#8590a5',
}

-- Terminal and tab bar colours that are not part of the palette proper.
M.T = {
  selection_bg = '#26395c',
  split = '#1c2433',
  scrollbar_thumb = '#263143',
  button_hover = '#1c2638', -- window button and "+" button hover
  tab_hover = '#172030',
  tab_edge = '#05070b',
  ansi = { '#243044', '#f87171', '#4ade80', '#f5a524', '#78a9ff', '#c4a1f7', '#5cc8f0', '#e2e8f2' },
  brights = { '#5b677d', '#fca5a5', '#86efac', '#fcd34d', '#a5c6ff', '#dac4fa', '#8fdcf5', '#f5f8fc' },
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
  web = { '◍', 'web', 'cyan', 8 },
  other = { '›', 'running', 'dim', 9 },
  shell = { '›', 'shell', 'dim', 10 },
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

-- True when this WezTerm has the web pane (the `hivegrid:web` spawn sentinel, `wezterm cli
-- split-pane --url`, the `hgweb` control sequences): the Hivegrid fork with its web feature,
-- not merely the fork. Stock WezTerm fails the Click probe above; a fork built before the web
-- pane has the Click item but its `wezterm cli split-pane --help` lacks `--url`, which is what
-- the second probe asks. `wezterm cli` loads this config too, so the answer is cached in
-- $XDG_CACHE_HOME/hivegrid/web-probe (read with io.open: no child process while it is valid),
-- keyed by the executables' paths and sizes and the version string (Lua cannot stat a file
-- without a child process; the web feature changes the binary size). Every web entry point
-- (config/web.lua, the "+" menu rows) is gated on this.
local function file_size(path)
  local f = io.open(path, 'rb')
  if not f then return nil end
  local size = f:seek('end')
  f:close()
  return size
end

local function probe_cache_path()
  local base = os.getenv('XDG_CACHE_HOME')
  if not base or base == '' then
    local home = os.getenv('HOME')
    if not home or home == '' then return nil end
    base = home .. '/.cache'
  end
  return base .. '/hivegrid/web-probe'
end

local function cli_has_url_flag()
  local dir = wezterm.executable_dir
  local exe = dir .. '/wezterm'
  local size = file_size(exe)
  local key
  if size then
    key = table.concat({ exe, size, file_size(dir .. '/wezterm-gui') or '-', tostring(wezterm.version) }, '|')
  else
    exe = 'wezterm'
  end
  local path = key and probe_cache_path()
  if path then
    local f = io.open(path, 'rb')
    if f then
      local k, v = f:read('l'), f:read('l')
      f:close()
      if k == key and (v == '1' or v == '0') then return v == '1' end
    end
  end
  -- io.popen, not wezterm.run_child_process: that yields, which a module body (running inside
  -- require, a C call) cannot do.
  local ok, out, success = pcall(function()
    local h = io.popen("'" .. exe:gsub("'", "'\\''") .. "' cli split-pane --help 2>&1", 'r')
    if not h then error('popen failed') end
    local text = h:read('a')
    local closed = h:close()
    return text, closed == true
  end)
  local has = ok and success and (out or ''):find('--url', 1, true) ~= nil
  -- Only an answer is kept; a probe that could not run (no GUI, broken binary) is asked again.
  if path and ok and success then
    local f = io.open(path, 'wb')
    if not f then
      os.execute("mkdir -p '" .. path:gsub('/[^/]*$', ''):gsub("'", "'\\''") .. "' 2>/dev/null")
      f = io.open(path, 'wb')
    end
    if f then
      f:write(key, '\n', has and '1' or '0', '\n')
      f:close()
    end
  end
  return has
end
M.has_web = M.has_click and cli_has_url_flag()

-- The page a web pane shows, from the working directory the pane reports (the fork reports
-- the page URL there; a terminal pane reports a file: URL, or nothing). A Url object is
-- read through tostring (it has host, port and path fields but no href). Returns { href,
-- scheme, host, path } for a non-file URL, nil for a file: URL (unless `allow_file`: a web
-- pane may show a local page), a bare path or no value; `host` keeps a port
-- ("localhost:3000") and user info is dropped.
function M.web_url(cwd, allow_file)
  if cwd == nil then return nil end
  local ok, href = pcall(tostring, cwd)
  if not ok or type(href) ~= 'string' then return nil end
  local scheme, host, path = href:match('^(%a[%w+.-]*)://([^/?#]*)([^?#]*)')
  scheme = scheme or href:match('^(%a[%w+.-]*):')
  if not scheme or scheme == 'table' or (scheme == 'file' and not allow_file) then return nil end
  return { href = href, scheme = scheme, host = (host or ''):gsub('^.*@', ''), path = path or '' }
end

-- What a pane list shows for a page: its host with the port (and path unless that is just
-- "/"); a URL without a host (data:, about:) shows as itself, cut to 40 characters.
function M.web_label(url, with_path)
  if url.host ~= '' then
    return url.host .. ((with_path and url.path ~= '' and url.path ~= '/') and url.path or '')
  end
  local text = url.href ~= '' and url.href or (url.scheme .. ':' .. url.path)
  return (utf8.len(text) or #text) > 40 and (wezterm.truncate_right(text, 39) .. '…') or text
end

-- True for a web pane (the fork sets the user var hivegrid_web on it).
function M.is_web(pane)
  local ok, vars = pcall(function() return pane:get_user_vars() end)
  return ok and type(vars) == 'table' and vars.hivegrid_web == '1'
end

-- The WEZTERM_UNIX_SOCKET of the mux a pane belongs to (so `wezterm cli` aims at the right
-- GUI instance), read from the environment of the pane's foreground process. A pane with no
-- process of its own, a web pane, asks the other terminal panes of its tab instead. nil
-- when none can say.
function M.mux_socket(pane)
  local function of(p)
    local ok, info = pcall(function() return p:get_foreground_process_info() end)
    local f = ok and info and info.pid and io.open('/proc/' .. info.pid .. '/environ', 'rb')
    if not f then return nil end
    local sock = f:read('a'):match('WEZTERM_UNIX_SOCKET=([^\0]+)')
    f:close()
    return sock
  end
  local sock = of(pane)
  if sock then return sock end
  local okt, tab = pcall(function() return pane:tab() end)
  local okp, panes = pcall(function() return okt and tab and tab:panes() or {} end)
  for _, p in ipairs(okp and panes or {}) do
    if p:pane_id() ~= pane:pane_id() and not M.is_web(p) then
      sock = of(p)
      if sock then return sock end
    end
  end
  return nil
end

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
