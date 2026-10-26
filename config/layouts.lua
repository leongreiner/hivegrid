-- Pane/layout control for grids of agents. Wire: require('layouts')(config, wezterm)
--   LEADER+r  resize mode      LEADER+p  pane mode
--   LEADER+e  equalize panes   LEADER+b  broadcast typed input (guarded)
--   CTRL+SHIFT+A  arrange the panes into the grid that reads best (also automatic when a
--   tidy layout gains or loses one pane)
-- Everything here works on the grid panes only: the sidebar bar and view and the overview
-- dock (sidebar.is_dock) are never counted, moved or resized.
return function(config, wezterm)
  local act = wezterm.action
  local panemap = require 'panemap'
  config.keys = config.keys or {}
  config.key_tables = config.key_tables or {}

  local function bind(mods, key, action)
    table.insert(config.keys, { key = key, mods = mods, action = action })
  end

  local function toast(window, msg)
    window:toast_notification('Hivegrid', msg, nil, 3000)
  end

  local function is_dock(id)
    return require('sidebar').is_dock(id)
  end

  -- The tab's grid panes (as panes_with_info rows) and the size of the area they fill.
  local function grid_of(tab)
    local grid, x0, x1, y0, y1 = {}, math.huge, 0, math.huge, 0
    for _, i in ipairs(tab:panes_with_info()) do
      if not is_dock(i.pane:pane_id()) then
        grid[#grid + 1] = i
        x0, x1 = math.min(x0, i.left), math.max(x1, i.left + i.width)
        y0, y1 = math.min(y0, i.top), math.max(y1, i.top + i.height)
      end
    end
    return grid, x1 - x0, y1 - y0
  end

  -- Grid panes in reading order (the numbers of the overview dock and the pane picker).
  local function in_order(grid)
    local num, order = panemap.numbering(grid), {}
    for i, info in ipairs(grid) do
      order[num[i]] = info
    end
    return order
  end

  -- Layout work runs `wezterm cli` from callbacks that yield between the calls, so a status
  -- tick (or another key) can arrive half way: `busy` keeps them from interleaving. It is
  -- published as wezterm.GLOBAL.layout_busy for the sidebar and dock, which resize panes too
  -- (AdjustPaneSize acts on the active pane, so interleaved resizes hit the wrong pane).
  local busy = false
  local function exclusive(fn)
    return function(...)
      if busy then
        return
      end
      busy = true
      wezterm.GLOBAL.layout_busy = true
      local ok, err = pcall(fn, ...)
      busy = false
      wezterm.GLOBAL.layout_busy = false
      if not ok then
        wezterm.log_error('layouts: ' .. tostring(err))
      end
    end
  end

  -- Resize mode ---------------------------------------------------------
  -- AdjustPaneSize moves the nearest divider along that axis, not "this pane's
  -- edge": Right/Down push it right/down regardless of which side the pane is on.
  local RESIZE_STEP = 2
  local function resize(dir)
    return act.AdjustPaneSize { dir, RESIZE_STEP }
  end
  config.key_tables.resize = {
    { key = 'LeftArrow', action = resize 'Left' },
    { key = 'h', action = resize 'Left' },
    { key = 'RightArrow', action = resize 'Right' },
    { key = 'l', action = resize 'Right' },
    { key = 'UpArrow', action = resize 'Up' },
    { key = 'k', action = resize 'Up' },
    { key = 'DownArrow', action = resize 'Down' },
    { key = 'j', action = resize 'Down' },
    { key = 'Escape', action = act.PopKeyTable },
    { key = 'Enter', action = act.PopKeyTable },
  }
  -- The timeout is reset by every matching key press, so it only fires when idle.
  bind('LEADER', 'r', act.ActivateKeyTable {
    name = 'resize', one_shot = false, prevent_fallback = true, timeout_milliseconds = 8000,
  })

  -- Equalize --------------------------------------------------------------
  -- Lua cannot resize a pane to an absolute size (AdjustPaneSize is relative and
  -- queued asynchronously), so this drives `wezterm cli adjust-pane-size`, which is
  -- synchronous, and re-reads geometry with `wezterm cli list`. Every divider between two
  -- grid panes is snapped to the evenly spaced position nearest to it. A divider can only
  -- be moved through a pane whose nearest same-axis split it is; layouts built by
  -- split-pane/omp-grid all qualify. (Window resizes keep the proportions by themselves on
  -- the Hivegrid build, which scales every split.)
  local AXES = {
    { pos = 'left', len = 'cols', cpos = 'top', clen = 'rows', dec = 'Left', inc = 'Right' },
    { pos = 'top', len = 'rows', cpos = 'left', clen = 'cols', dec = 'Up', inc = 'Down' },
  }
  local MAX_PASSES = 5

  local cli_bin = wezterm.executable_dir .. '/wezterm'
  do
    local f = io.open(cli_bin, 'rb')
    if f then f:close() else cli_bin = 'wezterm' end
  end

  -- Target the mux this pane belongs to; with several GUI instances `wezterm cli`
  -- otherwise picks one itself and pane ids could refer to the wrong window.
  local function make_cli(pane)
    local sock
    local ok, info = pcall(function() return pane:get_foreground_process_info() end)
    if ok and info and info.pid then
      local f = io.open('/proc/' .. info.pid .. '/environ', 'rb')
      if f then
        sock = f:read('a'):match('WEZTERM_UNIX_SOCKET=([^\0]+)')
        f:close()
      end
    end
    return function(...)
      local cmd = {}
      if sock then
        cmd = { 'env', 'WEZTERM_UNIX_SOCKET=' .. sock }
      end
      table.insert(cmd, cli_bin)
      table.insert(cmd, 'cli')
      for _, a in ipairs { ... } do
        table.insert(cmd, tostring(a))
      end
      return wezterm.run_child_process(cmd)
    end
  end

  -- Pane rectangles keyed by pane id, in the shape `wezterm cli list` gives them.
  local function rect(id, left, top, cols, rows, zoomed)
    return { id = id, left = left, top = top, cols = cols, rows = rows, zoomed = zoomed }
  end

  -- The sidebar view is docked inside the grid, split off the leftmost pane: it counts as
  -- part of that pane, so the grid's columns are measured with it in, and the divider
  -- between the two (the view's own width) is never moved.
  local function fuse(panes, v)
    if v then
      for _, p in pairs(panes) do
        if p.left == v.left + v.cols + 1 and p.top == v.top and p.rows == v.rows then
          p.left, p.cols = v.left, p.cols + v.cols + 1
          break
        end
      end
    end
    return panes
  end

  -- The sidebar view pane of a tab when it sits inside the grid (stock WezTerm: a split of
  -- the leftmost pane); the Hivegrid build docks it as a column of its own beside the grid.
  local function view_of(tab)
    return require('sidebar').inline_view(tab:tab_id())
  end

  -- Rectangles of the grid panes `ids` (and the sidebar view `view`, fused in).
  local function snapshot(cli, ids, view)
    local ok, out = cli('list', '--format', 'json')
    if not ok then
      return nil
    end
    local panes, v = {}, nil
    for _, e in ipairs(wezterm.json_parse(out)) do
      local r = rect(e.pane_id, e.left_col, e.top_row, e.size.cols, e.size.rows, e.is_zoomed)
      if ids[e.pane_id] then
        panes[e.pane_id] = r
      elseif e.pane_id == view then
        v = r
      end
    end
    return fuse(panes, v)
  end

  -- First and one-past-last cell the panes cover along axis a.
  local function extent(panes, a)
    local lo, hi = math.huge, 0
    for _, p in pairs(panes) do
      lo, hi = math.min(lo, p[a.pos]), math.max(hi, p[a.pos] + p[a.len])
    end
    return lo, hi
  end

  -- A pane at [pos, pos+len) is followed by a divider cell at pos+len. The dividers of p
  -- along axis a that have another grid pane on their far side: not the window edge, the
  -- sidebar view or the overview dock, which are none of our business.
  local function pane_dividers(panes, p, a)
    local xs = {}
    local function beside(x, far)
      for _, q in pairs(panes) do
        if q ~= p and far(q) == x and q[a.cpos] < p[a.cpos] + p[a.clen]
            and p[a.cpos] < q[a.cpos] + q[a.clen] then
          return true
        end
      end
      return false
    end
    local e = p[a.pos] + p[a.len]
    if beside(e, function(q) return q[a.pos] - 1 end) then
      xs[#xs + 1] = e
    end
    local s = p[a.pos] - 1
    if beside(s, function(q) return q[a.pos] + q[a.len] end) then
      xs[#xs + 1] = s
    end
    return xs
  end

  -- Layout trees ------------------------------------------------------------
  -- A tree is a pane id (leaf) or a split { dir = 'right' | 'bottom', a = tree, b = tree,
  -- frac }: b goes right of / below a and takes frac of the cells (each divider counted
  -- with the pane before it), so a tree fits whatever area it is given.
  local function first_leaf(t)
    while type(t) == 'table' do
      t = t.a
    end
    return t
  end

  -- Cells of b when a split with frac cuts an extent of s cells (a gets s - b - 1).
  local function cut(frac, s)
    return math.max(1, math.min(s - 2, math.floor(frac * (s + 1) + 0.5) - 1))
  end

  -- items[1..n] in equal parts along dir.
  local function chain(items, dir)
    local function from(i)
      if i == #items then
        return items[i]
      end
      local rest = #items - i
      return { dir = dir, a = items[i], b = from(i + 1), frac = rest / (rest + 1) }
    end
    return from(1)
  end

  -- The tree that reproduces the geometry of `slots` (rects with an id each). Every layout
  -- made of splits can be cut in two by a straight line through dividers only; rows first.
  local function carve(slots)
    if #slots == 1 then
      return slots[1].id
    end
    for _, ax in ipairs { { AXES[2], 'bottom' }, { AXES[1], 'right' } } do
      local a, dir = ax[1], ax[2]
      local lo, hi = extent(slots, a)
      local ends = {}
      for _, s in ipairs(slots) do
        if s[a.pos] + s[a.len] < hi then
          ends[#ends + 1] = s[a.pos] + s[a.len]
        end
      end
      table.sort(ends)
      for _, e in ipairs(ends) do
        local first, second, clean = {}, {}, true
        for _, s in ipairs(slots) do
          if s[a.pos] + s[a.len] <= e then
            first[#first + 1] = s
          elseif s[a.pos] >= e then
            second[#second + 1] = s
          else
            clean = false
            break
          end
        end
        if clean and #first > 0 and #second > 0 then
          local ta, tb = carve(first), carve(second)
          if ta == nil or tb == nil then
            return nil
          end
          return { dir = dir, a = ta, b = tb, frac = (hi - e) / (hi - lo + 1) }
        end
      end
    end
    return nil
  end

  -- Panes side by side along dir in tree t; a split the other way counts as one.
  local function weight(t, dir)
    if type(t) ~= 'table' or t.dir ~= dir then
      return 1
    end
    return weight(t.a, dir) + weight(t.b, dir)
  end

  -- Rectangles of the panes when tree t fills area r, every pane along a split direction
  -- given an equal share (a column of three beside a column of two: equal columns, equal
  -- rows within each).
  local function even_layout(t, r, out)
    if type(t) ~= 'table' then
      out[t] = r
      return out
    end
    local a = t.dir == 'right' and AXES[1] or AXES[2]
    local wa, wb = weight(t.a, t.dir), weight(t.b, t.dir)
    local s = r[a.len]
    local nb = cut(wb / (wa + wb), s)
    local ra, rb = rect(nil, r.left, r.top, r.cols, r.rows), rect(nil, r.left, r.top, r.cols, r.rows)
    ra[a.len] = s - nb - 1
    rb[a.pos], rb[a.len] = r[a.pos] + s - nb, nb
    even_layout(t.a, ra, out)
    return even_layout(t.b, rb, out)
  end

  -- Where every pane goes when the grid is evened out; nil when the panes cannot be read
  -- as splits.
  local function even_rects(panes)
    local slots = {}
    for _, p in pairs(panes) do
      slots[#slots + 1] = p
    end
    table.sort(slots, function(p, q) return p.id < q.id end)
    local tree = carve(slots)
    if not tree then
      return nil
    end
    local x0, x1 = extent(panes, AXES[1])
    local y0, y1 = extent(panes, AXES[2])
    return even_layout(tree, rect(nil, x0, y0, x1 - x0, y1 - y0), {})
  end

  -- Where divider x of pane p belongs in the target rectangles.
  local function goal_of(want, p, x, a)
    local t = want[p.id]
    if not t then
      return nil
    end
    return x == p[a.pos] + p[a.len] and t[a.pos] + t[a.len] or t[a.pos] - 1
  end

  -- Panes with a divider off its target by more than `slack` cells.
  local function misaligned(panes, a, want, slack)
    local ids = {}
    for id, p in pairs(panes) do
      local xs, off = pane_dividers(panes, p, a), {}
      for i, x in ipairs(xs) do
        off[i] = (goal_of(want, p, x, a) or x) - x
      end
      if math.abs(off[1] or 0) > (slack or 0) or math.abs(off[2] or 0) > (slack or 0) then
        table.insert(ids, id)
      end
    end
    table.sort(ids)
    return ids
  end

  local function equalize_axis(cli, snap, a, want, slack)
    for _ = 1, MAX_PASSES do
      local panes = snap()
      if not panes then
        return false
      end
      local todo = misaligned(panes, a, want, slack)
      if #todo == 0 then
        return true
      end
      for _, id in ipairs(todo) do
        local cur = snap()
        if not cur then
          return false
        end
        local p = cur[id]
        local inner = {}
        for _, x in ipairs(pane_dividers(cur, p, a)) do
          inner[x] = true
        end
        -- AdjustPaneSize acts on the active pane, whatever --pane-id says.
        cli('activate-pane', '--pane-id', id)
        -- Probe by one cell to learn which divider this pane's resize actually moves. One
        -- against the sidebar or the overview dock: undo it.
        local moved
        for _, dir in ipairs { a.inc, a.dec } do
          cli('adjust-pane-size', '--pane-id', id, '--amount', 1, dir)
          local after = snap()
          if not after then
            return false
          end
          local q = after[id]
          if q[a.pos] + q[a.len] ~= p[a.pos] + p[a.len] then
            moved = { old = p[a.pos] + p[a.len], new = q[a.pos] + q[a.len] }
          elseif q[a.pos] ~= p[a.pos] then
            moved = { old = p[a.pos] - 1, new = q[a.pos] - 1 }
          end
          if moved and not inner[moved.old] then
            cli('adjust-pane-size', '--pane-id', id, '--amount', 1, dir == a.inc and a.dec or a.inc)
            moved = nil
          end
          if moved then
            break
          end
        end
        local goal = moved and goal_of(want, p, moved.old, a)
        if goal and goal ~= moved.new then
          local delta = goal - moved.new
          cli('adjust-pane-size', '--pane-id', id, '--amount', math.abs(delta),
            delta > 0 and a.inc or a.dec)
        end
      end
    end
    return true
  end

  -- `auto` (the re-even after the grid's area changed) leaves dividers a cell off alone and
  -- stays quiet: a cell is what rounding costs, and the user did not ask.
  local equalize = exclusive(function(window, pane, auto)
    local slack = auto and 1 or 0
    local tab = window:active_tab()
    local ids, n = {}, 0
    for _, p in ipairs(tab:panes()) do
      if not is_dock(p:pane_id()) then
        ids[p:pane_id()] = true
        n = n + 1
      end
    end
    if n < 2 then
      return
    end
    local cli = make_cli(pane)
    local view = view_of(tab)
    local function snap()
      return snapshot(cli, ids, view)
    end
    local start = snap()
    local count = 0
    for _ in pairs(start or {}) do
      count = count + 1
    end
    if count ~= n then
      toast(window, 'equalize: cannot reach this window via wezterm cli')
      return
    end
    for _, p in pairs(start) do
      if p.zoomed then
        cli('zoom-pane', '--pane-id', p.id, '--unzoom')
        start = snap() or start
        break
      end
    end
    local want = even_rects(start)
    if not want then
      toast(window, 'equalize: this layout cannot be read as splits; nothing was moved')
      return
    end
    for _, a in ipairs(AXES) do
      if not equalize_axis(cli, snap, a, want, slack) then
        toast(window, 'equalize: wezterm cli failed')
        break
      end
    end
    -- equalize_axis activates the panes it resizes; the keyboard goes back where it was.
    cli('activate-pane', '--pane-id', pane:pane_id())
    local final = snap()
    if final and not auto and (#misaligned(final, AXES[1], want) > 0
        or #misaligned(final, AXES[2], want) > 0) then
      toast(window, 'equalize: could not move every divider; got as close as possible')
    end
  end)

  local equalize_action = wezterm.action_callback(equalize)
  bind('LEADER', 'e', equalize_action)
  local eq_key = require('settings')(config, wezterm).key('equalize', 'CTRL+SHIFT+e')
  bind(eq_key.mods, eq_key.key, equalize_action)

  -- equalize(window, pane) is also called by the overview dock's button.
  local api = { equalize = equalize }

  -- Rebuilding ------------------------------------------------------------
  -- Arrange, rotate and swap rebuild the grid from a layout tree, keeping every process:
  -- the panes are moved, never recreated. Moving works in two steps: all panes but the
  -- first leaf go to temporary tabs, so the first one fills the grid's area alone; that
  -- area is then cut with split-pane --move-pane-id, b's first leaf moving into each cut.

  -- Puts the grid panes of the active tab into `tree`; `focus` gets the keyboard after.
  local function rebuild(window, pane, tree, focus)
    local tab = window:active_tab()
    local grid = grid_of(tab)
    local cli = make_cli(pane)
    for _, info in ipairs(grid) do
      if info.is_zoomed then
        cli('zoom-pane', '--pane-id', info.pane:pane_id(), '--unzoom')
      end
    end
    local anchor = first_leaf(tree)
    for _, info in ipairs(grid) do
      local id = info.pane:pane_id()
      if id ~= anchor then
        cli('move-pane-to-new-tab', '--pane-id', id)
      end
    end
    local placed, failed = { [anchor] = true }, false
    local function size(id, dir)
      local p = wezterm.mux.get_pane(id)
      local d = p and p:get_dimensions()
      return d and (dir == 'right' and d.cols or d.viewport_rows)
    end
    local function build(t, occupant)
      if type(t) ~= 'table' then
        return
      end
      local id = first_leaf(t.b)
      local s = not failed and size(occupant, t.dir)
      if s and s >= 3 then
        local cells = cut(t.frac, s)
        if cli('split-pane', '--pane-id', occupant, '--' .. t.dir, '--cells', cells, '--move-pane-id', id) then
          placed[id] = true
        end
      end
      failed = failed or not placed[id]
      build(t.a, occupant)
      if placed[id] then
        build(t.b, id)
      end
    end
    build(tree, anchor)
    -- Anything not placed (a split failed) goes back beside the first pane, never lost.
    for _, info in ipairs(grid) do
      local id = info.pane:pane_id()
      if not placed[id] then
        cli('split-pane', '--pane-id', anchor, '--right', '--move-pane-id', id)
      end
    end
    cli('activate-pane', '--pane-id', focus)
    cli('activate-tab', '--tab-id', tab:tab_id())
    return not failed
  end

  -- Arrange ---------------------------------------------------------------
  -- Re-tiles the grid panes into the grid that reads best for their number and the window's
  -- shape (5 panes in a row become 3 + 2 in two rows). They keep their reading order, so
  -- the numbers stay.
  local CELL_ASPECT = 2.1 -- cell height / cell width on screen
  local MIN_PANE_COLS, MIN_PANE_ROWS = 60, 8

  -- Panes per row of the ideal grid for n panes in an area of cols x rows cells: the row
  -- count whose panes come closest to square on screen, few empty slots, no pane narrower
  -- than MIN_PANE_COLS where avoidable. The top rows take the extra panes.
  local function ideal_counts(n, cols, rows)
    local best, best_r
    for r = 1, n do
      local c = math.ceil(n / r)
      local pc = (cols - (c - 1)) / c
      local pr = (rows - (r - 1)) / r
      if r == 1 or pr >= MIN_PANE_ROWS then
        local score = math.abs(math.log(pc / (pr * CELL_ASPECT))) + 0.15 * (r * c - n) / n
        if pc < MIN_PANE_COLS then
          score = score + (MIN_PANE_COLS - pc) / MIN_PANE_COLS
        end
        if not best or score < best then
          best, best_r = score, r
        end
      end
    end
    local counts = {}
    for i = 1, best_r do
      counts[i] = n // best_r + (i <= n % best_r and 1 or 0)
    end
    return counts
  end

  -- Panes per row of the current layout, nil when the panes are not in aligned rows.
  local function shape_of(grid)
    local list = {}
    for _, p in ipairs(grid) do
      list[#list + 1] = p
    end
    table.sort(list, function(a, b)
      if a.top ~= b.top then
        return a.top < b.top
      end
      return a.left < b.left
    end)
    local rows = {}
    for _, p in ipairs(list) do
      local row = rows[#rows]
      if row and math.abs(p.top - row.top) <= 2 then
        if math.abs(p.height - row.height) > 2 then
          return nil
        end
        row.n = row.n + 1
      else
        if row and p.top < row.top + row.height - 1 then
          return nil
        end
        rows[#rows + 1] = { top = p.top, height = p.height, n = 1 }
      end
    end
    local counts = {}
    for i, r in ipairs(rows) do
      counts[i] = r.n
    end
    return counts
  end

  local function same_counts(a, b)
    if not a or not b or #a ~= #b then
      return false
    end
    for i = 1, #a do
      if a[i] ~= b[i] then
        return false
      end
    end
    return true
  end

  -- A single row or a single column.
  local function is_strip(counts)
    if not counts then
      return false
    end
    if #counts == 1 then
      return true
    end
    for _, c in ipairs(counts) do
      if c ~= 1 then
        return false
      end
    end
    return true
  end

  local function arrange_now(window, pane)
    local grid, cols, rows = grid_of(window:active_tab())
    local n = #grid
    if n < 2 then
      return
    end
    local order = in_order(grid)
    local rows_t, k = {}, 1
    for r, c in ipairs(ideal_counts(n, cols, rows)) do
      local ids = {}
      for j = 1, c do
        ids[j] = order[k].pane:pane_id()
        k = k + 1
      end
      rows_t[r] = chain(ids, 'right')
    end
    if not rebuild(window, pane, chain(rows_t, 'bottom'), pane:pane_id()) then
      toast(window, 'arrange: could not build the grid; panes were put back side by side')
    end
  end
  local arrange = exclusive(arrange_now)
  api.arrange = arrange
  local arrange_action = wezterm.action_callback(arrange)
  local ar_key = require('settings')(config, wezterm).key('arrange', 'CTRL+SHIFT+a')
  bind(ar_key.mods, ar_key.key, arrange_action)

  -- Rotate and swap -------------------------------------------------------
  -- WezTerm's RotatePanes and PaneSelect work on every pane of the tab, so they would move
  -- the sidebar and the dock into the grid. These rebuild the grid with the same geometry
  -- and the panes in other slots instead. `assign(order)` gets the grid panes in reading
  -- order and returns the pane for each slot, or nil to leave things as they are.
  local function permute(window, pane, assign, focus)
    local order = in_order(grid_of(window:active_tab()))
    if #order < 2 then
      return
    end
    local ids = assign(order)
    if not ids then
      return
    end
    local slots = {}
    for i, info in ipairs(order) do
      slots[i] = rect(ids[i], info.left, info.top, info.width, info.height)
    end
    local tree = carve(slots)
    if not tree then
      toast(window, 'This layout cannot be rebuilt; nothing was moved')
      return
    end
    if not rebuild(window, pane, tree, focus or pane:pane_id()) then
      toast(window, 'Could not rebuild the layout; panes were put back side by side')
    end
  end

  -- Clockwise moves every pane to the next slot in reading order, the last to the first.
  local function rotate(step)
    return wezterm.action_callback(exclusive(function(window, pane)
      permute(window, pane, function(order)
        local n, ids = #order, {}
        for i = 1, n do
          ids[i] = order[(i - 1 - step) % n + 1].pane:pane_id()
        end
        return ids
      end)
    end))
  end

  local function home(path)
    local h = wezterm.home_dir
    if path:sub(1, #h) == h then
      return '~' .. path:sub(#h + 1)
    end
    return path
  end

  local function cwd_of(p)
    local ok, u = pcall(function() return p:get_current_working_dir() end)
    if not ok or not u then
      return ''
    end
    local path = type(u) == 'string' and u:gsub('^file://[^/]*', '') or u.file_path or ''
    return home((path:gsub('(.)/$', '%1')))
  end

  -- Chooser over the tab's grid panes (the Hivegrid picker, picker.lua): each row's digit is
  -- its reading-order number, the dock's digit. `then_do(window, pane, id)`.
  local function choose(title, verb, then_do)
    return wezterm.action_callback(function(window, pane)
      local order = in_order(grid_of(window:active_tab()))
      if #order < 2 then
        toast(window, 'Only one pane in this tab')
        return
      end
      local rows = {}
      for n, info in ipairs(order) do
        local p = info.pane
        local here = p:pane_id() == pane:pane_id()
        rows[n] = {
          id = tostring(p:pane_id()), key = tostring(n),
          cols = { { text = cwd_of(p) }, { text = p:get_title(), color = 'muted' } },
          meta = here and 'here' or nil, meta_color = 'accent',
        }
      end
      require('picker').open(window, pane, {
        title = title, crumb = #order .. ' panes', verb = verb, rows = rows,
        selected = tostring(pane:pane_id()),
      }, function(win, _, id)
        then_do(win, pane, tonumber(id))
      end)
    end)
  end

  local select_pane = choose('Go to pane', 'go',
    function(_, _, id)
      local target = wezterm.mux.get_pane(id)
      if target then
        target:activate()
      end
    end)

  -- 's' keeps the keyboard on the moved pane, 'S' on the slot (the pane that came in).
  local function swap(keep_slot)
    return choose('Swap with pane', 'swap',
      exclusive(function(window, pane, id)
        if id == pane:pane_id() then
          return
        end
        permute(window, pane, function(order)
          local ids = {}
          for i, info in ipairs(order) do
            local cur = info.pane:pane_id()
            ids[i] = cur == id and pane:pane_id() or cur == pane:pane_id() and id or cur
          end
          return ids
        end, keep_slot and id or pane:pane_id())
      end))
  end

  -- Even grid: every divider where equalize would put it, give or take the cell a window
  -- resize rounds off.
  local function is_even(tab, grid)
    local panes, view, v = {}, view_of(tab), nil
    for _, i in ipairs(tab:panes_with_info()) do
      local id = i.pane:pane_id()
      if id == view then
        v = rect(id, i.left, i.top, i.width, i.height)
      end
    end
    for _, i in ipairs(grid) do
      local id = i.pane:pane_id()
      panes[id] = rect(id, i.left, i.top, i.width, i.height)
    end
    fuse(panes, v)
    local want = even_rects(panes)
    return want ~= nil and #misaligned(panes, AXES[1], want, 1) == 0
      and #misaligned(panes, AXES[2], want, 1) == 0
  end

  -- Auto arrange: when the number of grid panes changes and then holds for two status ticks
  -- (omp-grid is still splitting before that), a tab whose layout was a tidy one, the
  -- ideal grid for the old count or a single row or column, is re-tiled for the new count.
  -- A layout the user built by hand (panes not in aligned rows) is left alone.
  -- Re-even: when the grid's area changes with the same panes (the sidebar view or the dock
  -- opened or closed beside it; a window resize on stock WezTerm), WezTerm gives or takes
  -- the cells at one end of the tree; a grid that was even is evened out again.
  local seen = {} -- tab id -> { n = settled pane count, shape = its rows, area, even, pend, ticks }
  wezterm.on('update-status', function(window)
    -- The sidebar opening, closing or replacing its view is a layout change in progress.
    if busy or wezterm.GLOBAL.sidebar_busy then
      return
    end
    local tab = window:active_tab()
    local active = window:active_pane()
    if not tab or not active then
      return
    end
    local grid, cols, rows = grid_of(tab)
    local on_tab = false
    for _, p in ipairs(tab:panes()) do
      on_tab = on_tab or p:pane_id() == active:pane_id()
    end
    if not on_tab then
      return -- an overlay (panel) is showing
    end
    local id, n = tab:tab_id(), #grid
    local zoomed = false
    for _, i in ipairs(grid) do
      zoomed = zoomed or i.is_zoomed
    end
    local st = seen[id]
    local function settle()
      st.shape, st.area, st.even = shape_of(grid), cols .. 'x' .. rows, is_even(tab, grid)
    end
    if not st then
      st = { n = n }
      seen[id] = st
      settle()
      return
    end
    if n == st.n then
      st.pend = nil
      if zoomed then
        return
      end
      if n > 1 and st.even and st.area ~= cols .. 'x' .. rows then
        equalize(window, active, true)
        grid, cols, rows = grid_of(tab)
      end
      settle()
      return
    end
    if st.pend ~= n then
      st.pend, st.ticks = n, 1
      return
    end
    st.ticks = st.ticks + 1
    if st.ticks < 3 then
      return
    end
    local prev_n, prev_shape = st.n, st.shape
    st.n, st.pend = n, nil
    -- One pane more or fewer is somebody splitting or closing; a jump (omp-grid building its
    -- layout) is a layout chosen on purpose and stays as it is. A tidy layout is re-tiled
    -- even when its shape is already right: the new pane must land at its reading-order
    -- place, and the split left it half as big as the others.
    local now = shape_of(grid)
    local tidy = same_counts(prev_shape, ideal_counts(prev_n, cols, rows)) or is_strip(prev_shape)
    if n >= 2 and not zoomed and tidy and now and math.abs(n - prev_n) == 1 then
      arrange(window, active)
      grid, cols, rows = grid_of(tab)
    end
    settle()
  end)

  -- Pane mode -----------------------------------------------------------
  local function once(...)
    return act.Multiple { act.PopKeyTable, ... }
  end
  config.key_tables.pane = {
    { key = 'r', action = rotate(1) },
    { key = 'R', mods = 'SHIFT', action = rotate(-1) },
    { key = 'g', action = once(select_pane) },
    { key = 's', action = once(swap(false)) },
    { key = 'S', mods = 'SHIFT', action = once(swap(true)) },
    { key = 'x', action = once(act.CloseCurrentPane { confirm = true }) },
    {
      key = 't',
      action = once(wezterm.action_callback(function(window, pane)
        if is_dock(pane:pane_id()) or #grid_of(window:active_tab()) < 2 then
          toast(window, 'Only one pane in this tab')
          return
        end
        -- The pane is followed to its new tab, like tmux's break-pane.
        local tab = pane:move_to_new_tab()
        if tab then
          tab:activate()
        end
      end)),
    },
    { key = 'z', action = once(act.TogglePaneZoomState) },
    { key = 'e', action = once(equalize_action) },
    { key = 'Escape', action = act.PopKeyTable },
    { key = 'Enter', action = act.PopKeyTable },
    { key = 'q', action = act.PopKeyTable },
  }
  bind('LEADER', 'p', act.ActivateKeyTable {
    name = 'pane', one_shot = false, prevent_fallback = true, timeout_milliseconds = 8000,
  })

  -- Broadcast -----------------------------------------------------------
  -- State: wezterm.GLOBAL.broadcast = { tab_id, window_id } while active, nil
  -- otherwise. The key table 'broadcast' is the authority: the parent should show
  -- a warning when window:active_key_table() == 'broadcast'.
  wezterm.GLOBAL.broadcast = nil

  local function broadcast_off(window, pane)
    wezterm.GLOBAL.broadcast = nil
    if window:active_key_table() == 'broadcast' then
      window:perform_action(act.PopKeyTable, pane)
    end
  end

  -- Keys are fanned out to the panes of the tab that is active now; leaving the
  -- tab the mode was started in ends it instead of typing into another tab.
  local function fanout(send)
    return wezterm.action_callback(function(window, pane)
      local state = wezterm.GLOBAL.broadcast
      local tab = window:active_tab()
      if not state or state.tab_id ~= tab:tab_id() then
        broadcast_off(window, pane)
        toast(window, 'Broadcast ended: tab changed')
        return
      end
      for _, p in ipairs(tab:panes()) do
        if not is_dock(p:pane_id()) then send(window, p) end
      end
    end)
  end
  local function text(s)
    return fanout(function(_, p) p:send_text(s) end)
  end
  local function key(k, mods)
    return fanout(function(window, p)
      window:perform_action(act.SendKey { key = k, mods = mods }, p)
    end)
  end

  local bt = {}
  local function add(k, mods, action)
    table.insert(bt, { key = k, mods = mods, action = action })
  end
  for c = 32, 126 do
    local ch = string.char(c)
    add(ch, 'NONE', text(ch))
    -- Shifted symbols arrive with SHIFT still set; letters are normalised without it.
    if not ch:match('%w') then
      add(ch, 'SHIFT', text(ch))
    end
  end
  for c = string.byte('a'), string.byte('z') do
    local ch = string.char(c)
    add(ch, 'CTRL', key(ch, 'CTRL'))
  end
  for _, k in ipairs { 'Enter', 'Backspace', 'Tab', 'Delete', 'Home', 'End', 'PageUp', 'PageDown',
    'LeftArrow', 'RightArrow', 'UpArrow', 'DownArrow', 'Escape' } do
    add(k, 'NONE', key(k, 'NONE'))
  end
  add('Enter', 'SHIFT', key('Enter', 'SHIFT'))
  add('Enter', 'ALT', key('Enter', 'ALT'))
  add('Tab', 'SHIFT', key('Tab', 'SHIFT'))
  add('LeftArrow', 'CTRL', key('LeftArrow', 'CTRL'))
  add('RightArrow', 'CTRL', key('RightArrow', 'CTRL'))
  -- The clipboard is not readable from Lua, so a paste cannot be fanned out.
  add('v', 'CTRL', wezterm.action_callback(function(window)
    toast(window, 'Paste is not broadcast; type the text or leave broadcast mode')
  end))
  add('b', 'LEADER', wezterm.action_callback(broadcast_off))
  add('Escape', 'CTRL|SHIFT', wezterm.action_callback(broadcast_off))
  config.key_tables.broadcast = bt

  -- Typing an exact word keeps a stray LEADER+b from ever arming it.
  bind('LEADER', 'b', wezterm.action_callback(function(window, pane)
    if window:active_key_table() == 'broadcast' then
      broadcast_off(window, pane)
      return
    end
    window:perform_action(act.PromptInputLine {
      description = 'Type "broadcast" to send all typing to every pane of this tab (Enter to confirm, Esc to cancel)',
      -- The callback's own pane is the prompt's overlay, which is gone by then: the key
      -- table goes to the pane LEADER+b was pressed in.
      action = wezterm.action_callback(function(win, _, line)
        if line == nil then
          return
        end
        if line ~= 'broadcast' then
          toast(win, 'Broadcast not started: type the word broadcast')
          return
        end
        wezterm.GLOBAL.broadcast = { tab_id = win:active_tab():tab_id(), window_id = win:window_id() }
        win:perform_action(act.ActivateKeyTable {
          name = 'broadcast', one_shot = false,
        }, pane)
      end),
    }, pane)
  end))
  return api
end
