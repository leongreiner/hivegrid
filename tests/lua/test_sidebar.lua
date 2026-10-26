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
  local rs = { r(5, 0, 0, 9), r(6, 10, 0, 50), r(1, 61, 0, 100) }
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, 30), { id = 5, delta = -4 })
  rs[1].width = 5
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, 30), { id = 6, delta = -20 })
  rs[2].width = 30
  T.eq(i.next_step(rs, { handle = 5, content = 6 }, 30), nil)
  T.eq(i.next_step(rs, {}, 30), nil, 'no bar')
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
