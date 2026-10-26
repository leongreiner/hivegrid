-- config/layouts.lua through its public surface: api.arrange / api.equalize, the
-- 'update-status' auto-arrange handler and the key bindings / key tables it installs.
-- `wezterm cli` is answered by tests/lua/fakemux.lua, a model of WezTerm's split tree.
local T = require 'tests.lua.t'
local FM = require 'tests.lua.fakemux'

-- layouts runs its work under pcall and logs errors; a test fails on any logged error.
local function test(name, fn)
  T.test(name, function(wz)
    fn(wz)
    local errors = {}
    for _, l in ipairs(wz.stub.logs) do
      if l.level == 'error' then errors[#errors + 1] = l.message end
    end
    T.eq(errors, {}, 'errors logged by layouts')
  end)
end

local function setup(wz, opts)
  local mux = FM.new(wz, opts)
  local config = {}
  local api = require('layouts')(config, wz)
  return mux, api, config
end

local function dock(wz, tab_id, pane_id)
  wz.GLOBAL.sidebar = { [tostring(tab_id)] = { handle = pane_id } }
end

-- Grid panes of the first tab in reading order, and the pane count of each row.
local function reading(mux, skip)
  local list = {}
  for _, p in ipairs(mux:panes(mux.tabs[1])) do
    if not (skip and skip[p.id]) then list[#list + 1] = p end
  end
  local num = require('panemap').numbering(list)
  local ordered = {}
  for i, p in ipairs(list) do ordered[num[i]] = p end
  local ids, counts, last_top = {}, {}, nil
  for i, p in ipairs(ordered) do
    ids[i] = p.id
    if last_top and math.abs(p.top - last_top) <= 2 then
      counts[#counts] = counts[#counts] + 1
    else
      counts[#counts + 1] = 1
      last_top = p.top
    end
  end
  return ids, counts, ordered
end

local function spread(values)
  local lo, hi = math.huge, -math.huge
  for _, v in ipairs(values) do lo, hi = math.min(lo, v), math.max(hi, v) end
  return hi - lo
end

local function field(list, k)
  local out = {}
  for i, p in ipairs(list) do out[i] = p[k] end
  return out
end

-- The command log with split sizes left out (sizes are checked through the geometry).
local function plan(mux, filter)
  local out = {}
  for _, c in ipairs(mux:commands(filter)) do
    out[#out + 1] = (c:gsub(' %-%-cells %d+', ''):gsub(' %-%-percent %d+', ''))
  end
  return out
end

local function count(mux, name) return #mux:commands(name) end

-- Arrange ---------------------------------------------------------------------------

test('arrange: 5 panes in a row of a wide window become rows of 3 + 2, order kept', function(wz)
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3, 4, 5 }, 250) })
  api.arrange(mux.window, mux.window:active_pane())
  T.eq(plan(mux), {
    'move-pane-to-new-tab --pane-id 2',
    'move-pane-to-new-tab --pane-id 3',
    'move-pane-to-new-tab --pane-id 4',
    'move-pane-to-new-tab --pane-id 5',
    'split-pane --pane-id 1 --bottom --move-pane-id 4',
    'split-pane --pane-id 1 --right --move-pane-id 2',
    'split-pane --pane-id 2 --right --move-pane-id 3',
    'split-pane --pane-id 4 --right --move-pane-id 5',
    'activate-pane --pane-id 1',
    'activate-tab --tab-id 1',
  })
  local ids, counts, ordered = reading(mux)
  T.eq(counts, { 3, 2 }, 'the top row takes the extra pane')
  T.eq(ids, { 1, 2, 3, 4, 5 }, 'reading order (agent numbers) unchanged')
  T.eq(#mux.tabs, 1, 'no pane left behind in a temporary tab')
  local top = { ordered[1], ordered[2], ordered[3] }
  T.ok(spread(field(top, 'width')) <= 1, 'top row widths equal: ' .. T.show(field(top, 'width')))
  T.ok(spread(field({ ordered[4], ordered[5] }, 'width')) <= 1, 'bottom row widths equal')
  T.ok(spread(field(ordered, 'height')) <= 1, 'row heights equal: ' .. T.show(field(ordered, 'height')))
  T.eq(#mux.window.toasts, 0)
end)

test('arrange: 4 panes in a tall window become one column with equal heights', function(wz)
  local mux, api = setup(wz, { cols = 100, rows = 120, root = FM.strip('h', { 1, 2, 3, 4 }, 100) })
  api.arrange(mux.window, mux.window:active_pane())
  T.eq(plan(mux, 'split-pane'), {
    'split-pane --pane-id 1 --bottom --move-pane-id 2',
    'split-pane --pane-id 2 --bottom --move-pane-id 3',
    'split-pane --pane-id 3 --bottom --move-pane-id 4',
  })
  local ids, counts, ordered = reading(mux)
  T.eq(counts, { 1, 1, 1, 1 })
  T.eq(ids, { 1, 2, 3, 4 })
  T.ok(spread(field(ordered, 'height')) <= 1, 'equal heights: ' .. T.show(field(ordered, 'height')))
end)

test('arrange keeps the reading order of panes whose ids are not in order', function(wz)
  -- 2x2 grid read as 30, 10 / 40, 20; arranged for 4 panes it stays 2 + 2 in that order.
  local root = FM.v(29, FM.h(124, FM.leaf(30), FM.leaf(10)), FM.h(124, FM.leaf(40), FM.leaf(20)))
  local mux, api = setup(wz, { cols = 250, rows = 60, root = root })
  api.arrange(mux.window, mux.window:active_pane())
  local ids, counts = reading(mux)
  T.eq(ids, { 30, 10, 40, 20 })
  T.eq(counts, { 2, 2 })
end)

test('arrange: a failed split puts every pane back beside the first, with a toast', function(wz)
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3, 4, 5 }, 250) })
  local function planned(args) return args[1] == 'split-pane' and (args[5] == '--cells' or args[5] == '--percent') end
  mux.fail = function(args) return planned(args) and args[#args] == '2' end
  api.arrange(mux.window, mux.window:active_pane())
  T.eq(#mux.tabs, 1, 'all panes back in the tab')
  local ids = reading(mux)
  table.sort(ids)
  T.eq(ids, { 1, 2, 3, 4, 5 })
  -- After the failure no further planned split is tried; the leftovers go back by plain splits.
  local after, seen_fail = {}, false
  for _, args in ipairs(mux.log) do
    if seen_fail and args[1] == 'split-pane' then after[#after + 1] = table.concat(args, ' ') end
    if planned(args) and args[#args] == '2' then seen_fail = true end
  end
  T.eq(after, {
    'split-pane --pane-id 1 --right --move-pane-id 2',
    'split-pane --pane-id 1 --right --move-pane-id 3',
    'split-pane --pane-id 1 --right --move-pane-id 5',
  })
  T.eq(#mux.window.toasts, 1, 'the user is told')
end)

test('arrange does nothing with fewer than two grid panes (dock panes do not count)', function(wz)
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.h(30, FM.leaf(99), FM.leaf(1)) })
  dock(wz, mux.tab:tab_id(), 99)
  api.arrange(mux.window, mux.window:active_pane())
  T.eq(mux:commands(), {})
end)

test('arrange unzooms a zoomed pane first and never moves the sidebar', function(wz)
  local root = FM.h(30, FM.leaf(99), FM.strip('h', { 1, 2, 3 }, 219))
  local mux, api = setup(wz, { cols = 250, rows = 60, root = root })
  dock(wz, mux.tab:tab_id(), 99)
  mux.tabs[1].zoomed = 2
  mux:sync()
  api.arrange(mux.window, wz.mux.get_pane(2))
  T.eq(mux:commands()[1], 'zoom-pane --pane-id 2 --unzoom')
  for _, c in ipairs(mux:commands()) do
    T.ok(not c:find('pane%-id 99'), 'sidebar touched: ' .. c)
  end
  T.eq(mux:geometry(99).width, 30)
  local ids = reading(mux, { [99] = true })
  T.eq(ids, { 1, 2, 3 })
end)

-- Rotate / swap (pane mode) -------------------------------------------------------------

test('pane mode r rotates panes one slot clockwise, geometry and sidebar kept', function(wz)
  local root = FM.h(30, FM.leaf(99), FM.h(124, FM.leaf(1), FM.v(29, FM.leaf(2), FM.leaf(3))))
  local mux, _, config = setup(wz, { cols = 280, rows = 60, root = root })
  dock(wz, mux.tab:tab_id(), 99)
  local _, _, before = reading(mux, { [99] = true })
  wz.stub.perform(wz.stub.key(config.key_tables.pane, 'r'), mux.window, wz.mux.get_pane(1))
  local ids, _, after = reading(mux, { [99] = true })
  T.eq(ids, { 3, 1, 2 }, 'each pane moved to the next slot, the last to the first')
  T.eq(mux:geometry(99).width, 30, 'sidebar untouched')
  for i = 1, 3 do
    T.ok(math.abs(after[i].width - before[i].width) <= 1 and math.abs(after[i].height - before[i].height) <= 1,
      string.format('slot %d kept its size: %s -> %s', i, T.show(before[i]), T.show(after[i])))
  end
  T.eq(#mux.tabs, 1)
end)

test('pane mode s swaps with the chosen pane, keyboard stays on the moved pane', function(wz)
  local mux, _, config = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3 }, 250) })
  local win = mux.window
  wz.stub.perform(wz.stub.key(config.key_tables.pane, 's'), win, wz.mux.get_pane(1))
  local pick = wz.stub.picker(win)
  T.eq(#pick.spec.rows, 3)
  T.eq(pick.spec.rows[3].key, '3', 'the digit is the reading-order number')
  pick.answer('3')
  T.eq((reading(mux)), { 3, 2, 1 })
  T.eq(mux.tabs[1].active, 1)
end)

-- Auto arrange ------------------------------------------------------------------------

local function tick(wz, mux, n)
  for _ = 1, n or 1 do wz.stub.call('update-status', mux.window, mux.window:active_pane()) end
end

test('auto-arrange: a tidy strip that gains a pane is re-tiled after 3 steady ticks', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  mux:split(2, 'h', 50)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 2)
  T.eq(mux:commands(), {}, 'count must hold before acting')
  tick(wz, mux)
  T.eq(count(mux, 'move-pane-to-new-tab'), 2, 'arranged on the third tick')
  local _, counts, ordered = reading(mux)
  T.eq(counts, { 3 })
  T.ok(spread(field(ordered, 'width')) <= 1, 'widths evened: ' .. T.show(field(ordered, 'width')))
  mux.log = {}
  tick(wz, mux, 5)
  T.eq(mux:commands(), {}, 'settled: no further arranging')
end)

test('auto-arrange: a count that flips back restarts the wait', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  local _, new = mux:split(2, 'h', 50)
  mux:sync()
  tick(wz, mux, 2)
  mux:kill(new)
  tick(wz, mux)
  mux:split(2, 'h', 50)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 2)
  T.eq(count(mux, 'move-pane-to-new-tab'), 0, 'two ticks after the flip are not enough')
  tick(wz, mux)
  T.eq(count(mux, 'move-pane-to-new-tab'), 2)
end)

test('auto-arrange: an ideal grid losing a pane is re-tiled', function(wz)
  local root = FM.v(29, FM.strip('h', { 1, 2, 3 }, 250), FM.strip('h', { 4, 5 }, 250))
  local mux = setup(wz, { cols = 250, rows = 60, root = root })
  tick(wz, mux)
  mux:kill(3)
  mux.log = {}
  tick(wz, mux, 3)
  T.ok(count(mux, 'move-pane-to-new-tab') > 0, 'arranged')
  local ids, counts, ordered = reading(mux)
  T.eq(counts, { 2, 2 })
  T.eq(ids, { 1, 2, 4, 5 })
  T.ok(spread(field(ordered, 'width')) <= 1)
end)

test('auto-arrange leaves a jump by two panes (omp-grid) alone', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  mux:split(2, 'h', 50)
  mux:split(1, 'h', 50)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 6)
  T.eq(count(mux, 'move-pane-to-new-tab'), 0)
  T.eq(count(mux, 'split-pane'), 0)
end)

test('auto-arrange leaves a hand-built layout alone', function(wz)
  -- Main pane plus a stacked side column: not aligned rows.
  local root = FM.h(124, FM.leaf(1), FM.v(29, FM.leaf(2), FM.leaf(3)))
  local mux = setup(wz, { cols = 250, rows = 60, root = root })
  tick(wz, mux)
  mux:split(3, 'v', 50)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 6)
  T.eq(count(mux, 'move-pane-to-new-tab'), 0)
end)

test('auto-arrange waits while an overlay (panel) is the active pane', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  mux:split(2, 'h', 50)
  mux:sync()
  mux.log = {}
  mux.window.active_pane_override = wz.fake.pane { id = 777 }
  tick(wz, mux, 5)
  T.eq(mux:commands(), {}, 'nothing while the overlay shows')
end)

test('auto-arrange skips a tab with a zoomed pane', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  mux:split(2, 'h', 50)
  mux.tabs[1].zoomed = 1
  mux:sync()
  mux.log = {}
  tick(wz, mux, 5)
  T.eq(mux:commands(), {})
end)

test('sidebar opening beside an even grid re-evens it; no re-tiling', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  tick(wz, mux)
  -- The sidebar view is a top-level split at the left: the grid loses 31 columns, which
  -- WezTerm takes from the last pane.
  mux.tabs[1].root = FM.h(30, FM.leaf(99), mux.tabs[1].root)
  dock(wz, mux.tab:tab_id(), 99)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 4)
  T.eq(count(mux, 'move-pane-to-new-tab'), 0, 'not re-tiled')
  local _, _, ordered = reading(mux, { [99] = true })
  T.ok(spread(field(ordered, 'width')) <= 1, 'evened: ' .. T.show(field(ordered, 'width')))
  T.eq(mux:geometry(99).width, 30)
end)

test('a hand-sized grid is not evened when the sidebar opens', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.h(60, FM.leaf(1), FM.leaf(2)) })
  tick(wz, mux)
  mux.tabs[1].root = FM.h(30, FM.leaf(99), mux.tabs[1].root)
  dock(wz, mux.tab:tab_id(), 99)
  mux:sync()
  mux.log = {}
  tick(wz, mux, 4)
  T.eq(count(mux, 'adjust-pane-size'), 0)
  T.eq(mux:geometry(1).width, 60)
end)

-- Equalize --------------------------------------------------------------------------

test('equalize: uneven columns beside a sidebar snap to even widths, sidebar untouched', function(wz)
  -- [sidebar 30][A 60][B 90][C 67] in 250 columns: the grid starts at column 31.
  local root = FM.h(30, FM.leaf(99), FM.h(60, FM.leaf(1), FM.h(90, FM.leaf(2), FM.leaf(3))))
  local mux, api = setup(wz, { cols = 250, rows = 60, root = root })
  dock(wz, mux.tab:tab_id(), 99)
  api.equalize(mux.window, wz.mux.get_pane(2))
  T.eq(mux:geometry(99).width, 30, 'sidebar width')
  local _, _, ordered = reading(mux, { [99] = true })
  T.ok(spread(field(ordered, 'width')) <= 1, 'even columns: ' .. T.show(field(ordered, 'width')))
  T.eq(mux.tabs[1].active, 2, 'focus returns to the pane it started in')
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

test('equalize: a bar split off the leftmost pane is probed but never moved', function(wz)
  -- On stock WezTerm the sidebar bar is a leaf split of the leftmost grid pane, so that
  -- pane's nearest column split is the bar's: resizing it would move the bar.
  -- [bar 30][A 79] [B 139] in 250 columns.
  local root = FM.h(110, FM.h(30, FM.leaf(99), FM.leaf(1)), FM.leaf(2))
  local mux, api = setup(wz, { cols = 250, rows = 60, root = root })
  dock(wz, mux.tab:tab_id(), 99)
  api.equalize(mux.window, wz.mux.get_pane(1))
  T.eq(mux:geometry(99).width, 30, 'sidebar width')
  local _, _, ordered = reading(mux, { [99] = true })
  T.ok(spread(field(ordered, 'width')) <= 1, 'even columns: ' .. T.show(field(ordered, 'width')))
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

test('equalize: independently split rows end up with aligned, even dividers', function(wz)
  local root = FM.v(20, FM.h(60, FM.leaf(1), FM.leaf(2)), FM.h(140, FM.leaf(3), FM.leaf(4)))
  local mux, api = setup(wz, { cols = 200, rows = 50, root = root })
  api.equalize(mux.window, wz.mux.get_pane(1))
  local _, counts, ordered = reading(mux)
  T.eq(counts, { 2, 2 })
  T.ok(spread(field(ordered, 'width')) <= 1, 'widths: ' .. T.show(field(ordered, 'width')))
  T.ok(spread(field(ordered, 'height')) <= 1, 'heights: ' .. T.show(field(ordered, 'height')))
  T.eq(#mux.window.toasts, 0)
end)

test('equalize: a row of 2 beside a row of 3 gets halves and thirds', function(wz)
  local root = FM.v(20, FM.h(60, FM.leaf(1), FM.leaf(2)),
    FM.h(50, FM.leaf(3), FM.h(50, FM.leaf(4), FM.leaf(5))))
  local mux, api = setup(wz, { cols = 200, rows = 50, root = root })
  api.equalize(mux.window, wz.mux.get_pane(1))
  local _, counts, ordered = reading(mux)
  T.eq(counts, { 2, 3 })
  local top, bottom = { ordered[1], ordered[2] }, { ordered[3], ordered[4], ordered[5] }
  T.ok(spread(field(top, 'width')) <= 1, 'halves: ' .. T.show(field(top, 'width')))
  T.ok(spread(field(bottom, 'width')) <= 1, 'thirds: ' .. T.show(field(bottom, 'width')))
  T.ok(spread(field(ordered, 'height')) <= 1, 'heights: ' .. T.show(field(ordered, 'height')))
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

-- The explorer view as sidebar.lua docks it: a leaf split left of the leftmost grid pane,
-- recorded as the tab's sidebar content.
local function open_view(wz, mux, pane, cols)
  local view = mux:insert_left(pane, cols)
  wz.GLOBAL.sidebar = { [tostring(mux.tab:tab_id())] = { content = view } }
  return view
end

test('equalize: an arranged grid stays put when the explorer view opens beside it', function(wz)
  -- 83 | 82 | 83: the widths arrange gives three panes in 250 columns.
  local root = FM.h(83, FM.leaf(1), FM.h(82, FM.leaf(2), FM.leaf(3)))
  local mux, api = setup(wz, { cols = 250, rows = 60, root = root })
  tick(wz, mux)
  local view = open_view(wz, mux, 1, 30)
  tick(wz, mux, 4)
  T.eq(mux:commands('adjust-pane-size'), {}, 'no re-even fight when the view opens')
  api.equalize(mux.window, wz.mux.get_pane(2))
  T.eq(mux:commands('adjust-pane-size'), {}, 'view + first pane count as one column')
  T.eq(mux:geometry(view).width, 30)
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

test('opening the view beside an omp-grid grid (1 cell off exact) neither resizes nor toasts', function(wz)
  -- omp-grid's split-pane --percent 66 then 50 gives 84 | 82 | 82 in 250 columns.
  local root = FM.h(84, FM.leaf(1), FM.h(82, FM.leaf(2), FM.leaf(3)))
  local mux = setup(wz, { cols = 250, rows = 60, root = root })
  tick(wz, mux)
  open_view(wz, mux, 1, 30)
  tick(wz, mux, 4)
  T.eq(mux:commands('adjust-pane-size'), {})
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

test('equalize with the explorer view open evens the columns it shares', function(wz)
  -- [view 30][A 49] [B 169]: column 1 is the view plus A.
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.h(80, FM.leaf(1), FM.leaf(2)) })
  local view = open_view(wz, mux, 1, 30)
  api.equalize(mux.window, wz.mux.get_pane(2))
  T.eq(mux:geometry(view).width, 30, 'view width never changes')
  local first = mux:geometry(view).width + 1 + mux:geometry(1).width
  T.ok(math.abs(first - mux:geometry(2).width) <= 1,
    string.format('view+A %d vs B %d', first, mux:geometry(2).width))
  T.eq(#mux.window.toasts, 0, T.show(mux.window.toasts))
end)

test('equalize unzooms first and is a no-op on an already even grid', function(wz)
  local mux, api = setup(wz, { cols = 201, rows = 50, root = FM.strip('h', { 1, 2 }, 201) })
  mux.tabs[1].zoomed = 2
  mux:sync()
  api.equalize(mux.window, wz.mux.get_pane(1))
  T.eq(mux:commands('zoom-pane'), { 'zoom-pane --pane-id 2 --unzoom' })
  T.eq(mux:commands('adjust-pane-size'), {})
  T.eq(#mux.window.toasts, 0)
end)

test('equalize: wezterm cli unreachable -> toast, nothing resized', function(wz)
  local mux, api = setup(wz, { cols = 200, rows = 50, root = FM.strip('h', { 1, 2 }, 200) })
  mux.fail = function(args) return args[1] == 'list' end
  api.equalize(mux.window, wz.mux.get_pane(1))
  T.eq(mux:commands('adjust-pane-size'), {})
  T.eq(#mux.window.toasts, 1)
end)

test('equalize with a single grid pane does nothing', function(wz)
  local mux, api = setup(wz, { cols = 200, rows = 50, root = FM.h(30, FM.leaf(99), FM.leaf(1)) })
  dock(wz, mux.tab:tab_id(), 99)
  api.equalize(mux.window, wz.mux.get_pane(1))
  T.eq(mux:commands(), {})
end)

-- Key bindings ----------------------------------------------------------------------

test('equalize/arrange keys follow settings, defaults otherwise', function(wz)
  local cfg = T.tmpdir() .. '/settings.json'
  T.write(cfg, '{"keys": {"equalize": "ALT+e"}}')
  T.setenv('HIVEGRID_SETTINGS', cfg)
  local config = {}
  require('layouts')(config, wz)
  T.eq(wz.stub.key(config.keys, 'e', 'ALT').kind, 'callback', 'rebound equalize')
  T.eq(wz.stub.key(config.keys, 'e', 'CTRL|SHIFT'), nil, 'default equalize key not bound as well')
  T.eq(wz.stub.key(config.keys, 'a', 'CTRL|SHIFT').kind, 'callback', 'default arrange key')
  T.ok(wz.stub.key(config.keys, 'e', 'LEADER'), 'LEADER+e always equalizes')
end)

-- Broadcast -------------------------------------------------------------------------

local function broadcast_setup(wz)
  local config = {}
  require('layouts')(config, wz)
  local win, tab = wz.fake.grid {
    { id = 1, left = 31, width = 100 }, { id = 2, left = 132, width = 100 },
    { id = 99, left = 0, width = 30 },
  }
  dock(wz, tab:tab_id(), 99)
  return config, win, tab
end

-- Presses LEADER+b and answers the prompt with `line` (nil = Esc).
local function arm(wz, config, win, line)
  local pane = win:active_pane()
  wz.stub.perform(wz.stub.key(config.keys, 'b', 'LEADER'), win, pane)
  local prompt = win.actions[#win.actions].action
  T.eq(prompt.kind, 'PromptInputLine')
  wz.stub.perform(prompt.args.action, win, pane, line)
end

local function sent(wz, id) return table.concat(wz.mux.get_pane(id).sent) end

test('broadcast arms only on exactly "broadcast"', function(wz)
  local config, win = broadcast_setup(wz)
  for i, line in ipairs { 'Broadcast', 'broadcast ', 'b', '' } do
    arm(wz, config, win, line)
    T.eq(wz.GLOBAL.broadcast, nil, string.format('%q', line))
    T.eq(win:active_key_table(), nil)
    T.eq(#win.toasts, i, 'a wrong word is explained')
  end
  arm(wz, config, win, nil) -- Esc
  T.eq(wz.GLOBAL.broadcast, nil)
  T.eq(#win.toasts, 4, 'Esc cancels silently')
  arm(wz, config, win, 'broadcast')
  T.eq(wz.GLOBAL.broadcast, { tab_id = win:active_tab():tab_id(), window_id = win:window_id() })
  T.eq(win:active_key_table(), 'broadcast')
end)

test('broadcast types into every grid pane, never into the dock', function(wz)
  local config, win = broadcast_setup(wz)
  arm(wz, config, win, 'broadcast')
  local bt = config.key_tables.broadcast
  for _, k in ipairs { 'l', 's', '|' } do
    local mods = k == '|' and 'SHIFT' or 'NONE'
    wz.stub.perform(wz.stub.key(bt, k, mods), win, win:active_pane())
  end
  T.eq(sent(wz, 1), 'ls|')
  T.eq(sent(wz, 2), 'ls|')
  T.eq(sent(wz, 99), '')
  win.actions = {}
  wz.stub.perform(wz.stub.key(bt, 'c', 'CTRL'), win, win:active_pane())
  local targets = {}
  for _, a in ipairs(win.actions) do
    T.eq(a.action, { kind = 'SendKey', args = { key = 'c', mods = 'CTRL' } })
    targets[#targets + 1] = a.pane:pane_id()
  end
  T.eq(targets, { 1, 2 }, 'CTRL+c goes to each grid pane')
end)

test('broadcast ends with a toast when the tab changed, nothing typed', function(wz)
  local config, win = broadcast_setup(wz)
  arm(wz, config, win, 'broadcast')
  local other = wz.fake.tab { panes = { { id = 7 } } }
  win.tabs[2] = other
  win.active = 2
  wz.stub.perform(wz.stub.key(config.key_tables.broadcast, 'x', 'NONE'), win, win:active_pane())
  T.eq(sent(wz, 7), '')
  T.eq(sent(wz, 1), '')
  T.eq(wz.GLOBAL.broadcast, nil)
  T.eq(win:active_key_table(), nil, 'key table popped')
  T.eq(#win.toasts, 1)
end)

test('broadcast: paste is refused with a toast; LEADER+b again turns it off', function(wz)
  local config, win = broadcast_setup(wz)
  arm(wz, config, win, 'broadcast')
  wz.stub.perform(wz.stub.key(config.key_tables.broadcast, 'v', 'CTRL'), win, win:active_pane())
  T.eq(sent(wz, 1) .. sent(wz, 2), '', 'nothing typed')
  T.eq(#win.actions, 2, 'no SendKey fanned out (prompt + key table only)')
  T.eq(#win.toasts, 1)
  T.ok(wz.GLOBAL.broadcast ~= nil, 'paste does not end broadcast')
  wz.stub.perform(wz.stub.key(config.key_tables.broadcast, 'b', 'LEADER'), win, win:active_pane())
  T.eq(wz.GLOBAL.broadcast, nil)
  T.eq(win:active_key_table(), nil)
end)
