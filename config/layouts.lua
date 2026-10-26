-- Pane/layout control for grids of agents. Wire: require('layouts')(config, wezterm)
--   LEADER+r  resize mode      LEADER+p  pane mode
--   LEADER+e  equalize panes   LEADER+b  broadcast typed input (guarded)
--   CTRL+SHIFT+A  arrange the panes into the grid that reads best (also automatic when a
--   tidy layout gains or loses one pane)
return function(config, wezterm)
  local act = wezterm.action
  config.keys = config.keys or {}
  config.key_tables = config.key_tables or {}

  local function bind(mods, key, action)
    table.insert(config.keys, { key = key, mods = mods, action = action })
  end

  local function toast(window, msg)
    window:toast_notification('WezTerm layouts', msg, nil, 3000)
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

  -- Equalize ------------------------------------------------------------
  -- Lua cannot resize a pane to an absolute size (AdjustPaneSize is relative and
  -- queued asynchronously), so this drives `wezterm cli adjust-pane-size`, which is
  -- synchronous, and re-reads geometry with `wezterm cli list`. Dividers are snapped
  -- to even positions per axis. A divider can only be moved through a pane whose
  -- nearest same-axis split it is; layouts built by split-pane/omp-grid all qualify.
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

  local function snapshot(cli, ids)
    local ok, out = cli('list', '--format', 'json')
    if not ok then
      return nil
    end
    local panes = {}
    for _, e in ipairs(wezterm.json_parse(out)) do
      if ids[e.pane_id] then
        panes[e.pane_id] = {
          id = e.pane_id, left = e.left_col, top = e.top_row,
          cols = e.size.cols, rows = e.size.rows, zoomed = e.is_zoomed,
        }
      end
    end
    return panes
  end

  -- Actual divider positions along an axis, the evenly spaced positions they should snap
  -- to, the end of the grid and its origin (not 0 when the sidebar bar sits left of it).
  -- A pane at [pos, pos+len) is followed by a divider cell at pos+len. The number of
  -- columns is the most panes side by side on any line across the axis, not the number of
  -- distinct pane starts: independently split rows put their dividers a cell or two apart.
  local function dividers(panes, a)
    local starts, seen, total = {}, {}, 0
    for _, p in pairs(panes) do
      if not seen[p[a.pos]] then
        seen[p[a.pos]] = true
        table.insert(starts, p[a.pos])
      end
      total = math.max(total, p[a.pos] + p[a.len])
    end
    table.sort(starts)
    local actual, ideal = {}, {}
    for j = 2, #starts do
      table.insert(actual, starts[j] - 1)
    end
    local k = 1
    for _, p in pairs(panes) do
      local c, n = p[a.cpos], 0
      for _, q in pairs(panes) do
        if q[a.cpos] <= c and c < q[a.cpos] + q[a.clen] then
          n = n + 1
        end
      end
      k = math.max(k, n)
    end
    local origin = starts[1] or 0
    for j = 1, k - 1 do
      ideal[j] = origin + math.floor(j * (total - origin + 1) / k) - 1
    end
    return actual, ideal, total, origin
  end

  -- The evenly spaced position nearest to divider x.
  local function ideal_for(x, ideal)
    local best, target = math.huge, nil
    for _, d in ipairs(ideal) do
      if math.abs(d - x) < best then
        best, target = math.abs(d - x), d
      end
    end
    return target
  end

  -- The dividers of a pane that lie inside the grid (not against its outer edge, where a
  -- sidebar or the overview dock may sit).
  local function pane_dividers(p, a, total, origin)
    local xs = {}
    if p[a.pos] + p[a.len] < total then
      table.insert(xs, p[a.pos] + p[a.len])
    end
    if p[a.pos] > origin then
      table.insert(xs, p[a.pos] - 1)
    end
    return xs
  end

  local function misaligned(panes, a)
    local _, ideal, total, origin = dividers(panes, a)
    local ids = {}
    for id, p in pairs(panes) do
      for _, x in ipairs(pane_dividers(p, a, total, origin)) do
        -- A cell off is rounding, not misalignment (and would never settle).
        if math.abs((ideal_for(x, ideal) or x) - x) > 1 then
          table.insert(ids, id)
          break
        end
      end
    end
    table.sort(ids)
    return ids
  end

  local function equalize_axis(cli, ids, a)
    for _ = 1, MAX_PASSES do
      local panes = snapshot(cli, ids)
      if not panes then
        return false
      end
      local todo = misaligned(panes, a)
      if #todo == 0 then
        return true
      end
      for _, id in ipairs(todo) do
        local cur = snapshot(cli, ids)
        if not cur then
          return false
        end
        local p = cur[id]
        local actual, ideal = dividers(cur, a)
        local function is_inner(x)
          for _, d in ipairs(actual) do
            if d == x then
              return true
            end
          end
          return false
        end
        -- AdjustPaneSize acts on the active pane, whatever --pane-id says.
        cli('activate-pane', '--pane-id', id)
        -- Probe by one cell to learn which divider this pane's resize actually moves. A
        -- divider against a sidebar or the overview dock is none of our business: undo it.
        local moved
        for _, dir in ipairs { a.inc, a.dec } do
          cli('adjust-pane-size', '--pane-id', id, '--amount', 1, dir)
          local after = snapshot(cli, ids)
          if not after then
            return false
          end
          local q = after[id]
          if q[a.pos] + q[a.len] ~= p[a.pos] + p[a.len] then
            moved = { old = p[a.pos] + p[a.len], new = q[a.pos] + q[a.len] }
          elseif q[a.pos] ~= p[a.pos] then
            moved = { old = p[a.pos] - 1, new = q[a.pos] - 1 }
          end
          if moved and not is_inner(moved.old) then
            cli('adjust-pane-size', '--pane-id', id, '--amount', 1, dir == a.inc and a.dec or a.inc)
            moved = nil
          end
          if moved then
            break
          end
        end
        local target = moved and ideal_for(moved.old, ideal)
        if target and target ~= moved.new then
          local delta = target - moved.new
          cli('adjust-pane-size', '--pane-id', id, '--amount', math.abs(delta),
            delta > 0 and a.inc or a.dec)
        end
      end
    end
    return true
  end

  local function equalize(window, pane)
    local tab = window:active_tab()
    local ids, n = {}, 0
    for _, p in ipairs(tab:panes()) do
      if not require('sidebar').is_dock(p:pane_id()) then
        ids[p:pane_id()] = true
        n = n + 1
      end
    end
    if n < 2 then
      return
    end
    local cli = make_cli(pane)
    local start = snapshot(cli, ids)
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
        break
      end
    end
    for _, a in ipairs(AXES) do
      if not equalize_axis(cli, ids, a) then
        toast(window, 'equalize: wezterm cli failed')
        break
      end
    end
    -- equalize_axis activates the panes it resizes; the keyboard goes back where it was.
    cli('activate-pane', '--pane-id', pane:pane_id())
    local final = snapshot(cli, ids)
    if final and (#misaligned(final, AXES[1]) > 0 or #misaligned(final, AXES[2]) > 0) then
      toast(window, 'equalize: layout is not a regular grid; got as close as possible')
    end
  end

  local equalize_action = wezterm.action_callback(equalize)
  bind('LEADER', 'e', equalize_action)
  local eq_key = require('settings')(config, wezterm).key('equalize', 'CTRL+SHIFT+e')
  bind(eq_key.mods, eq_key.key, equalize_action)

  -- equalize(window, pane) is also called by the overview dock's button.
  local api = { equalize = equalize }

  -- Arrange ---------------------------------------------------------------
  -- Re-tiles the grid panes into the grid that reads best for their number and the window's
  -- shape (5 panes in a row become 3 + 2 in two rows), keeping every process: the panes are
  -- moved, never recreated. They keep their reading order, so the numbers stay.
  -- Moving works in two steps: all panes but the first go to temporary tabs, so each one
  -- is then split into a big, empty area; the first pane's area is cut into the rows, and
  -- each row into its panes, with percentages that give equal sizes.
  local panemap = require 'panemap'
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

  -- The tab's grid panes (as panes_with_info rows) and the size of the area they fill.
  local function grid_of(tab)
    local grid, x0, x1, y0, y1 = {}, math.huge, 0, math.huge, 0
    for _, i in ipairs(tab:panes_with_info()) do
      if not require('sidebar').is_dock(i.pane:pane_id()) then
        grid[#grid + 1] = i
        x0, x1 = math.min(x0, i.left), math.max(x1, i.left + i.width)
        y0, y1 = math.min(y0, i.top), math.max(y1, i.top + i.height)
      end
    end
    return grid, x1 - x0, y1 - y0
  end

  local function arrange(window, pane)
    local tab = window:active_tab()
    local grid, cols, rows = grid_of(tab)
    local n = #grid
    if n < 2 then
      return
    end
    local num = panemap.numbering(grid)
    local order = {}
    for i, info in ipairs(grid) do
      order[num[i]] = info.pane:pane_id()
    end
    local counts = ideal_counts(n, cols, rows)
    local cli = make_cli(pane)
    for _, info in ipairs(grid) do
      if info.is_zoomed then
        cli('zoom-pane', '--pane-id', info.pane:pane_id(), '--unzoom')
      end
    end

    local function pct(x)
      return math.floor(100 * x + 0.5)
    end
    local anchor = order[1]
    local start, s = {}, 1
    for r, c in ipairs(counts) do
      start[r] = s
      s = s + c
    end
    for k = 2, n do
      cli('move-pane-to-new-tab', '--pane-id', order[k])
    end
    local placed, failed = { [anchor] = true }, false
    local function put(target, dir, percent, id)
      local ok = false
      if not failed then
        ok = cli('split-pane', '--pane-id', target, dir, '--percent', percent, '--move-pane-id', id)
      end
      if ok then
        placed[id] = true
      else
        failed = true
      end
      return ok
    end
    local R, first = #counts, { anchor }
    for r = 2, R do
      local id = order[start[r]]
      if put(first[r - 1], '--bottom', pct((R - r + 1) / (R - r + 2)), id) then
        first[r] = id
      end
    end
    for r = 1, R do
      local prev, c = first[r], counts[r]
      for j = 2, c do
        local id = order[start[r] + j - 1]
        if prev and put(prev, '--right', pct((c - j + 1) / (c - j + 2)), id) then
          prev = id
        end
      end
    end
    -- Anything not placed (a split failed) goes back beside the first pane, never lost.
    for k = 2, n do
      if not placed[order[k]] then
        cli('split-pane', '--pane-id', anchor, '--right', '--move-pane-id', order[k])
      end
    end
    cli('activate-pane', '--pane-id', pane:pane_id())
    cli('activate-tab', '--tab-id', tab:tab_id())
    if failed then
      toast(window, 'arrange: could not build the grid; panes were put back side by side')
    end
  end
  api.arrange = arrange
  local arrange_action = wezterm.action_callback(arrange)
  local ar_key = require('settings')(config, wezterm).key('arrange', 'CTRL+SHIFT+a')
  bind(ar_key.mods, ar_key.key, arrange_action)

  -- Auto arrange: when the number of grid panes changes and then holds for two status ticks
  -- (omp-grid is still splitting before that), a tab whose layout was a tidy one, the
  -- ideal grid for the old count or a single row or column, is re-tiled for the new count.
  -- A layout the user built by hand (panes not in aligned rows) is left alone.
  local seen = {} -- tab id -> { n = settled pane count, shape = its rows, pend, ticks }
  wezterm.on('update-status', function(window)
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
    local st = seen[id]
    if not st then
      seen[id] = { n = n, shape = shape_of(grid) }
      return
    end
    if n == st.n then
      st.pend, st.shape = nil, shape_of(grid)
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
    local zoomed = false
    for _, i in ipairs(grid) do
      zoomed = zoomed or i.is_zoomed
    end
    if n < 2 or zoomed then
      st.shape = shape_of(grid)
      return
    end
    -- One pane more or fewer is somebody splitting or closing; a jump (omp-grid building its
    -- layout) is a layout chosen on purpose and stays as it is. A tidy layout is re-tiled
    -- even when its shape is already right: the split left one pane half as big, and rows
    -- with fewer panes would not even out with equalize (it aligns them to the other rows).
    local now = shape_of(grid)
    local tidy = same_counts(prev_shape, ideal_counts(prev_n, cols, rows)) or is_strip(prev_shape)
    if tidy and now and math.abs(n - prev_n) == 1 then
      arrange(window, active)
      grid = grid_of(tab)
    end
    st.shape = shape_of(grid)
  end)

  -- Pane mode -----------------------------------------------------------
  local function once(...)
    return act.Multiple { act.PopKeyTable, ... }
  end
  config.key_tables.pane = {
    { key = 'r', action = act.RotatePanes 'Clockwise' },
    { key = 'R', mods = 'SHIFT', action = act.RotatePanes 'CounterClockwise' },
    { key = 'g', action = once(act.PaneSelect { mode = 'Activate' }) },
    { key = 's', action = once(act.PaneSelect { mode = 'SwapWithActive' }) },
    { key = 'S', mods = 'SHIFT', action = once(act.PaneSelect { mode = 'SwapWithActiveKeepFocus' }) },
    { key = 'x', action = once(act.CloseCurrentPane { confirm = true }) },
    {
      key = 't',
      action = once(wezterm.action_callback(function(window, pane)
        if #window:active_tab():panes() < 2 then
          toast(window, 'Only one pane in this tab')
          return
        end
        pane:move_to_new_tab()
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
        if not require('sidebar').is_dock(p:pane_id()) then send(window, p) end
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
      action = wezterm.action_callback(function(win, p, line)
        if line ~= 'broadcast' then
          return
        end
        wezterm.GLOBAL.broadcast = { tab_id = win:active_tab():tab_id(), window_id = win:window_id() }
        win:perform_action(act.ActivateKeyTable {
          name = 'broadcast', one_shot = false,
        }, p)
      end),
    }, pane)
  end))
  return api
end
