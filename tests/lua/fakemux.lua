-- A small model of the WezTerm mux for tests that drive `wezterm cli`: every tab is a
-- binary split tree whose geometry follows WezTerm's rules (mux/src/tab.rs):
--   * a split node stores the size of its first child; the second child gets the rest
--     minus one divider cell (a resize keeps nested first children, the second absorbs);
--   * split-pane --percent P gives the new (second) pane floor(dim * P / 100) cells;
--   * adjust-pane-size moves the nearest same-axis split above the ACTIVE pane of the
--     tab (stock WezTerm ignores --pane-id there), first child clamped to [1, dim - 2].
-- Wire: local mux = require('tests.lua.fakemux').new(wz, { cols, rows, root = tree })
--   tree: M.leaf(id) | M.h(first_cols, a, b) | M.v(first_rows, a, b) | M.strip(dir, ids, dim)
--   mux.window / mux.tab: the GUI window and tab (wz.fake) kept in sync with the model
--   mux.log: every cli command (argv after `wezterm cli`); mux.fail(args) -> true fails it
--   mux:geometry(id) -> { left, top, width, height }; mux:split(...), mux:kill(id)
local M = {}

function M.leaf(id) return { id = id } end
function M.h(first, a, b) return { dir = 'h', first = first, a = a, b = b } end
function M.v(first, a, b) return { dir = 'v', first = first, a = a, b = b } end

-- ids side by side ('h') or stacked ('v') in `dim` cells, as equal as integer cells allow,
-- nested the way successive splits of the last pane build them.
function M.strip(dir, ids, dim)
  if #ids == 1 then return M.leaf(ids[1]) end
  local first = (dim - (#ids - 1)) // #ids
  local rest = {}
  for i = 2, #ids do rest[#rest + 1] = ids[i] end
  return { dir = dir, first = first, a = M.leaf(ids[1]), b = M.strip(dir, rest, dim - first - 1) }
end

local Mux = {}
Mux.__index = Mux

local function layout(node, x, y, w, h, out)
  node._x, node._y, node._w, node._h = x, y, w, h
  if node.id then
    out[#out + 1] = { id = node.id, left = x, top = y, width = w, height = h }
    return
  end
  if node.dir == 'h' then
    layout(node.a, x, y, node.first, h, out)
    layout(node.b, x + node.first + 1, y, math.max(0, w - node.first - 1), h, out)
  else
    layout(node.a, x, y, w, node.first, out)
    layout(node.b, x, y + node.first + 1, w, math.max(0, h - node.first - 1), out)
  end
end

-- Leaf of `id` and its ancestors (nearest first) as { node, parent, side } records.
local function path_to(node, id, chain)
  chain = chain or {}
  if node.id then return node.id == id and chain or nil end
  for _, side in ipairs { 'a', 'b' } do
    chain[#chain + 1] = node
    local found = path_to(node[side], id, chain)
    if found then return found end
    chain[#chain] = nil
  end
end

function M.new(wz, opts)
  local self = setmetatable({
    wz = wz, cols = opts.cols or 200, rows = opts.rows or 50, tabs = {}, log = {},
    next_pane = 500, next_tab = 1,
  }, Mux)
  self.fail = opts.fail or function() return false end
  local tab = self:new_tab(opts.root or M.leaf(1))
  tab.gui = wz.fake.tab { id = tab.id }
  self.tab = tab.gui
  self.window = wz.fake.window { tabs = { tab.gui } }
  self:sync()
  wz.stub.on_child = function(argv)
    local i = 1
    if argv[1] == 'env' then i = 3 end
    if argv[i] ~= 'wezterm' or argv[i + 1] ~= 'cli' then return false, '', 'not wezterm cli' end
    local args = {}
    for k = i + 2, #argv do args[#args + 1] = argv[k] end
    return self:exec(args)
  end
  return self
end

function Mux:new_tab(root)
  local t = { id = self.next_tab, root = root, zoomed = nil }
  self.next_tab = self.next_tab + 1
  local leaves = {}
  layout(root, 0, 0, self.cols, self.rows, leaves)
  t.active = leaves[1] and leaves[1].id
  self.tabs[#self.tabs + 1] = t
  return t
end

function Mux:tab_of(id)
  for _, t in ipairs(self.tabs) do
    if t.root and path_to(t.root, id) then return t end
  end
end

function Mux:panes(t)
  local out = {}
  layout(t.root, 0, 0, self.cols, self.rows, out)
  return out
end

function Mux:geometry(id)
  local t = self:tab_of(id)
  if not t then return nil end
  for _, p in ipairs(self:panes(t)) do
    if p.id == id then return p end
  end
end

-- Pushes the model into the GUI side: the first tab's panes_with_info/panes(), and every
-- pane's get_dimensions() (cols, viewport_rows), wherever it is.
function Mux:sync()
  for i, t in ipairs(self.tabs) do
    local rows = {}
    for _, p in ipairs(self:panes(t)) do
      rows[#rows + 1] = {
        id = p.id, left = p.left, top = p.top, width = p.width, height = p.height,
        active = p.id == t.active, zoomed = p.id == t.zoomed,
      }
      local pane = self.wz.mux.get_pane(p.id) or self.wz.fake.pane { id = p.id }
      pane.o.dims = { cols = p.width, viewport_rows = p.height, scrollback_rows = p.height, physical_top = 0 }
    end
    if i == 1 then self.tab:set_rows(rows) end
  end
end

-- Removes leaf `id` from its tab: the sibling takes the parent's place and space.
function Mux:remove(id)
  local t = self:tab_of(id)
  if not t then return false end
  local chain = path_to(t.root, id)
  if #chain == 0 then
    t.root = nil
    for i, x in ipairs(self.tabs) do
      if x == t then table.remove(self.tabs, i) end
    end
  else
    local parent = chain[#chain]
    local sibling = parent.a.id == id and parent.b or parent.a
    for k in pairs(parent) do parent[k] = nil end
    for k, v in pairs(sibling) do parent[k] = v end
  end
  if t.active == id and t.root then
    local leaves = self:panes(t)
    t.active = leaves[1].id
  end
  if t.zoomed == id then t.zoomed = nil end
  return true
end

function Mux:kill(id)
  self:remove(id)
  self:sync()
end

-- Splits `target`, putting pane `id` (moved from wherever it is, or a new pane) right of
-- or below it with `cells` cells or `percent` of the space (default 50).
function Mux:split(target, dir, percent, id, cells)
  if not self:tab_of(target) or target == id then return false end
  if id then
    if self:tab_of(id) and not self:remove(id) then return false end
  else
    self.next_pane = self.next_pane + 1
    id = self.next_pane
  end
  local t = self:tab_of(target)
  local g = self:geometry(target)
  local dim = dir == 'h' and g.width or g.height
  local new = math.max(1, cells or dim * (percent or 50) // 100)
  local first = dim - new - 1
  if first < 1 then
    self:new_tab(M.leaf(id)) -- WezTerm refuses the split; the moved pane is not lost
    return false
  end
  local chain = path_to(t.root, target)
  local leaf
  if #chain == 0 then
    leaf = t.root
  else
    local parent = chain[#chain]
    leaf = parent.a.id == target and parent.a or parent.b
  end
  leaf.id = nil
  leaf.dir, leaf.first, leaf.a, leaf.b = dir, first, M.leaf(target), M.leaf(id)
  return true, id
end

-- A new pane `cols` wide left of `target` (how sidebar.lua docks the explorer view: a
-- leaf split of the leftmost grid pane). Returns the new pane id.
function Mux:insert_left(target, cols)
  local t = self:tab_of(target)
  local chain = path_to(t.root, target)
  local parent = chain[#chain]
  local leaf = not parent and t.root or (parent.a.id == target and parent.a or parent.b)
  self.next_pane = self.next_pane + 1
  local id = self.next_pane
  leaf.id = nil
  leaf.dir, leaf.first, leaf.a, leaf.b = 'h', cols, M.leaf(id), M.leaf(target)
  self:sync()
  return id
end

function Mux:adjust(pane_id, dir, amount)
  local t = self:tab_of(pane_id)
  if not t or t.zoomed then return true end
  local axis = (dir == 'Left' or dir == 'Right') and 'h' or 'v'
  local delta = (dir == 'Right' or dir == 'Down') and amount or -amount
  self:panes(t) -- refresh node sizes
  -- The Hivegrid build resizes the pane it is given; stock WezTerm the tab's active one.
  local chain = path_to(t.root, self.wz.stub.fork and pane_id or t.active)
  for i = #chain, 1, -1 do
    local node = chain[i]
    if node.dir == axis then
      local dim = axis == 'h' and node._w or node._h
      node.first = math.max(1, math.min(dim - 2, node.first + delta))
      return true
    end
  end
  return true
end

local function opt(args, name)
  for i, a in ipairs(args) do
    if a == name then return args[i + 1] end
  end
end

local function has(args, name)
  for _, a in ipairs(args) do
    if a == name then return true end
  end
  return false
end

function Mux:exec(args)
  self.log[#self.log + 1] = args
  if self.fail(args) then return false, '', 'injected failure' end
  local cmd = args[1]
  local function num(name)
    local v = opt(args, name)
    return v and tonumber(v)
  end
  local id = num('--pane-id')
  local ok, out = true, ''
  if cmd == 'list' then
    local list = {}
    for _, t in ipairs(self.tabs) do
      for _, p in ipairs(self:panes(t)) do
        list[#list + 1] = {
          window_id = 0, tab_id = t.id, pane_id = p.id, left_col = p.left, top_row = p.top,
          size = { cols = p.width, rows = p.height }, is_active = p.id == t.active,
          is_zoomed = p.id == t.zoomed,
        }
      end
    end
    out = self.wz.json_encode(list)
  elseif cmd == 'activate-pane' then
    local t = self:tab_of(id)
    ok = t ~= nil
    if t then t.active = id end
  elseif cmd == 'adjust-pane-size' then
    ok = self:adjust(id, args[#args], num('--amount') or 1)
  elseif cmd == 'zoom-pane' then
    local t = self:tab_of(id)
    ok = t ~= nil
    if t and has(args, '--unzoom') then t.zoomed = nil end
  elseif cmd == 'move-pane-to-new-tab' then
    ok = self:tab_of(id) ~= nil and self:remove(id)
    if ok then
      local t = self:new_tab(M.leaf(id))
      out = tostring(t.id) .. '\n'
    end
  elseif cmd == 'split-pane' then
    local dir = has(args, '--bottom') and 'v' or 'h'
    local new
    ok, new = self:split(id, dir, num('--percent'), num('--move-pane-id'), num('--cells'))
    out = ok and (tostring(new) .. '\n') or ''
  elseif cmd == 'activate-tab' then
    ok = true
  elseif cmd == 'kill-pane' then
    ok = self:remove(id)
  else
    ok = false
  end
  self:sync()
  return ok, out, ok and '' or 'failed'
end

-- The log as strings ('split-pane --pane-id 1 --right ...') for readable comparisons.
function Mux:commands(filter)
  local out = {}
  for _, args in ipairs(self.log) do
    if not filter or args[1] == filter then out[#out + 1] = table.concat(args, ' ') end
  end
  return out
end

return M
