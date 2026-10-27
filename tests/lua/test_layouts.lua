-- config/layouts.lua through its public surface: api.arrange / api.equalize and the key
-- bindings / key tables it installs, and its handler for the fork's pane-drag-drop event.
-- Nothing else in it runs on its own.
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

-- Drag and drop (the fork's pane-drag-drop event) ----------------------------------------

local function dropped(wz, mux, from, to, zone)
  wz.stub.call('pane-drag-drop', mux.window, wz.mux.get_pane(from), wz.mux.get_pane(to), zone)
end

test('dropping a pane on the middle of another swaps them, keyboard follows the dragged one', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3 }, 250) })
  dropped(wz, mux, 1, 3, 'center')
  T.eq((reading(mux)), { 3, 2, 1 })
  T.eq(mux.tabs[1].active, 1)
end)

test('dropping on an edge moves the pane beside the target, the others keep their order', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3 }, 250) })
  dropped(wz, mux, 1, 3, 'bottom')
  local ids, _, list = reading(mux)
  T.eq(ids, { 2, 3, 1 }, 'reading order: 2 on top, 3 over 1')
  T.eq(list[2].left, list[3].left, '3 and 1 share a column')
  local three, one = mux:geometry(3), mux:geometry(1)
  T.eq(three.left, one.left, 'same column')
  T.ok(three.top < one.top, '3 above 1')
  T.ok(math.abs(three.height - one.height) <= 1, 'the target is split in half')
  T.eq(#mux.tabs, 1)
end)

test('dropping on the left edge puts the dragged pane left of the target', function(wz)
  local root = FM.v(29, FM.h(124, FM.leaf(1), FM.leaf(2)), FM.leaf(3))
  local mux = setup(wz, { cols = 250, rows = 60, root = root })
  dropped(wz, mux, 3, 1, 'left')
  local a, b, c = mux:geometry(3), mux:geometry(1), mux:geometry(2)
  T.ok(a.left < b.left and a.top == b.top, '3 left of 1, same row')
  T.ok(b.left < c.left, '1 still left of 2')
  T.eq(a.height, c.height, 'one row now')
end)

test('a drop never moves the sidebar and ignores the sidebar as source or target', function(wz)
  local root = FM.h(30, FM.leaf(99), FM.h(124, FM.leaf(1), FM.leaf(2)))
  local mux = setup(wz, { cols = 280, rows = 60, root = root })
  dock(wz, mux.tab:tab_id(), 99)
  dropped(wz, mux, 1, 99, 'center')
  dropped(wz, mux, 99, 1, 'top')
  T.eq(#mux:commands(), 0, 'nothing was moved')
  dropped(wz, mux, 1, 2, 'right')
  T.eq(mux:geometry(99).width, 30, 'sidebar untouched')
  T.eq(mux:geometry(99).left, 0)
end)

-- Nothing re-tiles by itself ---------------------------------------------------------
-- A split, a close or a resize leaves every pane where it is: the only commands the mux
-- sees come from the user's own actions (arrange, equalize, rotate, swap).

local function moves(mux)
  local bad = {}
  for _, c in ipairs(mux:commands()) do
    if c:find('move%-pane') or c:find('%-%-move%-pane%-id') or c:find('^adjust%-pane%-size') then
      bad[#bad + 1] = c
    end
  end
  return bad
end

test('layouts registers no event handler: nothing runs on status ticks or window resizes', function(wz)
  setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  for _, event in ipairs { 'update-status', 'window-resized', 'window-config-reloaded', 'user-var-changed' } do
    T.eq(wz.stub.handlers[event] or {}, {}, event)
  end
end)

test('a pane gained or lost keeps every other pane where it is', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2 }, 250) })
  local before = mux:geometry(1)
  local _, new = mux:split(2, 'h', 50)
  mux:sync()
  T.eq(mux:commands(), {}, 'a split issues no layout command')
  T.eq(mux:geometry(1), before, 'the first pane stayed')
  mux:kill(new)
  mux:sync()
  T.eq(mux:commands(), {}, 'closing a pane issues no layout command either')
  T.eq(#mux.tabs, 1)
end)

test('a hand-built or tidy grid that gains a pane is neither re-tiled nor evened', function(wz)
  local root = FM.v(29, FM.strip('h', { 1, 2, 3 }, 250), FM.strip('h', { 4, 5 }, 250))
  local mux = setup(wz, { cols = 250, rows = 60, root = root })
  mux:split(3, 'h', 50)
  mux:sync()
  T.eq(moves(mux), {})
  T.eq(count(mux, 'split-pane'), 0)
  T.eq(count(mux, 'move-pane-to-new-tab'), 0)
end)

test('the sidebar opening beside the grid does not even it or move a pane', function(wz)
  local mux = setup(wz, { cols = 250, rows = 60, root = FM.h(60, FM.leaf(1), FM.leaf(2)) })
  mux.tabs[1].root = FM.h(30, FM.leaf(99), mux.tabs[1].root)
  dock(wz, mux.tab:tab_id(), 99)
  mux:sync()
  T.eq(mux:commands(), {})
  T.eq(mux:geometry(1).width, 60)
  T.eq(mux:geometry(99).width, 30)
end)

test('equalize still moves dividers when asked for', function(wz)
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3 }, 250) })
  mux:split(3, 'h', 50)
  mux:sync()
  mux.log = {}
  api.equalize(mux.window, mux.window:active_pane())
  T.ok(count(mux, 'adjust-pane-size') > 0, 'equalize resizes the dividers')
  local _, _, ordered = reading(mux)
  T.ok(spread(field(ordered, 'width')) <= 1, 'widths even: ' .. T.show(field(ordered, 'width')))
end)

test('arrange still re-tiles when asked for', function(wz)
  local mux, api = setup(wz, { cols = 250, rows = 60, root = FM.strip('h', { 1, 2, 3, 4, 5 }, 250) })
  mux.log = {}
  api.arrange(mux.window, mux.window:active_pane())
  T.ok(count(mux, 'move-pane-to-new-tab') > 0, 'arrange moves panes')
  local _, counts = reading(mux)
  T.eq(counts, { 3, 2 })
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

-- A window resize that lands while equalize runs (the run takes seconds: every step is a
-- `wezterm cli` process) used to leave its targets, computed for the old area, pointing past
-- the new edge: the row divider was pushed to the far end and the panes behind it kept a
-- row or two. The Hivegrid build scales every split in proportion on a resize; the model
-- here does the same for the rows when the hook below resizes the window.
test('equalize: a window resize in the middle of the run does not collapse a row', function(wz)
  local root = FM.v(37, FM.h(50, FM.leaf(1), FM.h(80, FM.leaf(2), FM.leaf(3))),
    FM.h(100, FM.leaf(4), FM.h(30, FM.leaf(5), FM.leaf(6))))
  local mux, api = setup(wz, { cols = 200, rows = 54, root = root })
  local function shrink(rows)
    local ratio = rows / mux.rows
    local function walk(node)
      if not node.id then
        if node.dir == 'v' then node.first = math.max(1, math.floor(node.first * ratio)) end
        walk(node.a)
        walk(node.b)
      end
    end
    walk(mux.tabs[1].root)
    mux.rows = rows
  end
  local adjusts = 0
  mux.fail = function(args)
    if args[1] == 'adjust-pane-size' then
      adjusts = adjusts + 1
      if adjusts == 4 then shrink(30) end
    end
    return false
  end
  api.equalize(mux.window, wz.mux.get_pane(1))
  T.ok(adjusts > 4, 'the resize came during the run')
  local _, counts, ordered = reading(mux)
  T.eq(counts, { 3, 3 })
  T.ok(spread(field(ordered, 'height')) <= 1, 'heights: ' .. T.show(field(ordered, 'height')))
  T.ok(ordered[4].height >= 8, 'bottom row: ' .. T.show(field(ordered, 'height')))
  T.ok(spread(field({ ordered[4], ordered[5], ordered[6] }, 'width')) <= 1,
    'bottom widths: ' .. T.show(field(ordered, 'width')))
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
  local view = open_view(wz, mux, 1, 30)
  T.eq(mux:commands('adjust-pane-size'), {}, 'nothing re-evens when the view opens')
  api.equalize(mux.window, wz.mux.get_pane(2))
  T.eq(mux:commands('adjust-pane-size'), {}, 'view + first pane count as one column')
  T.eq(mux:geometry(view).width, 30)
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
