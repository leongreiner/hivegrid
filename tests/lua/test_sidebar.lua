-- sidebar: is_sidebar / is_dock over the per-tab registries in wezterm.GLOBAL, the pure
-- layout helpers, and sync's bar upkeep (creation retry, focus hand-back, closing a tab
-- that has only chrome left).
local T = require 'tests.lua.t'

local T0 = 1760000000

-- Registries ----------------------------------------------------------------------------------

T.test('is_sidebar: the bar and the view of any tab, never the grid pane recorded as last', function(wz)
  local sb = require('sidebar')
  T.eq(sb.is_sidebar(5), false, 'no registry yet')
  wz.GLOBAL.sidebar = {
    ['1'] = { handle = 5, content = 6, last = 7, view = 'files' },
    ['2'] = { handle = 15, last = 17 },
  }
  wz.GLOBAL.overview = { ['1'] = { pane = 77, last = 7 } }
  T.eq({ sb.is_sidebar(5), sb.is_sidebar(6), sb.is_sidebar(15) }, { true, true, true })
  T.eq({ sb.is_sidebar(7), sb.is_sidebar(17), sb.is_sidebar(77), sb.is_sidebar(1) }, { false, false, false, false })
end)

T.test('is_dock: sidebar panes and the overview dock of any tab; grid panes are not docks', function(wz)
  local sb = require('sidebar')
  T.eq(sb.is_dock(77), false, 'no registry yet')
  wz.GLOBAL.sidebar = { ['1'] = { handle = 5, content = 6, last = 7 } }
  wz.GLOBAL.overview = { ['1'] = { pane = 77, last = 7 }, ['2'] = { pane = 88, last = 20 } }
  T.eq({ sb.is_dock(5), sb.is_dock(6), sb.is_dock(77), sb.is_dock(88) }, { true, true, true, true })
  T.eq({ sb.is_dock(7), sb.is_dock(20), sb.is_dock(1) }, { false, false, false })
end)

T.test('the registries are read live: a closed dock stops counting at once', function(wz)
  local sb = require('sidebar')
  wz.GLOBAL.overview = { ['1'] = { pane = 77 } }
  T.eq(sb.is_dock(77), true)
  wz.GLOBAL.overview = {}
  T.eq(sb.is_dock(77), false)
end)

T.test('the built api exposes the same lookups', function(wz)
  local api = require('sidebar')({}, wz)
  wz.GLOBAL.sidebar = { ['1'] = { handle = 5 } }
  wz.GLOBAL.overview = { ['1'] = { pane = 77 } }
  T.eq({ api.is_sidebar(5), api.is_sidebar(77), api.is_dock(77), api.is_dock(1) }, { true, false, true, false })
end)

-- Pure layout helpers ---------------------------------------------------------------------------

local function r(id, left, top, width, height)
  return { id = id, left = left, top = top or 0, width = width, height = height or 40 }
end

T.test('reconcile drops dead panes and a `last` that points at the bar or the view', function(wz)
  local i = require('sidebar')({}, wz).internals
  local rs = { r(5, 0, 0, 5), r(6, 6, 0, 30), r(1, 37, 0, 100) }
  T.eq(i.reconcile({ view = 'git', handle = 5, content = 9, last = 1, dir = '/w' }, rs),
    { view = 'git', handle = 5, last = 1, dir = '/w' })
  T.eq(i.reconcile({ handle = 5, content = 6, last = 6 }, rs), { handle = 5, content = 6 })
  T.eq(i.reconcile({ handle = 4, last = 3 }, rs), {})
end)

T.test('width_for: 22% of the window or the saved width, never eating the anchor below 20 columns', function(wz)
  local i = require('sidebar')({}, wz).internals
  local one = { r(1, 0, 0, 200) }
  T.eq(i.width_for(one, one[1], true, nil), 44)
  T.eq(i.width_for(one, one[1], true, 60), 60)
  T.eq(i.width_for(one, one[1], true, 300), 200 - 6 - 1 - 20, 'capped by the room left')
  T.eq(i.width_for(one, one[1], false, 300), 200 - 1 - 20, 'no bar to make room for')
  local narrow = { r(1, 0, 0, 34) }
  T.eq(i.width_for(narrow, narrow[1], true, nil), nil, 'fewer than 8 columns to spare')
  T.eq(i.width_for({ r(1, 0, 0, 100) }, r(1, 0, 0, 100), false, nil), 28, 'never under 28 when room allows')
end)

T.test('default_width: 22% of the window, 28 to 60 columns', function(wz)
  local i = require('sidebar')({}, wz).internals
  T.eq({ i.default_width(100), i.default_width(159), i.default_width(239), i.default_width(316) }, { 28, 34, 52, 60 })
end)

T.test('column_width: the grid keeps 20 columns per pane side by side', function(wz)
  local i = require('sidebar')({}, wz).internals
  local function skip(id) return id == 5 end
  -- Bar, then a 3x2 grid of 60-col panes (3 side by side) over 200 columns.
  local rs = { r(5, 0, 0, 5) }
  for k, left in ipairs { 6, 67, 128 } do
    rs[#rs + 1] = r(10 + k, left, 0, 60, 20)
    rs[#rs + 1] = r(20 + k, left, 21, 60, 19)
  end
  T.eq(i.column_width(rs, skip, nil), 41, 'default for 188 columns')
  T.eq(i.column_width(rs, skip, 300), 182 - 1 - 60, 'a dragged width capped by the grid')
  T.eq(i.column_width(rs, skip, 300, 41), 41 + 182 - 1 - 60, 'an open view counts its own columns')
  local small = { r(5, 0, 0, 5), r(1, 6, 0, 28) }
  T.eq(i.column_width(small, skip, nil), nil, 'fewer than 8 columns to spare')
  T.eq(i.column_width({ r(5, 0, 0, 5) }, skip, nil), nil, 'no grid')
end)

T.test('content_target keeps 20 columns for the pane beside the view, at least 8 for the view', function(wz)
  local i = require('sidebar')({}, wz).internals
  local c, a = r(6, 6, 0, 30), r(1, 37, 0, 100)
  T.eq(i.content_target({ c, a }, c, 40), 40)
  T.eq(i.content_target({ c, a }, c, 500), 30 + 100 - 20)
  T.eq(i.content_target({ c, a }, c, nil), nil, 'no saved width: leave it')
  local c2, a2 = r(6, 6, 0, 5), r(1, 12, 0, 10)
  T.eq(i.content_target({ c2, a2 }, c2, 40), 8)
  T.eq(i.content_target({ c }, c, 40), nil, 'nothing beside it')
end)

T.test('next_step fixes the bar width before the view width', function(wz)
  local i = require('sidebar')({}, wz).internals
  local function want(w) return function() return w end end
  local rs = { r(5, 0, 0, 9), r(6, 10, 0, 50), r(1, 61, 0, 100) }
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, want(30)), { id = 5, delta = -4 })
  rs[1].width = 5
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, want(30)), { id = 6, delta = -20 })
  rs[2].width = 30
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, want(30)), nil)
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, want(nil)), nil, 'no target: leave the view')
  T.eq(i.next_step(rs, {}, want(30)), nil, 'no bar')
end)

T.test('the view anchors beside the bar, not on a taller pane under it', function(wz)
  local i = require('sidebar')({}, wz).internals
  -- Bar at the top-left only (stock build), a wide pane under it and the grid beside it.
  local rs = { r(5, 0, 0, 5, 20), r(2, 0, 21, 50, 19), r(1, 6, 0, 100, 20) }
  local function skip(id) return id == 5 end
  T.eq(i.anchor_for(rs, { handle = 5 }, skip).id, 1)
end)

-- sync -----------------------------------------------------------------------------------------

local function cli_calls(wz, sub)
  local out = {}
  for _, argv in ipairs(wz.stub.children) do
    if argv[2] == 'cli' and argv[3] == sub then out[#out + 1] = argv end
  end
  return out
end

T.test('a tab without a bar gets one; a failed split is retried after 10 s, not every tick', function(wz)
  T.clock(T0)
  local api = require('sidebar')({}, wz)
  local win = wz.fake.grid { { id = 1, width = 120, active = true } }
  api.sync(win)
  local s = cli_calls(wz, 'split-pane')
  T.eq(#s, 1)
  T.eq({ s[1][4], s[1][5], s[1][6], s[1][7], s[1][8] }, { '--left', '--cells', '5', '--pane-id', '1' })
  T.clock(T0 + 5)
  api.sync(win)
  T.eq(#cli_calls(wz, 'split-pane'), 1)
  T.clock(T0 + 10)
  api.sync(win)
  T.eq(#cli_calls(wz, 'split-pane'), 2)
end)

T.test('a new bar is recorded and the keyboard stays in the grid', function(wz)
  T.clock(T0)
  wz.stub.on_child = function(argv)
    if argv[3] == 'split-pane' then return true, '5\n' end
    return true, ''
  end
  local api = require('sidebar')({}, wz)
  local win, tab = wz.fake.grid { { id = 1, width = 120, active = true } }
  api.sync(win)
  T.eq(wz.GLOBAL.sidebar[tostring(tab:tab_id())].handle, 5)
  local act = cli_calls(wz, 'activate-pane')
  T.eq(act[1] and act[1][5], '1')
  T.eq(T.read(os.getenv('XDG_RUNTIME_DIR') .. '/wezterm-sidebar-5'), 'closed files\n', 'bar state file')
end)

T.test('the bar learns how far to move its bottom button to centre the stack', function(wz)
  T.clock(T0)
  local api = require('sidebar')({}, wz)
  -- Hivegrid build at 1280x720: 24 px above the first row (64 - 40 px of tab bar), 27 below.
  local win, tab = wz.fake.grid({ { id = 5, width = 5 }, { id = 1, left = 6, width = 100, active = true } },
    { dims = { pixel_width = 1280, pixel_height = 720, dpi = 96, tab_bar_height = 40, content_top = 64,
      content_bottom = 693 } })
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5 } }
  api.sync(win)
  T.eq(T.read(os.getenv('XDG_RUNTIME_DIR') .. '/wezterm-sidebar-5'), 'closed files 3\n')
end)

T.test('the bar never keeps the keyboard: focus goes back to the grid pane used last', function(wz)
  T.clock(T0)
  local api = require('sidebar')({}, wz)
  local win, tab = wz.fake.grid {
    { id = 5, width = 5, active = true }, { id = 1, left = 6, width = 60 }, { id = 2, left = 67, width = 60 },
  }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, last = 2 } }
  api.sync(win)
  T.eq(wz.stub.activations, { 2 })
end)

T.test('a tab with only the bar and the dock left closes like its last pane did', function(wz)
  T.clock(T0)
  local api = require('sidebar')({}, wz)
  local win, tab = wz.fake.grid { { id = 5, width = 5, active = true }, { id = 77, left = 6, width = 40 } }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5 } }
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 77 } }
  api.sync(win)
  local a = win.actions[#win.actions]
  T.eq(a and a.action.kind, 'CloseCurrentTab')
  T.eq(a.action.args, { confirm = false })
end)

T.test('a tab with a grid pane beside the bar is kept', function(wz)
  T.clock(T0)
  local api = require('sidebar')({}, wz)
  local win, tab = wz.fake.grid { { id = 5, width = 5 }, { id = 1, left = 6, width = 100, active = true } }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5 } }
  api.sync(win)
  for _, a in ipairs(win.actions) do T.ok(a.action.kind ~= 'CloseCurrentTab', 'closed a live tab') end
  T.eq(wz.GLOBAL.sidebar[tostring(tab:tab_id())].last, 1)
end)

T.test('a bar click from a pane that is not the bar is ignored', function(wz)
  T.clock(T0)
  require('sidebar')({}, wz)
  local win, tab = wz.fake.grid { { id = 5, width = 5 }, { id = 1, left = 6, width = 100, active = true } }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(1), 'sidebar_click', 'files 123')
  T.eq(cli_calls(wz, 'split-pane'), {})
end)

T.test('a bar click opens the explorer beside the grid pane used last', function(wz)
  T.clock(T0)
  wz.stub.on_child = function(argv)
    if argv[3] == 'split-pane' then return true, '6\n' end
    return true, ''
  end
  require('sidebar')({}, wz)
  local win, tab = wz.fake.grid { { id = 5, width = 5, active = true }, { id = 1, left = 6, width = 150 } }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, last = 1 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(5), 'sidebar_click', 'files 123')
  local s = cli_calls(wz, 'split-pane')
  T.eq(#s, 1)
  local argv = table.concat(s[1], ' ')
  T.ok(argv:find('--pane-id 1 ', 1, true), argv)
  T.ok(argv:find('/.local/bin/yazi', 1, true), argv)
  local rec = wz.GLOBAL.sidebar[tostring(tab:tab_id())]
  T.eq({ rec.content, rec.view }, { 6, 'files' })
  T.eq(T.read(os.getenv('XDG_RUNTIME_DIR') .. '/wezterm-sidebar-5'), 'open files\n')
end)

-- Column layout (Hivegrid build) -----------------------------------------------------------------

-- A one-row fake of the Hivegrid build's mux: `panes` = { { id, width }, ... } left to right.
-- adjust-pane-size moves the right edge of the pane (its right neighbour gives or takes the
-- columns), split-pane --right cuts a new pane off the right of one, kill-pane gives a pane's
-- columns to the pane split off it, else to its left neighbour. Returns the live list and a
-- function that applies changes made to it by hand (a divider drag).
local function column_mux(wz, tab, panes, first_new)
  local new = first_new
  local function layout()
    local rows, left = {}, 0
    for _, p in ipairs(panes) do
      rows[#rows + 1] = { id = p.id, left = left, width = p.width, height = 58, active = p.active }
      left = left + p.width + 1
    end
    tab:set_rows(rows)
  end
  local function index(id)
    for k, p in ipairs(panes) do if p.id == id then return k end end
  end
  wz.stub.on_child = function(argv)
    local function arg(name)
      for k = 4, #argv do if argv[k] == name then return argv[k + 1] end end
    end
    local sub, id = argv[3], tonumber(arg('--pane-id') or '')
    local k = id and index(id)
    if sub == 'adjust-pane-size' and k then
      local d = tonumber(arg('--amount')) * (argv[#argv] == 'Right' and 1 or -1)
      panes[k].width, panes[k + 1].width = panes[k].width + d, panes[k + 1].width - d
    elseif sub == 'split-pane' and k then
      local cells = tonumber(arg('--cells'))
      panes[k].width = panes[k].width - cells - 1
      new = new + 1
      table.insert(panes, k + 1, { id = new, width = cells, from = id })
      layout()
      return true, new .. '\n'
    elseif sub == 'kill-pane' and k then
      local heir = panes[k + 1] and panes[k + 1].from == id and panes[k + 1] or panes[k - 1]
      heir.width = heir.width + panes[k].width + 1
      table.remove(panes, k)
    end
    layout()
    return true, ''
  end
  layout()
  return panes, layout
end

local function widths(panes)
  local out = {}
  for _, p in ipairs(panes) do out[#out + 1] = p.id .. ':' .. p.width end
  return table.concat(out, ' ')
end

T.test('column layout: the view is split off the widened bar, the grid gives up its columns once', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  require('sidebar')({}, wz)
  local win, tab = wz.fake.grid {}
  local panes = column_mux(wz, tab, { { id = 5, width = 5 }, { id = 1, width = 194, active = true } }, 100)
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, last = 1 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(5), 'sidebar_click', 'files 1')
  T.eq(widths(panes), '5:5 101:44 1:149', '22% of 200 columns')
  local adj = cli_calls(wz, 'adjust-pane-size')
  T.eq(#adj, 1, 'one resize of the grid')
  T.eq({ adj[1][5], adj[1][7], adj[1][8] }, { '5', '45', 'Right' })
  local split = cli_calls(wz, 'split-pane')[1]
  T.eq({ split[4], split[5], split[6] }, { '--right', '--cells', '44' })
  T.ok(table.concat(split, ' '):find('--pane-id 5 ', 1, true))
  local rec = wz.GLOBAL.sidebar[tostring(tab:tab_id())]
  T.eq({ rec.content, rec.view, rec.width }, { 101, 'files', nil }, 'no width pinned before a drag')
end)

T.test('column layout: switching the view replaces it in place, the grid does not move', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  require('sidebar')({}, wz)
  local win, tab = wz.fake.grid {}
  local panes = column_mux(wz, tab,
    { { id = 5, width = 5 }, { id = 6, width = 44 }, { id = 1, width = 149, active = true } }, 100)
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, content = 6, view = 'files', last = 1 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(5), 'sidebar_click', 'git 1')
  T.eq(widths(panes), '5:5 101:44 1:149')
  T.eq(#cli_calls(wz, 'adjust-pane-size'), 0)
  local argv = table.concat(cli_calls(wz, 'split-pane')[1], ' ')
  T.ok(argv:find('--right --cells 42 ', 1, true) and argv:find('--pane-id 6 ', 1, true), argv)
  T.ok(argv:find('hivegrid-git', 1, true), argv)
  T.eq(cli_calls(wz, 'kill-pane')[1][5], '6')
  local rec = wz.GLOBAL.sidebar[tostring(tab:tab_id())]
  T.eq({ rec.content, rec.view }, { 101, 'git' })
end)

T.test('column layout: closing the view hands its columns back to the grid', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  require('sidebar')({}, wz)
  local win, tab = wz.fake.grid {}
  local panes = column_mux(wz, tab,
    { { id = 5, width = 5 }, { id = 6, width = 44 }, { id = 1, width = 149, active = true } }, 100)
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, content = 6, view = 'files', last = 1 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(5), 'sidebar_click', 'files 1')
  T.eq(widths(panes), '5:5 1:194')
end)

T.test('column layout: the view follows the window size until a drag sets its width', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  local api = require('sidebar')({}, wz)
  local win, tab = wz.fake.grid {}
  -- 159 columns (a 1280 px window) with the 52-column view a 1920 px window gave.
  local panes, layout = column_mux(wz, tab,
    { { id = 5, width = 5 }, { id = 6, width = 52 }, { id = 1, width = 100, active = true } }, 100)
  local key = tostring(tab:tab_id())
  wz.GLOBAL.sidebar = { [key] = { handle = 5, content = 6, view = 'files', last = 1 } }
  api.sync(win)
  T.eq(widths(panes), '5:5 6:34 1:118')
  -- A drag to 40 (same panes, same total) is kept: for this tab and as the remembered width.
  panes[2].width, panes[3].width = 40, 112
  layout()
  api.sync(win)
  T.eq(widths(panes), '5:5 6:40 1:112')
  T.eq({ wz.GLOBAL.sidebar[key].width, wz.GLOBAL.sidebar_width }, { 40, 40 })
  T.eq({ wz.GLOBAL.sidebar[key].width_of, wz.GLOBAL.sidebar_width_of }, { 159, 159 })
  -- The window grows to 239 columns: the dragged width grows with it (40 * 239 / 159 = 60).
  panes[3].width = 192
  layout()
  api.sync(win)
  T.eq(widths(panes), '5:5 6:60 1:172')
end)

T.test('the git view does not follow the grid while lazygit runs in it', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  local api = require('sidebar')({}, wz)
  local a, b = T.tmpdir(), T.tmpdir()
  local lg = { name = 'python3', children = { [42] = { name = 'lazygit', children = {} } } }
  wz.fake.pane { id = 6, process_info = lg }
  wz.fake.pane { id = 1, cwd = b }
  local win, tab = wz.fake.grid {}
  column_mux(wz, tab, { { id = 5, width = 5 }, { id = 6, width = 44 }, { id = 1, width = 149, active = true } }, 100)
  local key = tostring(tab:tab_id())
  wz.GLOBAL.sidebar = { [key] = { handle = 5, content = 6, view = 'git', last = 1, dir = a } }
  api.sync(win)
  T.eq(cli_calls(wz, 'split-pane'), {}, 'lazygit open: no restart')
  T.eq(wz.GLOBAL.sidebar[key].dir, a, 'the move is still pending')
  lg.children = {}
  T.clock(T0 + 5)
  api.sync(win)
  T.eq(#cli_calls(wz, 'split-pane'), 1, 'lazygit quit: the list restarts for the new directory')
  T.eq(wz.GLOBAL.sidebar[key].dir, b)
end)

T.test('Enter on a text file in the explorer opens an editor beside the grid pane used last', function(wz)
  T.clock(T0)
  wz.stub.on_child = function(argv)
    if argv[3] == 'split-pane' then return true, '9\n' end
    return true, ''
  end
  require('sidebar')({}, wz)
  local dir = T.tmpdir()
  T.write(dir .. '/notes.md', 'hi\n')
  local win, tab = wz.fake.grid {
    { id = 5, width = 5 }, { id = 6, left = 6, width = 44, active = true }, { id = 1, left = 51, width = 60 },
  }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 5, content = 6, view = 'files', last = 1 } }
  wz.emit('user-var-changed', win, wz.mux.get_pane(6), 'hivegrid_open_file', dir .. '/notes.md')
  local s = cli_calls(wz, 'split-pane')
  T.eq(#s, 1)
  local argv = table.concat(s[1], ' ')
  T.ok(argv:find('--bottom --cwd ' .. dir .. ' --pane-id 1 --', 1, true), 'a 60-column pane splits below: ' .. argv)
  T.eq(s[1][#s[1]], dir .. '/notes.md')
  T.ok(argv:find('EDITOR', 1, true), argv)
  T.eq(cli_calls(wz, 'activate-pane')[1][5], '9', 'the editor gets the keyboard')
  -- A folder, a missing file or a value from another pane opens nothing.
  wz.emit('user-var-changed', win, wz.mux.get_pane(6), 'hivegrid_open_file', dir)
  wz.emit('user-var-changed', win, wz.mux.get_pane(6), 'hivegrid_open_file', dir .. '/missing')
  wz.emit('user-var-changed', win, wz.mux.get_pane(1), 'hivegrid_open_file', dir .. '/notes.md')
  T.eq(#cli_calls(wz, 'split-pane'), 1)
end)

T.test('inline_view: the view counts into the grid only on stock WezTerm', function(wz)
  wz.GLOBAL.sidebar = { ['3'] = { handle = 5, content = 6 } }
  T.eq(require('sidebar').inline_view(3), 6)
  T.fresh()
end)

T.test('inline_view: none on the Hivegrid build, where the view is a column of its own', function(wz)
  wz.stub.fork = true
  wz.GLOBAL.sidebar = { ['3'] = { handle = 5, content = 6 } }
  T.eq(require('sidebar').inline_view(3), nil)
end)
