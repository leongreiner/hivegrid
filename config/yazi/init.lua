-- The Hivegrid explorer: yazi docked in the sidebar, usually 40-60 columns wide. The
-- user-var plumbing to WezTerm lives in plugins/hivegrid.yazi (also bound to keys in
-- keymap.toml); the colours come from the theme.toml config/explorer.lua generates.
local hg = require('hivegrid')

-- Double click on an entry asks WezTerm (the user var hivegrid_open_dir, handled in
-- sidebar.lua) to open a terminal in that folder, or in the file's folder.
-- yazi's own click handler only hovers the entry (right click also opens it), so a double
-- click does nothing by itself in yazi.
local DOUBLE_CLICK_SECS = 0.4

-- Matched by screen cell, not by entry: a click in the parent column navigates, so the
-- same cell shows a different entry on the second click.
local last = {}

local click = Entity.click
function Entity:click(event, up)
  click(self, event, up)
  if up or not event.is_left then return end

  local now = ya.time()
  if last.x == event.x and last.y == event.y and now - last.t <= DOUBLE_CLICK_SECS then
    local path = last.path
    last = {}
    hg.open_dir(path)
  else
    last = { x = event.x, y = event.y, t = now, path = tostring(self._file.url.path) }
  end
end

-- Resize cursor over the column borders (the rails dragged to resize the parent / current /
-- preview columns). WezTerm shows the pointer shape an application requests with OSC 22
-- (the Hivegrid build); stock WezTerm ignores it. Yazi enables button-event tracking only,
-- so any-event tracking (1003) is switched on here to receive hover events; with it left
-- on, a program yazi suspends to (a shell command) would see stray mouse reports.
local pointer = ''
local function set_pointer(name)
  if name == pointer then return end
  pointer = name
  hg.tty_write('\27]22;' .. name .. '\27\\')
end

hg.tty_write('\27[?1003h')

local move = Root.move
function Root:move(event)
  move(self, event)
  local c = ya.child_at(ui.Rect { x = event.x, y = event.y }, self:reflow())
  set_pointer(c and (c._id == 'rail-left' or c._id == 'rail-right') and 'ew-resize' or '')
end

-- Column widths. yazi's default ratio, 1:4:3, leaves the parent column 6 cells in the
-- default 52-column sidebar: one letter of a name. Until a rail is dragged (which sets
-- rt.mgr.ratio to cell counts), the ratio follows the width instead: below TINY only the
-- current column is left; below NARROW the parent column is dropped (the header shows the
-- path) and current : preview is 3 : 2; below WIDE the parent column gets 1/6.
local TINY, NARROW, WIDE = 32, 64, 100
local DEFAULT = { 1, 4, 3 }

local function ratio(w)
  local r = rt.mgr.ratio
  if r[1] ~= DEFAULT[1] or r[2] ~= DEFAULT[2] or r[3] ~= DEFAULT[3] then return r end
  if w < TINY then return { 0, 1, 0 } end
  if w < NARROW then return { 0, 3, 2 } end
  if w < WIDE then return { 1, 3, 2 } end
  return r
end

function Tab:layout()
  local r = ratio(self._area.w)
  local all = r[1] + r[2] + r[3]
  self._chunks = ui.Layout()
    :direction(ui.Layout.HORIZONTAL)
    :constraints({
      ui.Constraint.Ratio(r[1], all),
      ui.Constraint.Ratio(r[2], all),
      ui.Constraint.Ratio(r[3], all),
    })
    :split(self._area)
end

-- yazi's rail drag gives every column at least one cell, so a drag beside a dropped column
-- would bring it back one cell wide; it stays dropped instead.
local drag = Rail.drag
function Rail:drag(event)
  local c = self._chunks
  drag(self, event)
  local r = rt.mgr.ratio
  local fixed = { c[1].w == 0 and 0 or r[1], r[2], c[3].w == 0 and 0 or r[3] }
  if fixed[1] ~= r[1] or fixed[3] ~= r[3] then
    rt.mgr.ratio = fixed
    ui.render()
  end
end

-- Docked chrome (bar, git view, dock, explorer) shares one look: a `surface` body and a
-- title row " ⬡ <View> · …" with a rule under it. The palette comes from palette.lua, which
-- config/explorer.lua writes beside the theme; without it (yazi started outside Hivegrid)
-- the terminal's background and a plain one-row header stay. The surface is the pane's
-- default background (OSC 11), so everything yazi leaves unpainted takes it, the window
-- padding beside the pane included; the pane closes with yazi, so it is never reset. Like
-- the bar it is chrome, so the user var hivegrid_no_dim keeps the Hivegrid build from
-- dimming it while another pane has focus.
local ok, palette = pcall(dofile, (os.getenv('YAZI_CONFIG_HOME') or '') .. '/palette.lua')
if not ok or type(palette) ~= 'table' then palette = nil end
if palette then
  hg.tty_write('\27]11;' .. palette.surface .. '\27\\')
  hg.set_var('hivegrid_no_dim', '1')
end
local TITLE, SEP = ' ⬡ Files', ' · '
local TITLE_W = utf8.len(TITLE .. SEP)

-- `a` moved `t` of the way toward `b` (#rrggbb), like hivegrid_ui.mix.
local function mix(a, b, t)
  local out = '#'
  for i = 2, 6, 2 do
    local x, y = tonumber(a:sub(i, i + 1), 16), tonumber(b:sub(i, i + 1), 16)
    out = out .. string.format('%02x', math.floor(x + (y - x) * t + 0.5))
  end
  return out
end

function Root:layout()
  self._chunks = ui.Layout()
    :direction(ui.Layout.VERTICAL)
    :constraints({
      ui.Constraint.Length(palette and 2 or 1),
      ui.Constraint.Length(Tabs.height()),
      ui.Constraint.Fill(1),
      ui.Constraint.Length(1),
    })
    :split(self._area)
end

-- The title row of every Hivegrid view (hivegrid_ui.title_row): " ⬡ Files" in accent bold,
-- " · <path>" in muted (yazi already writes ~ for home and no trailing slash). The title's
-- cells come off the room the path may take (it is cut from the left).
local cwd = Header.cwd
function Header:cwd()
  self._right_width = self._right_width + TITLE_W
  local span = cwd(self)
  self._right_width = self._right_width - TITLE_W
  if span == '' then return span end
  if not palette then return ui.Line { ui.Span(TITLE .. SEP):style(th.mgr.cwd), span } end
  return ui.Line {
    ui.Span(TITLE):style(ui.Style():fg(palette.accent):bold()),
    ui.Span(SEP):style(ui.Style():fg(palette.muted)),
    span:style(ui.Style():fg(palette.muted)),
  }
end

-- The rule under it in the shared rule colour (hivegrid_ui.rule_color: 45% of the way from
-- the background to edge_hi), the same as the git view's and the dock's.
local header_redraw = Header.redraw
function Header:redraw()
  local out = header_redraw(self)
  if palette then
    local a = self._area
    out[#out + 1] = ui.Line(string.rep('─', a.w)):area(a { y = a.y + 1, h = 1 })
      :style(ui.Style():fg(mix(palette.surface, palette.edge_hi, 0.45)))
  end
  return out
end

-- Status line: the hovered name is cut to the room the right-hand pills leave instead of
-- running under them, and the permissions are dropped when the sidebar is too narrow for
-- them and a name.
local perm = Status.perm
function Status:perm()
  if self._area.w < NARROW then return '' end
  return perm(self)
end

function Status:name()
  local h = self._current.hovered
  if not h then return '' end
  local used = ui.Line { self:mode(), self:length() }:width()
  local room = self._area.w - used - self:children_redraw(self.RIGHT):width() - 2
  if room < 1 then return '' end
  return ' ' .. ui.truncate(ui.printable(h.name), { max = room })
end
