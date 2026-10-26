-- overview: the LEADER+o dock (open/close, width, focus), its auto-open and self-cleanup on
-- status ticks, the rows file the dock draws, the bar flag, and the dock's click requests.
local T = require 'tests.lua.t'

local T0 = 1750000000

-- Builds the module with fake agentstatus / sidebar cli / layouts. The fake agentstatus
-- returns one row per grid pane of the tab, marked active like the pane.
local function setup(wz, rows, o)
  o = o or {}
  T.clock(T0)
  if o.fork then wz.stub.fork = true end
  local calls = { cli = {}, restore = 0, equalize = {}, arrange = {} }
  local side = require('sidebar')
  local sb = {
    is_dock = side.is_dock,
    is_sidebar = side.is_sidebar,
    make_cli = function(_)
      return function(...)
        local a = table.pack(...)
        local argv = {}
        for i = 1, a.n do argv[i] = tostring(a[i]) end
        calls.cli[#calls.cli + 1] = argv
        if o.cli then return o.cli(argv) end
        if argv[1] == 'split-pane' then return true, (o.split_id or 77) .. '\n' end
        return true, ''
      end
    end,
    restore = function() calls.restore = calls.restore + 1 end,
  }
  local layouts = {
    equalize = function(_, p) calls.equalize[#calls.equalize + 1] = p:pane_id() end,
    arrange = function(_, p) calls.arrange[#calls.arrange + 1] = p:pane_id() end,
  }
  local as = {
    overview = function(_, tab_id)
      local out = {}
      for i, info in ipairs(wz.mux.get_tab(tab_id):panes_with_info()) do
        local id = info.pane:pane_id()
        if not side.is_dock(id) then
          out[#out + 1] = { id = id, num = i, kind = 'idle', active = info.is_active }
        end
      end
      return out
    end,
  }
  local config = { keys = {} }
  require('overview')(config, wz, as, sb, layouts)
  local win, tab = wz.fake.grid(rows, o.wopts)
  return win, tab, calls, config
end

local function toggle_key(config)
  for _, k in ipairs(config.keys) do
    if k.key == 'o' and k.mods == 'LEADER' then return k.action end
  end
  error('no LEADER+o')
end

local function press(wz, config, win, pane) wz.stub.perform(toggle_key(config), win, pane) end

local function tick(wz, win, n)
  for _ = 1, n or 1 do wz.emit('update-status', win, win:active_pane()) end
end

local function splits(calls)
  local out = {}
  for _, c in ipairs(calls.cli) do
    if c[1] == 'split-pane' then out[#out + 1] = c end
  end
  return out
end

local function rec(wz, tab) return (wz.GLOBAL.overview or {})[tostring(tab:tab_id())] end
local function state_file(id) return os.getenv('XDG_RUNTIME_DIR') .. '/hivegrid-overview-' .. id .. '.json' end
local function bin(wz) return wz.home_dir .. '/.local/bin/hivegrid-overview' end

local function two_wide(wz, extra)
  local a, b = wz.fake.pane { id = 1 }, wz.fake.pane { id = 2 }
  local rows = {
    { pane = a, left = 0, top = 0, width = 99, height = 40, active = true },
    { pane = b, left = 100, top = 0, width = 100, height = 40 },
  }
  for _, r in ipairs(extra or {}) do rows[#rows + 1] = r end
  return rows, a, b
end

-- Opening -----------------------------------------------------------------------------------

T.test('LEADER+o splits the top-right grid pane at 18% of the window and keeps the keyboard', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, calls, config = setup(wz, rows)
  press(wz, config, win, a)
  T.eq(splits(calls), { { 'split-pane', '--right', '--cells', '36', '--pane-id', '2', '--', bin(wz) } })
  T.eq(rec(wz, tab), { pane = 77, last = 1 })
  T.eq(wz.stub.activations, { 1 }, 'focus back on the pane LEADER+o came from')
  T.eq(calls.restore, 1, 'the bar is put back at once')
  local doc = wz.json_parse(T.read(state_file(77)))
  local ids, active = {}, {}
  for i, r in ipairs(doc.panes) do ids[i], active[i] = r.id, r.active end
  T.eq({ ids, active }, { { 1, 2 }, { true, false } })
  T.ok(type(doc.pal) == 'table' and doc.pal.accent, 'palette for the dock')
end)

T.test('a remembered width is clamped to at least 30 columns and at most a third of the window', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, calls, config = setup(wz, rows)
  wz.GLOBAL.overview_width = 5
  press(wz, config, win, a)
  T.eq(splits(calls)[1][4], '30')
  wz.GLOBAL.overview = nil
  wz.GLOBAL.overview_width = 150
  press(wz, config, win, a)
  T.eq(splits(calls)[2][4], '66')
  T.ok(rec(wz, tab), 'opened')
end)

T.test('the default width is 18% of the window, 30 to 48 columns', function(wz)
  for _, c in ipairs { { 160, '30' }, { 233, '41' }, { 320, '48' } } do
    local a, b = wz.fake.pane { id = 1 }, wz.fake.pane { id = 2 }
    local half = c[1] // 2
    local win, _, calls, config = setup(wz, {
      { pane = a, left = 0, width = half - 1, active = true }, { pane = b, left = half, width = c[1] - half },
    })
    press(wz, config, win, a)
    T.eq(splits(calls)[1][4], c[2], c[1] .. ' columns')
    wz.GLOBAL.overview = nil
  end
end)

T.test('a dragged width scales with the window it is opened in', function(wz)
  local rows, a = two_wide(wz) -- 200 columns
  local win, _, calls, config = setup(wz, rows)
  wz.GLOBAL.overview_width, wz.GLOBAL.overview_width_of = 30, 100
  press(wz, config, win, a)
  T.eq(splits(calls)[1][4], '60')
end)

T.test('a divider drag on the open dock is learned; a resize or a new pane is not a drag', function(wz)
  local rows, a = two_wide(wz, { { id = 77, left = 161, width = 39 } })
  local win, tab = setup(wz, rows)
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77, last = 1 } }
  tick(wz, win) -- the first tick records what it sees
  T.eq(wz.GLOBAL.overview_width, nil)
  tab:set_rows {
    { pane = a, left = 0, width = 99, active = true }, { id = 2, left = 100, width = 55 },
    { id = 77, left = 156, width = 44 },
  }
  tick(wz, win)
  T.eq({ wz.GLOBAL.overview_width, wz.GLOBAL.overview_width_of }, { 44, 200 })
  tab:set_rows {
    { pane = a, left = 0, width = 99, active = true }, { id = 2, left = 100, width = 100 },
    { id = 77, left = 201, width = 50 },
  }
  tick(wz, win) -- the window grew: not a drag
  T.eq(wz.GLOBAL.overview_width, 44)
end)

T.test('the Hivegrid build brings an open dock to its width when the window is resized', function(wz)
  local rows, a = two_wide(wz, { { id = 77, left = 161, width = 36 } })
  local win, tab, calls = setup(wz, rows, { fork = true })
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77, last = 1 } }
  tick(wz, win)
  T.eq(#calls.cli, 0, 'nothing to do at the size it opened with')
  tab:set_rows { -- 320 columns: the dock kept 36 (fixed width), the grid took the rest
    { pane = a, left = 0, width = 141, active = true }, { id = 2, left = 142, width = 141 },
    { id = 77, left = 284, width = 36 },
  }
  tick(wz, win)
  T.eq(calls.cli[#calls.cli], { 'adjust-pane-size', '--pane-id', '77', '--amount', '12', 'Left' })
  tab:set_rows { -- 160 columns
    { pane = a, left = 0, width = 61, active = true }, { id = 2, left = 62, width = 61 },
    { id = 77, left = 124, width = 36 },
  }
  tick(wz, win)
  T.eq(calls.cli[#calls.cli], { 'adjust-pane-size', '--pane-id', '77', '--amount', '6', 'Right' })
end)

T.test('a window too narrow for the dock gets a toast and no split', function(wz)
  local a, b = wz.fake.pane { id = 1 }, wz.fake.pane { id = 2 }
  local win, tab, calls, config = setup(wz, {
    { pane = a, left = 0, width = 30, active = true }, { pane = b, left = 31, width = 30 },
  })
  press(wz, config, win, a)
  T.eq(splits(calls), {})
  T.eq(#win.toasts, 1)
  T.eq(rec(wz, tab), nil)
end)

T.test('a failed split leaves no record behind and says so', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, _, config = setup(wz, rows, { cli = function() return false, '' end })
  press(wz, config, win, a)
  T.eq(rec(wz, tab), nil)
  T.eq(#win.toasts, 1)
end)

T.test('the Hivegrid build splits top-level beside the active pane and re-evens an even grid', function(wz)
  local rows, a = two_wide(wz)
  local win, _, calls, config = setup(wz, rows, { fork = true })
  press(wz, config, win, a)
  local s = splits(calls)[1]
  T.eq({ s[6], s[7] }, { '1', '--top-level' })
  T.eq(calls.equalize, { 1 })
end)

T.test('the Hivegrid build leaves an uneven hand-made grid alone', function(wz)
  local a, b = wz.fake.pane { id = 1 }, wz.fake.pane { id = 2 }
  local win, _, calls, config = setup(wz, {
    { pane = a, left = 0, width = 140, active = true }, { pane = b, left = 141, width = 59 },
  }, { fork = true })
  press(wz, config, win, a)
  T.eq(#splits(calls), 1)
  T.eq(calls.equalize, {})
end)

T.test('from the sidebar bar, LEADER+o acts for the grid pane used last', function(wz)
  local bar = wz.fake.pane { id = 9 }
  local rows, _, b = two_wide(wz)
  table.insert(rows, 1, { pane = bar, left = 0, width = 5, active = true })
  rows[2].active, rows[2].left, rows[3].left = false, 6, 106
  rows[3].width = 94
  local win, tab, calls, config = setup(wz, rows)
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 9, last = 2 } }
  press(wz, config, win, bar)
  T.eq(#splits(calls), 1)
  T.eq(wz.stub.activations, { b:pane_id() }, 'keyboard goes to the grid, not back to the bar')
end)

-- Closing -----------------------------------------------------------------------------------

T.test('LEADER+o again closes the dock and keeps it closed', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, calls, config = setup(wz, rows)
  press(wz, config, win, a)
  tab:set_rows {
    { pane = a, left = 0, width = 99, active = true }, { id = 2, left = 100, width = 60 },
    { id = 77, left = 161, width = 39 },
  }
  press(wz, config, win, a)
  T.eq(calls.cli[#calls.cli], { 'kill-pane', '--pane-id', '77' })
  T.eq(wz.GLOBAL.overview_width, nil, 'closing is not a drag: the next one opens at the default')
  T.eq(rec(wz, tab), nil)
  T.eq(T.read(state_file(77)), nil, 'state file removed')
  tab:set_rows(two_wide(wz))
  tick(wz, win, 6)
  T.clock(T0 + 60)
  tick(wz, win, 6)
  T.eq(#splits(calls), 1, 'a dock the user closed does not come back by itself')
  press(wz, config, win, a)
  T.eq(#splits(calls), 2, 'LEADER+o opens it again')
end)

T.test('the keyboard goes back to the last grid pane when the dock itself toggles', function(wz)
  local rows, _, b = two_wide(wz, { { id = 77, left = 161, width = 39 } })
  local win, tab, calls, config = setup(wz, rows)
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77, last = 1 } }
  b:activate()
  tick(wz, win) -- b active: it becomes the pane used last
  T.eq(rec(wz, tab).last, 2)
  local dock = wz.mux.get_pane(77)
  dock:activate()
  tick(wz, win) -- the dock has focus: last stays b
  T.eq(rec(wz, tab).last, 2)
  local before = #wz.stub.activations
  press(wz, config, win, dock)
  T.eq(calls.cli[#calls.cli], { 'kill-pane', '--pane-id', '77' })
  T.eq(wz.stub.activations[before + 1], 2)
end)

T.test('a dock closed by hand is forgotten on the next tick and stays closed', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, calls, config = setup(wz, rows)
  press(wz, config, win, a)
  T.ok(T.read(state_file(77)), 'rows written')
  tick(wz, win) -- pane 77 is not in the tab: it was closed with q
  T.eq(rec(wz, tab), nil)
  T.eq(T.read(state_file(77)), nil)
  tick(wz, win, 5)
  T.eq(#splits(calls), 1)
end)

T.test('a tab whose grid panes are all gone closes its dock', function(wz)
  local dock = wz.fake.pane { id = 77 }
  local win, tab, calls = setup(wz, { { pane = dock, width = 40, active = true } })
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77, last = 1 } }
  tick(wz, win)
  T.eq(calls.cli, { { 'kill-pane', '--pane-id', '77' } })
  T.eq(rec(wz, tab), nil)
end)

-- Auto-open ---------------------------------------------------------------------------------

T.test('a tab with two grid panes gets the dock once its layout held for three ticks', function(wz)
  local rows = two_wide(wz)
  local win, tab, calls = setup(wz, rows)
  tick(wz, win, 2)
  T.eq(#splits(calls), 0, 'omp-grid may still be splitting')
  tick(wz, win)
  T.eq(#splits(calls), 1)
  T.eq(rec(wz, tab).pane, 77)
  T.eq(#win.toasts, 0, 'auto-open is quiet')
end)

T.test('a changing pane count restarts the wait; a single pane never gets a dock', function(wz)
  local rows = two_wide(wz)
  local win, tab, calls = setup(wz, { rows[1] })
  tick(wz, win, 5)
  T.eq(#splits(calls), 0, 'one pane')
  tab:set_rows(rows)
  tick(wz, win, 2)
  tab:set_rows { rows[1], rows[2], { id = 3, left = 0, top = 41 } }
  tick(wz, win, 2)
  T.eq(#splits(calls), 0, 'count changed after two ticks')
  tick(wz, win)
  T.eq(#splits(calls), 1)
end)

T.test('a failing auto-open is retried every 10 s, not on every tick', function(wz)
  local rows = two_wide(wz)
  local win, _, calls = setup(wz, rows, { cli = function() return false, '' end })
  tick(wz, win, 3)
  T.eq(#splits(calls), 1)
  for s = 1, 9 do
    T.clock(T0 + s)
    tick(wz, win)
  end
  T.eq(#splits(calls), 1)
  T.clock(T0 + 10)
  tick(wz, win)
  T.eq(#splits(calls), 2)
  T.eq(#win.toasts, 0)
end)

-- Bar flag ----------------------------------------------------------------------------------

T.test('the bar button flag file exists exactly while the dock is open', function(wz)
  local rows, a = two_wide(wz)
  local win, tab, _, config = setup(wz, rows)
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5 } }
  local flag = os.getenv('XDG_RUNTIME_DIR') .. '/wezterm-sidebar-5.ov'
  press(wz, config, win, a)
  T.eq(T.read(flag), '1')
  tab:set_rows { rows[1], rows[2], { id = 77, left = 161, width = 39 } }
  press(wz, config, win, a)
  T.eq(T.read(flag), nil)
end)

-- Requests from the dock ----------------------------------------------------------------------

T.test('a row click from the dock focuses that grid pane of the same tab only', function(wz)
  local rows, a = two_wide(wz, { { id = 77, left = 161, width = 39 } })
  local other = wz.fake.tab { panes = { { id = 30 } } }
  local win, tab = setup(wz, rows)
  win.tabs[2] = other
  other._window = win
  local key = tostring(tab:tab_id())
  wz.GLOBAL.overview = { [key] = { pane = 77, last = 1 } }
  wz.GLOBAL.sidebar = { [key] = { handle = 5 } }
  local dock = wz.mux.get_pane(77)
  local function go_to(from, value) wz.emit('user-var-changed', win, from, 'hivegrid_overview_goto', value) end
  go_to(dock, '2:1761500000123')
  T.eq(wz.stub.activations, { 2 })
  go_to(a, '1:x') -- not from the dock
  go_to(dock, '30') -- another tab
  go_to(dock, '5') -- the sidebar bar
  go_to(dock, '77') -- the dock itself
  go_to(dock, 'garbage')
  go_to(dock, '999') -- gone
  T.eq(wz.stub.activations, { 2 })
end)

T.test('the dock grid buttons equalize or arrange; the same request from elsewhere is ignored', function(wz)
  local rows, a = two_wide(wz, { { id = 77, left = 161, width = 39 } })
  local win, tab, calls = setup(wz, rows)
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77, last = 1 } }
  local dock = wz.mux.get_pane(77)
  wz.emit('user-var-changed', win, dock, 'hivegrid_overview_equalize', '1')
  wz.emit('user-var-changed', win, dock, 'hivegrid_overview_arrange', '1')
  wz.emit('user-var-changed', win, a, 'hivegrid_overview_equalize', '1')
  wz.emit('user-var-changed', win, a, 'hivegrid_overview_arrange', '1')
  T.eq({ calls.equalize, calls.arrange }, { { 77 }, { 77 } })
end)

-- Closing from the dock ------------------------------------------------------------------------

-- Bar 5, grid panes 1 2 3 side by side, dock 77 (pane indexes 0..4); the dock used pane `last`.
local function close_setup(wz, last)
  local bar = wz.fake.pane { id = 5 }
  local win, tab = setup(wz, {
    { pane = bar, left = 0, width = 5 }, { id = 1, left = 6, width = 50 }, { id = 2, left = 57, width = 50 },
    { id = 3, left = 108, width = 50 }, { id = 77, left = 159, width = 41, active = true },
  })
  local key = tostring(tab:tab_id())
  wz.GLOBAL.overview = { [key] = { pane = 77, last = last } }
  wz.GLOBAL.sidebar = { [key] = { handle = 5 } }
  local dock = wz.mux.get_pane(77)
  return win, tab, dock, function(value, from)
    wz.emit('user-var-changed', win, from or dock, 'hivegrid_overview_close', value)
  end
end

local act = { close = { kind = 'CloseCurrentPane', args = { confirm = false } } }
local function close_then(index)
  return { kind = 'Multiple', args = { act.close, { kind = 'ActivatePaneByIndex', args = index } } }
end

T.test('x in the dock closes the pane used last; it hands over to its neighbour, keys stay in the dock', function(wz)
  local win, tab, _, close = close_setup(wz, 2)
  close('pane:2:dock:7')
  T.eq(wz.stub.activations, { 2 }, 'the target is made the active pane, then closed')
  T.eq(#win.actions, 1)
  T.eq(win.actions[1].action, close_then(3), 'the dock (index 4) is index 3 after the close')
  T.eq(win.actions[1].pane:pane_id(), 2)
  T.eq(rec(wz, tab).last, 3, 'the next pane in reading order is the one used last now')
end)

T.test('a click on a row ✕ closes another pane and gives the keyboard to the pane used last', function(wz)
  local win, tab, _, close = close_setup(wz, 1)
  close('pane:3:grid:8')
  T.eq(win.actions[1].action, close_then(1))
  T.eq(rec(wz, tab).last, 1)
end)

T.test('closing the last pane in reading order hands over to the one before it', function(wz)
  local win, tab, _, close = close_setup(wz, 3)
  close('pane:3:grid:9')
  T.eq(rec(wz, tab).last, 2)
  T.eq(win.actions[1].action, close_then(2))
end)

T.test('the bar, the dock, another tab and requests from elsewhere are never closed', function(wz)
  local win, _, _, close = close_setup(wz, 1)
  local other = wz.fake.tab { panes = { { id = 30 } } }
  win.tabs[2] = other
  other._window = win
  close('pane:5:dock:1') -- the sidebar bar
  close('pane:77:dock:2') -- the dock itself
  close('pane:30:dock:3') -- another tab
  close('pane:999:dock:4') -- gone
  close('garbage')
  close('pane:1:dock:5', wz.mux.get_pane(1)) -- not from the dock
  close('tab:6', wz.mux.get_pane(2))
  T.eq(win.actions, {})
  T.eq(wz.stub.activations, {})
end)

T.test('closing the only grid pane closes the tab with its bar and dock', function(wz)
  local bar = wz.fake.pane { id = 5 }
  local win, tab = setup(wz, {
    { pane = bar, left = 0, width = 5 }, { id = 1, left = 6, width = 150 }, { id = 77, left = 157, width = 43, active = true },
  })
  local key = tostring(tab:tab_id())
  wz.GLOBAL.overview = { [key] = { pane = 77, last = 1 } }
  wz.GLOBAL.sidebar = { [key] = { handle = 5 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(77), 'hivegrid_overview_close', 'pane:1:dock:1')
  T.eq(win.actions[1].action, { kind = 'CloseCurrentTab', args = { confirm = false } })
  T.eq(rec(wz, tab), nil, 'the dock record goes with the tab')
end)

T.test('the title row ✕ closes the whole tab', function(wz)
  local win, tab, _, close = close_setup(wz, 1)
  close('tab:4')
  T.eq(#win.actions, 1)
  T.eq(win.actions[1].action, { kind = 'CloseCurrentTab', args = { confirm = false } })
  T.eq(rec(wz, tab), nil)
end)

T.test('a close request for a tab that is not the active one is ignored', function(wz)
  local win, tab, _, close = close_setup(wz, 1)
  local other = wz.fake.tab { panes = { { id = 30 } } }
  win.tabs[2] = other
  other._window = win
  win.active = 2
  close('tab:5')
  close('pane:2:dock:6')
  T.eq(win.actions, {})
  T.ok(rec(wz, tab), 'dock kept')
end)
