-- agentstatus: per-pane agent state (omp title glyph, extension records, screen heuristics),
-- reading-order numbering, Alt+n / Alt+] navigation, the picker and the overview rows.
local T = require 'tests.lua.t'

local T0 = 1000000

local function advance(dt) T.clock(T.now() + dt) end

local function load()
  T.clock(T0)
  local config = {}
  local as = require('agentstatus')(config)
  return as, config
end

local function binding(config, key, mods)
  for _, b in ipairs(config.keys) do
    if b.key == key and b.mods == mods then return b.action end
  end
  error('no binding ' .. tostring(mods) .. '+' .. key)
end

-- An omp pane (process name as procfs reports it) with the given terminal title.
local function omp(wz, id, title, o)
  o = o or {}
  o.id, o.title = id, title
  o.process_name = o.process_name or '/usr/bin/omp'
  o.text = o.text or 'omp screen'
  return wz.fake.pane(o)
end

-- Overview rows of the tab keyed by pane id (scans unless a scan ran < 0.4 s ago).
local function rows(as, win, tab)
  local out = {}
  for _, r in ipairs(as.overview(win, tab:tab_id())) do out[r.id] = r end
  return out
end

local function ext_dir() return os.getenv('XDG_RUNTIME_DIR') .. '/hivegrid-agents' end

local function write_ext(wz, id, doc)
  T.write(ext_dir() .. '/' .. id .. '.json', wz.json_encode(doc))
end

-- A 2x2 grid of the given panes (row by row); `active` is the index of the active one.
local function grid2x2(wz, panes, active, wopts)
  local pos = { { 0, 0 }, { 41, 0 }, { 0, 13 }, { 41, 13 } }
  local r = {}
  for i, p in ipairs(panes) do
    r[i] = { pane = p, left = pos[i][1], top = pos[i][2], width = 40, height = 12, active = i == active }
  end
  return wz.fake.grid(r, wopts)
end

-- Title glyph --------------------------------------------------------------------------

T.test('omp title glyph gives working, idle and needs you, the session title is the heading', function(wz)
  local as = load()
  local win, tab = grid2x2(wz, {
    omp(wz, 11, 'π ⠋ refactor parser'),
    omp(wz, 12, 'π > fix tests'),
    omp(wz, 13, 'π ! approve plan'),
    omp(wz, 14, 'π ◔ long build'),
  }, 1, { focused = false })
  local r = rows(as, win, tab)
  T.eq(r[11].kind, 'working', 'spinner frame')
  T.eq(r[12].kind, 'idle', '">" = your turn')
  T.eq(r[13].kind, 'attention', '"!" = waits on you')
  T.eq(r[14].kind, 'working', 'other spinner glyph')
  T.eq({ r[11].title, r[12].title, r[13].title }, { 'refactor parser', 'fix tests', 'approve plan' })
end)

T.test('the title glyph stays authoritative after the screen has been quiet for long', function(wz)
  local as = load()
  local win, tab = grid2x2(wz, { omp(wz, 15, 'π ⠋ thinking'), omp(wz, 16, 'π > done here') }, 2,
    { focused = false })
  rows(as, win, tab)
  advance(60) -- screen text never changed: the timing heuristic alone would say not working
  local r = rows(as, win, tab)
  T.eq(r[15].kind, 'working')
  T.eq(r[16].kind, 'idle')
end)

T.test('title state is read before the process is known but ignored for another agent', function(wz)
  local as = load()
  local fresh = wz.fake.pane { id = 21, title = 'π > idle one', text = 'x' } -- no process name yet
  local claude = wz.fake.pane { id = 22, title = 'π ! not omp', text = 'x', process_name = '/usr/bin/claude' }
  local off = omp(wz, 23, 'π: my session') -- tui.titleState off: no run state
  local win, tab = grid2x2(wz, { fresh, claude, off }, 1, { focused = false })
  local r = rows(as, win, tab)
  T.eq(r[21].kind, 'idle')
  T.ok(r[22].kind ~= 'attention', 'a claude pane is not judged by an omp-looking title, got ' .. r[22].kind)
  T.eq(r[23].title, 'my session')
end)

-- State machine (internals.step) --------------------------------------------------------

local function ctx(now, o)
  o = o or {}
  return {
    now = now, focused = o.focused or false, proc = o.proc or 'claude',
    hit = o.hit or { working = false, ask = false }, title_state = o.title_state, ext = o.ext,
  }
end

T.test('screen heuristic: an agent that stops while unwatched is done until focused', function()
  local as = load()
  local step = as.internals.step
  local st = { changed_at = 100, run_start = 100 }
  T.eq(step(st, ctx(101)), 'working', 'screen changed 1 s ago')
  T.eq(step(st, ctx(103)), 'done', 'quiet for 3 s after working unwatched')
  T.eq(step(st, ctx(200)), 'done', 'stays done while nobody looks')
  T.eq(step(st, ctx(201, { focused = true })), 'idle', 'focus clears done')
  T.eq(step(st, ctx(202)), 'idle', 'and it does not come back after focus leaves')
end)

T.test('an agent watched while it worked and stopped is idle, not done', function()
  local as = load()
  local step = as.internals.step
  local st = { changed_at = 100, run_start = 100 }
  T.eq(step(st, ctx(101, { focused = true })), 'working')
  T.eq(step(st, ctx(104, { focused = true })), 'idle')
  T.eq(step(st, ctx(105)), 'idle', 'unfocusing later is not a finish')
end)

T.test('bell shows until focused; a question on screen outranks it', function()
  local as = load()
  local step = as.internals.step
  local st = { changed_at = 0, run_start = 0, bell = true }
  T.eq(step(st, ctx(100)), 'bell')
  T.eq(step(st, ctx(101, { hit = { working = false, ask = true } })), 'attention')
  T.eq(step(st, ctx(102, { focused = true })), 'idle')
  T.eq(step(st, ctx(103)), 'idle', 'bell cleared by focus')
end)

T.test('a shell prompt reads exited after an agent ran there, shell otherwise', function()
  local as = load()
  local step = as.internals.step
  T.eq(step({ changed_at = 0, run_start = 0 }, ctx(100, { proc = 'bash' })), 'shell')
  local st = { changed_at = 0, run_start = 0 }
  step(st, ctx(100, { proc = 'omp', title_state = 'idle' }))
  T.eq(step(st, ctx(101, { proc = 'zsh' })), 'exited')
  -- Even a still-fresh extension heartbeat cannot keep a quit agent alive.
  T.eq(step(st, ctx(102, { proc = 'bash', ext = 'working' })), 'exited')
end)

T.test('extension state is exact: it beats title, bell and prompt text, and idle never turns into done', function()
  local as = load()
  local step = as.internals.step
  local st = { changed_at = 0, run_start = 0, bell = true }
  local hit = { working = true, ask = true }
  T.eq(step(st, ctx(100, { proc = 'omp', title_state = 'working', hit = hit, ext = 'idle' })), 'idle')
  T.eq(step(st, ctx(101, { proc = 'omp', ext = 'busy' })), 'busy')
  T.eq(step(st, ctx(110, { proc = 'omp', ext = 'idle' })), 'idle', 'ext idle after busy unwatched')
  T.eq(step(st, ctx(111, { proc = 'omp', ext = 'done' })), 'done')
end)

T.test('omp loader markers, once learned, decide working; long continuous change still counts', function(wz)
  local as = load()
  local step = as.internals.step
  local none = { working = false, ask = false }
  local loader = { working = true, ask = false }
  -- Not learned yet: a changing omp screen is working by timing alone.
  T.eq(step({ changed_at = 100, run_start = 100 }, ctx(101, { proc = 'omp', hit = none })), 'working')
  T.ok(not wz.GLOBAL.agentstatus_markers_seen, 'no marker seen yet')
  -- A marker on a changing screen teaches it.
  T.eq(step({ changed_at = 100, run_start = 100 }, ctx(101, { proc = 'omp', hit = loader })), 'working')
  T.eq(wz.GLOBAL.agentstatus_markers_seen, true)
  -- Learned: change without a marker is a redraw, not work...
  T.eq(step({ changed_at = 101, run_start = 100 }, ctx(102, { proc = 'omp', hit = none, focused = true })), 'idle')
  -- ...unless the screen has kept changing for RUN_SECS.
  T.eq(step({ changed_at = 106, run_start = 100 }, ctx(106, { proc = 'omp', hit = none, focused = true })), 'working')
  -- A marker on a screen frozen for MARKER_SECS is a quote in the transcript.
  T.eq(step({ changed_at = 100, run_start = 100 }, ctx(110, { proc = 'omp', hit = loader, focused = true })), 'idle')
end)

T.test('markers: braille spinner at line start or loader text is working, prompts are asks', function()
  local as = load()
  local m = as.internals.markers
  T.eq(m('  ⠹ Thinking about it\n> '), { working = true, ask = false })
  T.eq(m('esc to interrupt'), { working = true, ask = false })
  T.eq(m('text ⠹ mid-line spinner'), { working = false, ask = false })
  T.eq(m('Overwrite file? [y/N]'), { working = false, ask = true })
  T.eq(m('1. Allow once\n2. Deny'), { working = false, ask = true })
  T.eq(m(''), { working = false, ask = false })
end)

-- Extension records ---------------------------------------------------------------------

T.test('a fresh extension record beats the title and supplies title, reason, cwd and since', function(wz)
  local as = load()
  write_ext(wz, 31, {
    state = 'attention', updated = T0 - 5, since = T0 - 42, title = 'Plan: parser',
    reason = 'plan review', cwd = '/work/parser',
  })
  local win, tab = grid2x2(wz, { omp(wz, 31, 'π ⠋ title says working'), omp(wz, 32, 'π > x') }, 2,
    { focused = false })
  local r = rows(as, win, tab)[31]
  T.eq({ r.kind, r.title, r.reason, r.name, r.since },
    { 'attention', 'Plan: parser', 'plan review', 'parser', T0 - 42 })
end)

T.test('stale, malformed or unknown-state extension files fall back to the title', function(wz)
  local as = load()
  write_ext(wz, 33, { state = 'attention', updated = T0 - 21, since = T0 - 30 })
  T.write(ext_dir() .. '/34.json', '{"state": "attention", "updated": ')
  write_ext(wz, 35, { state = 'exited', updated = T0, since = T0 })
  write_ext(wz, 36, { state = 'attention', since = T0 }) -- no heartbeat
  local win, tab = grid2x2(wz, {
    omp(wz, 33, 'π ⠋ a'), omp(wz, 34, 'π ⠋ b'), omp(wz, 35, 'π ⠋ c'), omp(wz, 36, 'π ⠋ d'),
  }, 1, { focused = false })
  local r = rows(as, win, tab)
  T.eq({ r[33].kind, r[34].kind, r[35].kind, r[36].kind }, { 'working', 'working', 'working', 'working' })
  T.eq(r[33].reason, nil)
end)

T.test('an extension record goes stale while the pane keeps running', function(wz)
  local as = load()
  write_ext(wz, 37, { state = 'attention', updated = T0, since = T0 })
  local win, tab = grid2x2(wz, { omp(wz, 37, 'π > x'), omp(wz, 38, 'π > y') }, 2, { focused = false })
  T.eq(rows(as, win, tab)[37].kind, 'attention')
  advance(21)
  T.eq(rows(as, win, tab)[37].kind, 'idle', 'heartbeat 21 s old: the title decides')
end)

-- Acknowledging done --------------------------------------------------------------------

T.test('looking at a done pane acknowledges it in acks.json and keeps other panes acks', function(wz)
  local as = load()
  T.write(ext_dir() .. '/acks.json', '{"7": 123}')
  write_ext(wz, 41, { state = 'done', updated = T0, since = 900.5 })
  write_ext(wz, 42, { state = 'done', updated = T0, since = 901 })
  local win, tab = grid2x2(wz, { omp(wz, 41, 'π > a'), omp(wz, 42, 'π > b') }, 1) -- 41 focused
  local r = rows(as, win, tab)
  T.eq(r[41].kind, 'idle', 'the focused done is seen')
  T.eq(r[42].kind, 'done', 'the background done is not')
  local acks = wz.json_parse(T.read(ext_dir() .. '/acks.json'))
  T.eq(acks, { ['7'] = 123, ['41'] = 900.5 })
end)

T.test('a done acknowledged elsewhere reads idle; a later done shows again', function(wz)
  local as = load()
  T.write(ext_dir() .. '/acks.json', '{"43": 900}')
  write_ext(wz, 43, { state = 'done', updated = T0, since = 900 })
  local win, tab = grid2x2(wz, { omp(wz, 43, 'π > a'), omp(wz, 44, 'π > b') }, 2, { focused = false })
  T.eq(rows(as, win, tab)[43].kind, 'idle')
  write_ext(wz, 43, { state = 'done', updated = T0 + 1, since = 950 })
  advance(1)
  T.eq(rows(as, win, tab)[43].kind, 'done')
  T.eq(wz.json_parse(T.read(ext_dir() .. '/acks.json')), { ['43'] = 900 }, 'unfocused: nothing written')
end)

-- Numbering and navigation --------------------------------------------------------------

T.test('agents are numbered in reading order with the sidebar and overview dock left out', function(wz)
  local as = load()
  local p = {}
  for id = 51, 56 do p[id] = omp(wz, id, 'π > ' .. id) end
  local win, tab = wz.fake.grid({
    { pane = p[56], left = 0, top = 0, width = 5, height = 25 },     -- sidebar bar
    { pane = p[54], left = 47, top = 13, width = 40, height = 12 },  -- bottom right
    { pane = p[51], left = 6, top = 0, width = 40, height = 12 },    -- top left
    { pane = p[53], left = 6, top = 13, width = 40, height = 12 },   -- bottom left
    { pane = p[52], left = 47, top = 0, width = 40, height = 12, active = true },
    { pane = p[55], left = 88, top = 0, width = 30, height = 25 },   -- overview dock
  }, { focused = false })
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 56 } }
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 55 } }
  local list = as.overview(win, tab:tab_id())
  local ids, nums = {}, {}
  for i, r in ipairs(list) do ids[i], nums[i] = r.id, r.num end
  T.eq(ids, { 51, 52, 53, 54 })
  T.eq(nums, { 1, 2, 3, 4 })
  T.eq({ as.agent_number(51), as.agent_number(54), as.agent_number(55), as.agent_number(56) }, { 1, 4, nil, nil })
end)

T.test('running counts the panes that work, are busy or need you, across tabs; the rest is not counted', function(wz)
  local as = load()
  write_ext(wz, 51, { state = 'busy', updated = T0 - 1, since = T0 - 4 })
  local win = grid2x2(wz, {
    omp(wz, 50, 'π ⠋ a'), omp(wz, 51, 'π > b'), omp(wz, 52, 'π ! c'), omp(wz, 53, 'π > d'),
  }, 1, { focused = false })
  T.eq(as.running(win), { working = 1, busy = 1, attention = 1 })
  T.eq(as.running(win), as.running(win), 'a second call in the same tick agrees')
end)

T.test('focus_dir is the full directory of the active grid pane, ~ expanded, nil for a web pane', function(wz)
  local as = load()
  local home = wz.home_dir
  local win = grid2x2(wz, {
    omp(wz, 60, 'π > a', { cwd = home .. '/work/proj' }), omp(wz, 61, 'π > b', { cwd = '/srv/other' }),
  }, 1, { focused = false })
  T.eq(as.focus_dir(win), home .. '/work/proj')
  advance(1)
  win:active_tab().rows[1].active, win:active_tab().rows[2].active = false, true
  T.eq(as.focus_dir(win), '/srv/other')
  local web = wz.fake.pane { id = 62, user_vars = { hivegrid_web = '1' }, cwd = wz.fake.url('https://example.com/') }
  local wwin = grid2x2(wz, { web }, 1, { focused = false })
  T.eq(as.focus_dir(wwin), nil)
end)

T.test('Alt+n skips idle and done agents and goes to the pane that needs you', function(wz)
  local _, config = load()
  local win = grid2x2(wz, {
    omp(wz, 61, 'π > me'), omp(wz, 62, 'π > idle'), omp(wz, 63, 'π ! ask'), omp(wz, 64, 'π ⠋ busy'),
  }, 1)
  wz.stub.perform(binding(config, 'n', 'ALT'), win, wz.mux.get_pane(61))
  T.eq(wz.stub.activations, { 63 })
  T.eq(#win.toasts, 0)
end)

T.test('Alt+n and LEADER+n cycle through the panes that need you in order, wrapping', function(wz)
  local _, config = load()
  local win = grid2x2(wz, {
    omp(wz, 65, 'π ! first'), omp(wz, 66, 'π ⠋ w'), omp(wz, 67, 'π > me'), omp(wz, 68, 'π ! second'),
  }, 3)
  local jump = binding(config, 'n', 'ALT')
  wz.stub.perform(jump, win, wz.mux.get_pane(67))
  T.eq(wz.stub.activations, { 68 }, 'the one after the active pane')
  advance(1)
  wz.stub.perform(binding(config, 'n', 'LEADER'), win, wz.mux.get_pane(68))
  T.eq(wz.stub.activations, { 68, 65 }, 'wraps to the first')
  advance(1)
  wz.stub.perform(jump, win, wz.mux.get_pane(65))
  T.eq(wz.stub.activations, { 68, 65, 68 }, 'and back')
end)

T.test('Alt+n also takes a bell, but not done, idle or unseen output', function(wz)
  local as, config = load()
  local jump = binding(config, 'n', 'ALT')
  local win = grid2x2(wz, {
    omp(wz, 71, 'π ⠋ me'), omp(wz, 72, 'π ⠋ w', { unseen = true }), omp(wz, 73, 'π > idle'),
  }, 1)
  wz.stub.perform(jump, win, wz.mux.get_pane(71))
  T.eq(wz.stub.activations, {}, 'nothing needs you')
  T.eq(#win.toasts, 1)
  T.eq(win.toasts[1].title, 'Hivegrid')
  T.eq(win.toasts[1].msg, 'No agent needs you')

  as.bell(win, wz.mux.get_pane(73))
  advance(1)
  wz.stub.perform(jump, win, wz.mux.get_pane(71))
  T.eq(wz.stub.activations, { 73 }, 'a bell counts')
  T.eq(#win.toasts, 1)
end)

T.test('Alt+n reaches a pane in another tab', function(wz)
  local _, config = load()
  local t1 = wz.fake.tab { panes = { { pane = omp(wz, 75, 'π ⠋ me'), active = true } } }
  local t2 = wz.fake.tab { panes = { { pane = omp(wz, 76, 'π ! there'), active = true } } }
  local t3 = wz.fake.tab { panes = { { pane = omp(wz, 77, 'π > quiet'), active = true } } }
  local win = wz.fake.window { tabs = { t1, t2, t3 } }
  wz.stub.perform(binding(config, 'n', 'ALT'), win, wz.mux.get_pane(75))
  T.eq(wz.stub.activations, { 76 })
end)

T.test('Alt+] and Alt+[ step through grid panes in reading order, wrapping, skipping the dock', function(wz)
  local _, config = load()
  local p = {}
  for id = 81, 85 do p[id] = omp(wz, id, 'π > x') end
  local win, tab = wz.fake.grid({
    { pane = p[84], left = 41, top = 13 },              -- index 0, number 4
    { pane = p[81], left = 0, top = 0 },                -- index 1, number 1
    { pane = p[85], left = 82, top = 0 },               -- index 2, the dock
    { pane = p[83], left = 0, top = 13 },               -- index 3, number 3
    { pane = p[82], left = 41, top = 0, active = true }, -- index 4, number 2
  })
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 85 } }
  local function last_index() return win.actions[#win.actions].action.args end
  wz.stub.perform(binding(config, ']', 'ALT'), win, p[82])
  T.eq(last_index(), 3, 'number 2 -> number 3 (index 3)')
  tab:set_rows {
    { pane = p[84], left = 41, top = 13, active = true }, { pane = p[81], left = 0, top = 0 },
    { pane = p[85], left = 82, top = 0 }, { pane = p[83], left = 0, top = 13 }, { pane = p[82], left = 41, top = 0 },
  }
  wz.stub.perform(binding(config, ']', 'ALT'), win, p[84])
  T.eq(last_index(), 1, 'last number wraps to number 1, never the dock')
  tab:set_rows {
    { pane = p[84], left = 41, top = 13 }, { pane = p[81], left = 0, top = 0, active = true },
    { pane = p[85], left = 82, top = 0 }, { pane = p[83], left = 0, top = 13 }, { pane = p[82], left = 41, top = 0 },
  }
  wz.stub.perform(binding(config, '[', 'ALT'), win, p[81])
  T.eq(last_index(), 0, 'number 1 back wraps to number 4')
end)

T.test('the picker lists panes in reading order and activates the chosen one', function(wz)
  local _, config = load()
  local win = grid2x2(wz, {
    omp(wz, 91, 'π > a'), omp(wz, 92, 'π ! b'), omp(wz, 93, 'π ⠋ c'),
  }, 1)
  wz.stub.perform(binding(config, 'g', 'LEADER'), win, wz.mux.get_pane(91))
  local pick = wz.stub.picker(win)
  local ids, keys = {}, {}
  for i, r in ipairs(pick.spec.rows) do ids[i], keys[i] = r.id, r.key end
  T.eq(ids, { '91', '92', '93' })
  T.eq(keys, { '1', '2', '3' }, 'in one tab the digits are the dock numbers')
  local texts = {}
  for _, c in ipairs(pick.spec.rows[2].cols) do texts[#texts + 1] = c.text end
  T.ok(table.concat(texts, ' '):find('b', 1, true), 'row shows the session title')
  T.eq(pick.spec.rows[1].meta, 'here', 'the pane it was opened from is marked')
  pick.answer(nil)
  T.eq(wz.stub.activations, {}, 'Esc does nothing')
  wz.stub.perform(binding(config, 'g', 'LEADER'), win, wz.mux.get_pane(91))
  wz.stub.picker(win).answer('93')
  T.eq(wz.stub.activations, { 93 })
end)

-- Rows, bell, reload, tab titles --------------------------------------------------------

T.test('rows name the directory (the agent own cwd first) and a non-agent program by name', function(wz)
  local as = load()
  local htop = wz.fake.pane { id = 101, process_name = '/usr/bin/htop', cwd = '/srv/box', text = 'x', title = 'htop' }
  local o = omp(wz, 102, 'π > t', { cwd = '/home/u/shell-dir', process_info = { cwd = '/home/u/real-dir' } })
  local win, tab = grid2x2(wz, { htop, o }, 1)
  local r = rows(as, win, tab)
  T.eq({ r[101].name, r[101].proc, r[101].title }, { 'box', 'htop', 'htop' })
  -- Watched and quiet: a plain program, shown by its own name.
  advance(4)
  r = rows(as, win, tab)
  T.eq({ r[101].kind, r[101].text }, { 'other', 'htop' })
  T.eq(r[102].name, 'real-dir', '`cd x && omp`: the process cwd beats the shell report')
end)

T.test('a pane first seen (startup, new pane, config reload) is a baseline, not work then done', function(wz)
  local as = load()
  local sh = wz.fake.pane { id = 105, process_name = '/bin/bash', text = '$ ', title = 'bash' }
  local vim = wz.fake.pane { id = 106, process_name = '/usr/bin/vim', text = 'static', title = 'vim' }
  local cl = wz.fake.pane { id = 107, process_name = '/usr/bin/claude', text = 'static claude', title = 'c' }
  local win, tab = grid2x2(wz, { sh, vim, cl }, 1)
  local r = rows(as, win, tab)
  T.eq({ r[106].kind, r[107].kind }, { 'other', 'idle' }, 'first sight')
  advance(5)
  r = rows(as, win, tab)
  T.eq({ r[106].kind, r[107].kind }, { 'other', 'idle' }, 'nothing ran: no done afterwards')
  package.loaded['agentstatus'] = nil
  local as2 = require('agentstatus')({})
  advance(1)
  rows(as2, win, tab)
  advance(5)
  r = rows(as2, win, tab)
  T.eq({ r[106].kind, r[107].kind }, { 'other', 'idle' }, 'after a config reload')
end)

T.test('since marks when the state began and is kept while it holds', function(wz)
  local as = load()
  local p = omp(wz, 111, 'π > waiting')
  local win, tab = grid2x2(wz, { p, omp(wz, 112, 'π > b') }, 2, { focused = false })
  T.eq(rows(as, win, tab)[111].since, T0)
  advance(5)
  T.eq(rows(as, win, tab)[111].since, T0, 'unchanged state keeps its start')
  p.o.title = 'π ⠋ waiting'
  advance(1)
  local r = rows(as, win, tab)[111]
  T.eq({ r.kind, r.since }, { 'working', T0 + 6 })
end)

T.test('a bell in a background pane shows until focused; one in the watched pane is ignored', function(wz)
  local as = load()
  local a, b = omp(wz, 121, 'π > a'), omp(wz, 122, 'π > b')
  local win, tab = grid2x2(wz, { a, b }, 1)
  rows(as, win, tab)
  as.bell(win, a)
  as.bell(win, b)
  advance(1)
  local r = rows(as, win, tab)
  T.eq({ r[121].kind, r[122].kind }, { 'idle', 'bell' })
  T.eq(as.pane_kind(122), 'bell')
  b:activate()
  advance(1)
  rows(as, win, tab)
  a:activate()
  advance(1)
  T.eq(rows(as, win, tab)[122].kind, 'idle', 'focusing cleared the bell')
end)

T.test('a config reload keeps a pane flags (bell) and its state age', function(wz)
  local as = load()
  local a, b = omp(wz, 131, 'π > a'), omp(wz, 132, 'π > b')
  local win, tab = grid2x2(wz, { a, b }, 1)
  rows(as, win, tab)
  as.bell(win, b)
  advance(1)
  local before = rows(as, win, tab)[132]
  T.eq(before.kind, 'bell')
  package.loaded['agentstatus'] = nil
  local as2 = require('agentstatus')({})
  advance(1)
  local after = rows(as2, win, tab)[132]
  T.eq({ after.kind, after.since }, { 'bell', before.since })
end)

T.test('tab name: explicit title, else the scanned directory with its agent, no process for a shell', function(wz)
  local as = load()
  local o = omp(wz, 141, 'π > t', { cwd = '/w/proj' })
  local sh = wz.fake.pane { id = 142, process_name = '/bin/bash', cwd = '/w/other', text = '$ ' }
  local win, tab = grid2x2(wz, { o, sh }, 1)
  rows(as, win, tab)
  local tid = tab:tab_id()
  T.eq(as.tab_parts { tab_id = tid, tab_title = '', active_pane = { pane_id = 141, title = 'x' } },
    { name = 'proj', proc = 'omp' })
  T.eq(as.tab_parts { tab_id = tid, tab_title = '', active_pane = { pane_id = 142, title = 'x' } },
    { name = 'other' })
  T.eq(as.tab_parts { tab_id = tid, tab_title = 'Mine', active_pane = { pane_id = 141, title = 'x' } },
    { name = 'Mine' })
  -- A pane never scanned (fresh, or a reload) in a tab never scanned: the shell's directory.
  T.eq(as.tab_parts { tab_id = 9999, tab_title = '', active_pane = {
    pane_id = 9998, title = 'bash', current_working_dir = { file_path = '/a/b' } } }, { name = 'b' })
end)
