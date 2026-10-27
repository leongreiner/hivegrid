-- session: the snapshot of the layout (what is saved, when, and what is never saved over), the
-- split tree it keeps, and the restore at GUI startup (windows, tabs, splits, omp chats, web
-- pages, what a damaged or foreign file cannot do).
local T = require 'tests.lua.t'

local function mkdir(path) assert(os.execute('mkdir -p ' .. T.sh_quote(path))) end

-- The session module wired like config/wezterm.lua wires it, with a fake agent scan (`facts`,
-- pane id -> what agentstatus.facts reports) and a state directory of its own. Returns the
-- api, the session file path and a table of what the fakes were asked.
local function setup(wz, o)
  o = o or {}
  local state = T.tmpdir()
  T.setenv('XDG_STATE_HOME', state)
  mkdir(state .. '/hivegrid')
  wz.stub.on_child = function(argv)
    if argv[1] == 'mkdir' then mkdir(argv[#argv]) return true, '', '' end
    return false, '', ''
  end
  local facts = {}
  local seen = { toggles = {}, spawned = {} }
  local as = { facts = function(id) return facts[id] end }
  local sb = {
    is_dock = require('sidebar').is_dock,
    toggle = function(_, _, view) seen.toggles[#seen.toggles + 1] = view end,
  }
  -- A window the GUI would have made: spawn_window and spawn_tab of the fake mux.
  wz.mux.spawn_window = function(opts)
    seen.spawned[#seen.spawned + 1] = opts
    local function make(win)
      local pane = wz.fake.pane { cwd = opts.cwd, dims = { cols = opts.width or 80, viewport_rows = opts.height or 24 } }
      local tab = wz.fake.tab { panes = { { pane = pane, left = 0, top = 0, width = opts.width or 80, height = opts.height or 24, active = true } } }
      tab._window = win
      return tab, pane
    end
    local win = wz.fake.window { tabs = {} }
    local tab, pane = make(win)
    win.tabs[1] = tab
    local mux = win:mux_window()
    function mux:spawn_tab(topts)
      local pane2 = wz.fake.pane { cwd = topts.cwd, dims = { cols = opts.width or 80, viewport_rows = opts.height or 24 } }
      local tab2 = wz.fake.tab { panes = { { pane = pane2, left = 0, top = 0, width = opts.width or 80, height = opts.height or 24, active = true } } }
      tab2._window = win
      table.insert(win.tabs, tab2)
      seen.spawned[#seen.spawned + 1] = topts
      return tab2, pane2, mux
    end
    seen.window = win
    return tab, pane, mux
  end
  local config = { keys = {} }
  local api = require('session')(config, wz, { as = as, sb = sb, web = o.web })
  return api, state .. '/hivegrid/session.json', facts, seen
end

local function read_json(wz, path)
  local text = T.read(path)
  return text and wz.json_parse(text) or nil
end

-- The size the rectangles a tree describes get when it is built the way restore builds it
-- (a split gives the second part floor(frac * (cells - 1) + 0.5) cells), as { left, top, width, height }
-- per leaf in order.
local function replay(node, left, top, width, height, out)
  out = out or {}
  if not node.dir then
    out[#out + 1] = { left = left, top = top, width = width, height = height, leaf = node }
    return out
  end
  if node.dir == 'right' then
    local second = math.floor(node.frac * (width - 1) + 0.5)
    local first = width - 1 - second
    replay(node.a, left, top, first, height, out)
    replay(node.b, left + first + 1, top, second, height, out)
  else
    local second = math.floor(node.frac * (height - 1) + 0.5)
    local first = height - 1 - second
    replay(node.a, left, top, width, first, out)
    replay(node.b, left, top + first + 1, width, second, out)
  end
  return out
end

local function items(rects)
  local out = {}
  for i, r in ipairs(rects) do
    out[i] = { left = r[1], top = r[2], width = r[3], height = r[4], leaf = { id = i } }
  end
  return out
end

-- The tree of a tab -----------------------------------------------------------------------

T.test('tree_of: a tiling comes back at the same sizes when it is built from the tree', function(wz)
  local api = setup(wz)
  local tree_of = api.internals.tree_of
  -- 3 columns, the middle one split in two, the last in three: dividers are one cell.
  local rects = {
    { 0, 0, 60, 40 }, { 61, 0, 70, 19 }, { 61, 20, 70, 20 },
    { 132, 0, 68, 12 }, { 132, 13, 68, 13 }, { 132, 27, 68, 13 },
  }
  local its = items(rects)
  local tree = tree_of(its)
  local got = replay(tree, 0, 0, 200, 40)
  T.eq(#got, 6)
  for _, g in ipairs(got) do
    local want = rects[g.leaf.id]
    for i, k in ipairs { 'left', 'top', 'width', 'height' } do
      T.near(g[k], want[i], 1, 'pane ' .. g.leaf.id .. ' ' .. k)
    end
  end
end)

T.test('tree_of: the first leaf is the top-left pane, and every pane appears once', function(wz)
  local api = setup(wz)
  local its = items { { 101, 0, 99, 50 }, { 0, 0, 100, 25 }, { 0, 26, 100, 24 } }
  local tree = api.internals.tree_of(its)
  T.eq(api.internals.first_leaf(tree).id, 2)
  local ids = {}
  replay(tree, 0, 0, 200, 50)
  for _, g in ipairs(replay(tree, 0, 0, 200, 50)) do ids[#ids + 1] = g.leaf.id end
  table.sort(ids)
  T.eq(ids, { 1, 2, 3 })
end)

T.test('tree_of: a pinwheel (no straight cut) still keeps every pane', function(wz)
  local api = setup(wz)
  local its = items { { 0, 0, 60, 20 }, { 61, 0, 39, 30 }, { 40, 31, 60, 19 }, { 0, 21, 39, 29 } }
  local ids = {}
  for _, g in ipairs(replay(api.internals.tree_of(its), 0, 0, 100, 50)) do ids[#ids + 1] = g.leaf.id end
  table.sort(ids)
  T.eq(ids, { 1, 2, 3, 4 })
end)

-- What the file holds -------------------------------------------------------------------

-- Two panes side by side: omp (a chat, a profile) and a shell; plus the bar and the dock, which
-- are chrome and not saved.
local function two_pane_window(wz, facts)
  facts[1] = { proc = 'omp', path = '/work/a', profile = 'work', session = { id = 'sid', file = '/s/a.jsonl', profile = 'work' } }
  facts[2] = { proc = 'bash', path = '/work/b' }
  local win, tab = wz.fake.grid({
    { id = 9, left = 0, top = 0, width = 5, height = 50 },
    { id = 1, left = 6, top = 0, width = 100, height = 50, active = true },
    { id = 2, left = 107, top = 0, width = 63, height = 50 },
    { id = 8, left = 171, top = 0, width = 30, height = 50 },
  }, { dims = { pixel_width = 1920, pixel_height = 1080, dpi = 96, is_full_screen = false } })
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 9, view = 'git', last = 1 } }
  wz.GLOBAL.overview = { [tostring(tab:tab_id())] = { pane = 8 } }
  tab:set_title('my tab')
  return win, tab
end

T.test('save: layout, chats and directories are written; the bar and the dock are not', function(wz)
  local api, file, facts = setup(wz)
  local _, tab = two_pane_window(wz, facts)
  api.save(100)
  local doc = read_json(wz, file)
  T.eq(doc.version, 1)
  local t = doc.windows[1].tabs[1]
  T.eq(t.title, 'my tab')
  T.eq(t.active, true)
  T.eq(t.layout.dir, 'right')
  T.eq(t.layout.a.kind, 'omp')
  T.eq(t.layout.a.cwd, '/work/a')
  T.eq(t.layout.a.profile, 'work')
  T.eq(t.layout.a.session, '/s/a.jsonl')
  T.eq(t.layout.a.active, true)
  T.eq(t.layout.b.kind, 'shell')
  T.eq(t.layout.b.cwd, '/work/b')
  T.ok(t.layout.a.cwd ~= nil and t.layout.b.session == nil)
  T.near(t.layout.frac, 63 / 163, 0.001, 'the share of the second pane')
  T.eq(t.sidebar, nil, 'the view was not open (no content pane)')
  T.eq(doc.windows[1].cols, 201)
  T.eq(doc.windows[1].rows, 50)
  T.eq(wz.stub.background[#wz.stub.background], { 'sync', '-f', file }, 'flushed to disk in the background')
  T.ok(tab ~= nil)
end)

T.test('save: an open sidebar view, a closed dock, and the dragged widths come with it', function(wz)
  local api, file, facts = setup(wz)
  local _, tab = two_pane_window(wz, facts)
  -- the view is a pane of the tab beside the bar: chrome, never part of the saved grid
  tab:set_rows {
    { id = 9, left = 0, top = 0, width = 5, height = 50 },
    { id = 7, left = 6, top = 0, width = 30, height = 50 },
    { id = 1, left = 37, top = 0, width = 70, height = 50, active = true },
    { id = 2, left = 108, top = 0, width = 63, height = 50 },
    { id = 8, left = 172, top = 0, width = 29, height = 50 },
  }
  wz.GLOBAL.sidebar[tostring(tab:tab_id())].content = 7
  wz.GLOBAL.overview_off = { [tostring(tab:tab_id())] = true }
  wz.GLOBAL.sidebar_width, wz.GLOBAL.sidebar_width_of = 40, 200
  api.save(100)
  local doc = read_json(wz, file)
  T.eq(doc.windows[1].tabs[1].sidebar, 'git')
  T.eq(doc.windows[1].tabs[1].dock_off, true)
  T.eq(doc.chrome.sidebar_width, 40)
  T.eq(doc.chrome.sidebar_width_of, 200)
end)

T.test('save: a web pane keeps its page, an omp whose heartbeat is stale keeps its chat', function(wz)
  local api, file, facts = setup(wz)
  facts[1] = { proc = 'omp', path = '/w', session = { file = '/s/a.jsonl', id = 'a' } }
  wz.fake.grid({
    { id = 1, left = 0, top = 0, width = 100, height = 40, active = true },
    { pane = wz.fake.pane { id = 2, user_vars = { hivegrid_web = '1' }, cwd = wz.fake.url('https://example.com/x') },
      left = 101, top = 0, width = 99, height = 40 },
  })
  api.save(100)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.b, { kind = 'web', url = 'https://example.com/x' })
  facts[1] = { proc = 'omp', path = '/w' } -- no extension record this tick
  api.save(110)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.a.session, '/s/a.jsonl', 'the chat is not forgotten')
end)

T.test('save: nothing is written when nothing changed, and a change is written', function(wz)
  local api, file, facts = setup(wz)
  two_pane_window(wz, facts)
  api.save(100)
  local first = T.read(file)
  os.remove(file)
  api.save(103)
  T.eq(T.read(file), nil, 'same snapshot: no write')
  facts[2].path = '/work/c'
  api.save(106)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.b.cwd, '/work/c')
  T.ok(first ~= T.read(file))
end)

T.test('save: a snapshot with less in it (a dying system) waits before it replaces the last one', function(wz)
  local api, file, facts = setup(wz)
  two_pane_window(wz, facts)
  api.save(100)
  facts[1] = { proc = 'bash', path = '/work/a' } -- omp gone
  api.save(103)
  api.save(106)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.a.kind, 'omp', 'still the chat that was open')
  api.save(112)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.a.kind, 'shell', 'it held long enough: it is the state now')
end)

T.test('save: a shrink that reverses before the wait ends is forgotten', function(wz)
  local api, file, facts = setup(wz)
  two_pane_window(wz, facts)
  api.save(100)
  facts[1] = { proc = 'bash', path = '/work/a' }
  api.save(103)
  facts[1] = { proc = 'omp', path = '/work/a', profile = 'work', session = { id = 'sid', file = '/s/a.jsonl', profile = 'work' } }
  api.save(106)
  facts[1] = { proc = 'bash', path = '/work/a' }
  api.save(109)
  api.save(112) -- 3 s after this shrink started, not 9
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.a.kind, 'omp')
end)

T.test('save: the file of a Hivegrid that is still running is left alone', function(wz)
  local api, file, facts = setup(wz)
  two_pane_window(wz, facts)
  local p = io.popen([[sh -c 'bash -c "exec -a wezterm-gui-test sleep 30" >/dev/null 2>&1 & echo $!']])
  local pid = tonumber(p:read('a'))
  p:close()
  local boot = (T.read('/proc/sys/kernel/random/boot_id') or ''):gsub('%s+$', '')
  local theirs = wz.json_encode {
    version = 1, owner = { pid = pid, boot = boot },
    windows = { { cols = 80, rows = 24, tabs = { { layout = { kind = 'shell' } } } } },
  }
  T.write(file, theirs)
  local ok = pcall(function()
    -- wait until the process has exec'ed its new name
    for _ = 1, 50 do
      local c = T.read('/proc/' .. pid .. '/cmdline')
      if c and c:find('wezterm', 1, true) then break end
      os.execute('sleep 0.02')
    end
    api.save(100)
    T.eq(T.read(file), theirs)
  end)
  os.execute('kill ' .. pid .. ' 2>/dev/null')
  T.ok(ok, 'the other instance kept its file')
  api.save(104)
  T.ok(T.read(file) ~= theirs, 'once it is gone the file is ours')
end)

-- Validation ----------------------------------------------------------------------------------

T.test('clean_doc: only a known version, shaped like a snapshot, with safe strings', function(wz)
  local api = setup(wz)
  local clean = api.internals.clean_doc
  T.eq(clean(nil), nil)
  T.eq(clean { version = 2, windows = {} }, nil)
  T.eq(clean { version = 1, windows = {} }, nil, 'no window')
  local doc = clean {
    version = 1,
    windows = { {
      cols = 5000, rows = 24, tabs = { {
        title = 'a\nb',
        layout = { dir = 'right', frac = 7, a = {
          kind = 'omp', cwd = 'relative', profile = 'x; rm -rf ~', session = '/etc/passwd',
        }, b = { kind = 'omp', profile = 'work', session = '/h/s.jsonl', cwd = '/p' } },
      } },
    } },
  }
  local t = doc.windows[1].tabs[1]
  T.eq(doc.windows[1].cols, nil, 'an absurd size is dropped')
  T.eq(doc.windows[1].rows, 24)
  T.eq(t.title, nil, 'a title with a control character is dropped')
  T.eq(t.layout.frac, 0.98, 'a share is kept inside the window')
  T.eq(t.layout.a.cwd, nil, 'a relative directory is dropped')
  T.eq(t.layout.a.profile, nil, 'a profile that is not a name never reaches the shell')
  T.eq(t.layout.a.session, nil, 'a session must be an absolute .jsonl path')
  T.eq(t.layout.b.profile, 'work')
  T.eq(t.layout.b.session, '/h/s.jsonl')
end)

T.test('clean_doc: a layout nested without end or with a missing half is refused', function(wz)
  local api = setup(wz)
  local deep = { kind = 'shell' }
  for _ = 1, 60 do deep = { dir = 'right', frac = 0.5, a = { kind = 'shell' }, b = deep } end
  T.eq(api.internals.clean_doc { version = 1, windows = { { tabs = { { layout = deep } } } } }, nil)
  T.eq(api.internals.clean_doc { version = 1, windows = { { tabs = { { layout = { dir = 'right', frac = 0.5, a = { kind = 'shell' } } } } } } }, nil)
end)

T.test('omp_command: resumes the chat only when its file is still there', function(wz)
  local api = setup(wz)
  local cmd = api.internals.omp_command
  local file = T.write(T.tmpdir() .. "/it's.jsonl", '{}')
  T.eq(cmd({ profile = 'work', session = file }, function(p) return p == file end),
    "omp --profile=work --restore '" .. file:gsub("'", "'\\''") .. "'")
  T.eq(cmd({ session = file }, function() return false end), 'omp', 'a chat that was never written: a fresh omp')
  T.eq(cmd({ profile = 'work' }, function() return true end), 'omp --profile=work')
end)

-- Restoring -------------------------------------------------------------------------------------

local function write_doc(wz, file, doc)
  doc.version = 1
  doc.owner = doc.owner or { pid = 2 ^ 22 + 12345, boot = 'gone' }
  T.write(file, wz.json_encode(doc))
end

T.test('restore: windows, tabs, splits, chats and pages come back at their size', function(wz)
  local web = { enabled = true, has_chrome = true, normalize_url = function(u) return u end }
  local api, file, _, seen = setup(wz, { web = web })
  local dir, chat = T.tmpdir(), T.write(T.tmpdir() .. '/s.jsonl', '{}')
  write_doc(wz, file, {
    chrome = { sidebar_width = 44, sidebar_width_of = 200 },
    windows = { {
      cols = 200, rows = 50, tabs = {
        {
          title = 'main', active = true, sidebar = 'git', dock_off = true,
          layout = { dir = 'right', frac = 0.4975,
            a = { kind = 'omp', cwd = dir, profile = 'work', session = chat, active = true },
            b = { dir = 'bottom', frac = 0.5, a = { kind = 'shell', cwd = '/gone/for/good' },
              b = { kind = 'web', url = 'https://example.com/' } } },
        },
        { layout = { kind = 'shell', cwd = dir } },
      },
    } },
  })
  T.eq(api.restore(nil, 1000), true)
  local sp = seen.spawned
  T.eq(sp[1].width, 200)
  T.eq(sp[1].height, 50)
  T.eq(sp[1].cwd, dir, 'the first pane starts in its directory')
  T.eq(sp[2].cwd, dir, 'the second tab')
  local win = seen.window
  T.eq(#win.tabs, 2)
  local t1 = win.tabs[1]
  T.eq(t1:get_title(), 'main')
  local first = t1.rows[1].pane
  -- split 1: right, the second part is the saved share of 200 cells
  T.eq(first.splits[1].direction, 'Right')
  T.eq(first.splits[1].size, 99)
  T.eq(first.sent, { "omp --profile=work --restore '" .. chat .. "'\r" })
  local right = t1.rows[2].pane
  T.eq(right.splits[1].direction, 'Bottom')
  T.eq(right.splits[1].args, { 'hivegrid:web', 'https://example.com/' })
  T.eq(right.splits[1].set_environment_variables.HIVEGRID_WEB_PROFILE, 'main')
  T.eq(right.splits[1].cwd, nil)
  T.eq(wz.GLOBAL.sidebar_width, 44)
  T.eq(wz.GLOBAL.session_pending[tostring(t1:tab_id())], { view = 'git' })
  T.eq(wz.GLOBAL.overview_off[tostring(t1:tab_id())], true)
  T.eq(wz.stub.activations[#wz.stub.activations], first:pane_id(), 'the focus is where it was')
  T.ok(T.read(file:gsub('session%.json$', 'session.prev.json')) ~= nil, 'the file that was found is kept')
end)

T.test('restore: a shell where the directory is gone, a shell where the page cannot open', function(wz)
  local api, file, _, seen = setup(wz) -- no web pane support
  write_doc(wz, file, {
    windows = { { cols = 120, rows = 30, tabs = { {
      layout = { dir = 'right', frac = 0.5, a = { kind = 'shell', cwd = '/gone' }, b = { kind = 'web', url = 'https://x.example/' } },
    } } } },
  })
  T.eq(api.restore(nil, 1000), true)
  T.eq(seen.spawned[1].cwd, nil)
  local split = seen.window.tabs[1].rows[1].pane.splits[1]
  T.eq(split.args, nil, 'a page without the fork is a shell')
  T.eq(split.cwd, nil)
end)

T.test('restore: not with a command of the user, not while the owner runs, not without a file', function(wz)
  local api, file, _, seen = setup(wz)
  T.eq(api.restore(nil, 1000), false, 'no file')
  write_doc(wz, file, { windows = { { tabs = { { layout = { kind = 'shell' } } } } } })
  T.eq(api.restore({ args = { 'htop' } }, 1000), false, 'wezterm start -- htop')
  T.eq(#seen.spawned, 0)
  write_doc(wz, file, {
    owner = { pid = tonumber(T.read('/proc/self/stat'):match('^(%d+)')) + 0, boot = 'other-boot' },
    windows = { { tabs = { { layout = { kind = 'shell' } } } } },
  })
  T.eq(api.restore(nil, 1000), true, 'an owner from another boot is gone')
end)

T.test('restore: a damaged file falls back to the previous copy, then to a plain window', function(wz)
  local api, file, _, seen = setup(wz)
  T.write(file, '{ not json')
  T.write(file .. '.bak', wz.json_encode {
    version = 1, owner = { pid = 2 ^ 22 + 7, boot = 'x' },
    windows = { { cols = 90, rows = 20, tabs = { { layout = { kind = 'shell' } } } } },
  })
  T.eq(api.restore(nil, 1000), true)
  T.eq(seen.spawned[1].width, 90)
  T.write(file .. '.bak', '[]')
  seen.spawned = {}
  T.eq(api.restore(nil, 1000), false)
end)

T.test('gui-startup: restores, else opens the window WezTerm would have', function(wz)
  local api, file, _, seen = setup(wz)
  local start = wz.stub.handlers['gui-startup'][1]
  start({ args = { 'htop' } })
  T.eq(seen.spawned[1].args, { 'htop' }, 'the user own command is a plain window')
  seen.spawned = {}
  write_doc(wz, file, { windows = { { cols = 100, rows = 30, tabs = { { layout = { kind = 'shell' } } } } } })
  start(nil)
  T.eq(#seen.spawned, 1)
  T.eq(seen.spawned[1].width, 100)
  T.ok(api ~= nil)
end)

T.test('a restored tab shows its sidebar view once the tab has a bar, then forgets it', function(wz)
  local api, file, _, seen = setup(wz)
  write_doc(wz, file, {
    windows = { { cols = 100, rows = 30, tabs = { { sidebar = 'files', active = true, layout = { kind = 'shell' } } } } },
  })
  api.restore(nil, 1000)
  local win = seen.window
  local tab = win:active_tab()
  local tick = wz.stub.handlers['update-status'][1]
  tick(win)
  T.eq(seen.toggles, {}, 'no bar yet')
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 55 } }
  tick(win)
  T.eq(seen.toggles, { 'files' })
  tick(win)
  T.eq(seen.toggles, { 'files' }, 'once')
end)

T.test('a restored omp pane keeps its chat in the snapshot while omp starts', function(wz)
  local api, file, facts, seen = setup(wz)
  local chat = T.write(T.tmpdir() .. '/s.jsonl', '{}')
  write_doc(wz, file, {
    windows = { { cols = 100, rows = 30, tabs = { { layout = { kind = 'omp', cwd = T.tmpdir(), session = chat, profile = 'work' } } } } },
  })
  api.restore(nil, 1000)
  local pane = seen.window.tabs[1].rows[1].pane
  facts[pane:pane_id()] = { proc = 'bash', path = '/x' } -- the shell has not started omp yet
  api.save(1010)
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.session, chat)
  facts[pane:pane_id()] = { proc = 'bash', path = '/x' } -- two minutes on: omp did not come back
  api.save(1200)
  api.save(1210) -- a snapshot with less in it must hold for the shrink wait
  T.eq(read_json(wz, file).windows[1].tabs[1].layout.kind, 'shell')
end)

T.test('session.enabled = false in settings.json: nothing is saved or restored', function(wz)
  T.write(T.read and (os.getenv('HOME') .. '/.config/hivegrid/settings.json'), '{ "session": { "enabled": false } }')
  local api = setup(wz)
  T.eq(api.enabled, false)
  T.eq(wz.stub.handlers['gui-startup'], nil)
  T.eq(wz.stub.handlers['update-status'], nil)
end)
