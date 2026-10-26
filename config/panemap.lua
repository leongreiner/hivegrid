-- Reading-order numbering of the panes of a tab (the digits of the overview dock and the
-- pane picker). No wezterm dependency, so it can be unit-tested with fake panes_with_info
-- data.
--
-- Pane rectangles (left/top/width/height in cells) are not a regular grid: dividers take a
-- cell and independently split rows round differently, so edges are clustered with a
-- small tolerance before panes are ordered.

local M = {}

local BASE_TOL = 2 -- covers the 1-cell divider plus rounding of independent splits

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

return M
