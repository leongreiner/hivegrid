-- Pure layout -> glyph-grid mapping for the pane minimap and agent numbering.
-- No wezterm dependency, so it can be unit-tested with fake panes_with_info data.
--
-- Pane rectangles (left/top/width/height in cells) are not a regular grid:
-- dividers take a cell and independently split rows round differently. Edges
-- are therefore clustered with a small tolerance, and each pane paints the
-- refined cluster cells it spans. A 3x3 grid gives 3x3 cells, "1 big + 3
-- stacked" gives 2 columns x 3 rows with the big pane repeated, and so on.

local M = {}

local BASE_TOL = 2 -- covers the 1-cell divider plus rounding of independent splits
local TOLS = { BASE_TOL, 4, 8, 16, 32 }

-- Cluster sorted values whose neighbours are within tol. Returns the
-- representative of each cluster and a value -> cluster index map.
local function cluster(values, tol)
  local sorted = {}
  for i, v in ipairs(values) do sorted[i] = v end
  table.sort(sorted)
  local reps, idx, group, prev = {}, {}, {}, nil
  local function flush()
    if #group == 0 then return end
    local sum = 0
    for _, v in ipairs(group) do sum = sum + v end
    reps[#reps + 1] = math.floor(sum / #group)
    for _, v in ipairs(group) do idx[v] = #reps end
    group = {}
  end
  for _, v in ipairs(sorted) do
    if prev and v - prev > tol then flush() end
    group[#group + 1] = v
    prev = v
  end
  flush()
  return reps, idx
end

-- Clusters a pane [start, start+size) covers: its own, plus every later one
-- that begins at least tol before the pane ends (so the divider cell and
-- rounding never make a pane swallow its neighbour's column).
local function span(reps, idx, start, size, tol)
  local k0 = idx[start]
  local k1 = k0
  for k = k0 + 1, #reps do
    if reps[k] < start + size - tol then k1 = k else break end
  end
  return k0, k1
end

local function edges(infos)
  local xs, ys = {}, {}
  for i, p in ipairs(infos) do
    xs[i], ys[i] = p.left, p.top
  end
  return xs, ys
end

-- Agent numbers in reading order (rows top to bottom, then left to right).
-- Returns { [i] = n } for infos[i].
function M.numbering(infos)
  local xs, ys = edges(infos)
  local _, xi = cluster(xs, BASE_TOL)
  local _, yi = cluster(ys, BASE_TOL)
  local order = {}
  for i = 1, #infos do order[i] = i end
  table.sort(order, function(a, b)
    local ya, yb = yi[ys[a]], yi[ys[b]]
    if ya ~= yb then return ya < yb end
    local xa, xb = xi[xs[a]], xi[xs[b]]
    if xa ~= xb then return xa < xb end
    if ys[a] ~= ys[b] then return ys[a] < ys[b] end
    if xs[a] ~= xs[b] then return xs[a] < xs[b] end
    return a < b
  end)
  local number = {}
  for n, i in ipairs(order) do number[i] = n end
  return number
end

local function build(infos, tol)
  local xs, ys = edges(infos)
  local xr, xi = cluster(xs, tol)
  local yr, yi = cluster(ys, tol)
  local cells = {}
  for r = 1, #yr do cells[r] = {} end
  -- Large panes first so a small pane is never hidden under a spanning one.
  local order = {}
  for i = 1, #infos do order[i] = i end
  table.sort(order, function(a, b)
    local aa, ab = infos[a].width * infos[a].height, infos[b].width * infos[b].height
    if aa ~= ab then return aa > ab end
    return a < b
  end)
  for _, i in ipairs(order) do
    local p = infos[i]
    local r0, r1 = span(yr, yi, p.top, p.height, tol)
    local c0, c1 = span(xr, xi, p.left, p.width, tol)
    for r = r0, r1 do
      for c = c0, c1 do cells[r][c] = i end
    end
  end
  return { rows = #yr, cols = #xr, cells = cells, tol = tol }
end

-- Widening the tolerance to fit can merge small panes into one cell; such a
-- grid would hide a pane, so it is not an acceptable picture.
local function shows_every_pane(g, n)
  local owned, count = {}, 0
  for r = 1, g.rows do
    for c = 1, g.cols do
      local i = g.cells[r][c]
      if i and not owned[i] then owned[i], count = true, count + 1 end
    end
  end
  return count == n
end

local function width_of(rows, cols, cw)
  return rows * cols * cw + (rows - 1)
end

-- Build the cell grid for one tab. opts: max_width (default 24), numbers
-- (default true: cells are digit + glyph while <= 9 panes fit). Returns nil
-- when there are no panes or the layout cannot fit max_width.
-- Result: { rows, cols, cells[r][c] = index into infos, cw = chars per
-- cell (2 with a digit, else 1), number[i], width }.
function M.grid(infos, opts)
  opts = opts or {}
  local max_w = opts.max_width or 24
  if #infos == 0 then return nil end
  local with_digit = opts.numbers ~= false and #infos <= 9
  for _, tol in ipairs(TOLS) do
    local g = build(infos, tol)
    if not shows_every_pane(g, #infos) then goto continue end
    for _, cw in ipairs(with_digit and { 2, 1 } or { 1 }) do
      local w = width_of(g.rows, g.cols, cw)
      if w <= max_w then
        g.cw, g.width = cw, w
        g.number = M.numbering(infos)
        return g
      end
    end
    ::continue::
  end
  return nil
end

-- Render a grid as wezterm.format items. style(i) -> { glyph, color, active,
-- zoomed }. Rows are separated by a dim bar since the status line has one row.
function M.render(g, style, opts)
  opts = opts or {}
  local sep = opts.sep_color or '#5a5a5a'
  local numc = opts.number_color or '#8b949e'
  local active_bg = opts.active_bg or '#37373d'
  local items = {}
  for r = 1, g.rows do
    if r > 1 then
      items[#items + 1] = { Foreground = { Color = sep } }
      items[#items + 1] = { Text = '│' }
    end
    for c = 1, g.cols do
      local i = g.cells[r][c]
      if i then
        local s = style(i)
        local attrs = s.active or s.zoomed
        if s.active then
          items[#items + 1] = { Background = { Color = active_bg } }
          items[#items + 1] = { Attribute = { Intensity = 'Bold' } }
        end
        if s.zoomed then items[#items + 1] = { Attribute = { Underline = 'Single' } } end
        if g.cw == 2 then
          items[#items + 1] = { Foreground = { Color = numc } }
          items[#items + 1] = { Text = tostring(g.number[i]) }
        end
        items[#items + 1] = { Foreground = { Color = s.color } }
        items[#items + 1] = { Text = s.glyph }
        if attrs then items[#items + 1] = 'ResetAttributes' end
      else
        items[#items + 1] = { Text = string.rep(' ', g.cw) }
      end
    end
  end
  return items
end

return M
