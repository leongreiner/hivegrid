-- Pane/layout control for grids of agents. Wire: require('layouts')(config, wezterm)
--   LEADER+r  resize mode      LEADER+p  pane mode
--   LEADER+e  equalize panes   LEADER+b  broadcast typed input (guarded)
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
    { pos = 'left', len = 'cols', dec = 'Left', inc = 'Right' },
    { pos = 'top', len = 'rows', dec = 'Up', inc = 'Down' },
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

  -- Actual divider positions along an axis and the evenly spaced positions they
  -- should have. A pane at [pos, pos+len) is followed by a divider cell at pos+len.
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
    local k = #starts
    local actual, ideal = {}, {}
    for j = 2, k do
      table.insert(actual, starts[j] - 1)
    end
    for j = 1, k - 1 do
      table.insert(ideal, math.floor(j * (total + 1) / k) - 1)
    end
    return actual, ideal, total
  end

  local function ideal_for(x, actual, ideal)
    local best, target = math.huge, nil
    for j, d in ipairs(actual) do
      if math.abs(d - x) < best then
        best, target = math.abs(d - x), ideal[j]
      end
    end
    return target
  end

  local function pane_dividers(p, a, total)
    local xs = {}
    if p[a.pos] + p[a.len] < total then
      table.insert(xs, p[a.pos] + p[a.len])
    end
    if p[a.pos] > 0 then
      table.insert(xs, p[a.pos] - 1)
    end
    return xs
  end

  local function misaligned(panes, a)
    local actual, ideal, total = dividers(panes, a)
    local ids = {}
    for id, p in pairs(panes) do
      for _, x in ipairs(pane_dividers(p, a, total)) do
        if ideal_for(x, actual, ideal) ~= x then
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
        -- Probe by one cell to learn which divider this pane's resize actually moves.
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
          if moved then
            break
          end
        end
        local target = moved and ideal_for(moved.old, actual, ideal)
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
      if not require('sidebar').is_sidebar(p:pane_id()) then
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
        return
      end
    end
    local final = snapshot(cli, ids)
    if final and (#misaligned(final, AXES[1]) > 0 or #misaligned(final, AXES[2]) > 0) then
      toast(window, 'equalize: layout is not a regular grid; got as close as possible')
    end
  end

  local equalize_action = wezterm.action_callback(equalize)
  bind('LEADER', 'e', equalize_action)

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
        if not require('sidebar').is_sidebar(p:pane_id()) then send(window, p) end
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
end
