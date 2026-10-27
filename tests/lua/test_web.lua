-- Web pane (config/web.lua and its hooks): URL normalisation, opening in the tab's web pane,
-- the open-uri link routing, the key table, the grid/overview/broadcast/picker behaviour of
-- web panes, the settings keys, and that stock WezTerm gets nothing registered.
local T = require 'tests.lua.t'

local APC = '\27_hgweb;'

-- The real /opt/google/chrome/chrome (when this machine has one) is invisible to the config.
local real_open, hidden = io.open, false
local function hide_system_chrome()
  if hidden then return end
  hidden = true
  io.open = function(path, ...)
    if path == '/opt/google/chrome/chrome' then return nil, 'hidden' end
    return real_open(path, ...)
  end
end

-- A tab of `rows` (fake.tab panes) in a window; returns config, window, tab and the modules
-- wired the way config/wezterm.lua wires them: the Hivegrid fork with the web pane, unless
-- opts.stock (stock WezTerm) or opts.no_web (a fork whose cli has no --url); a Chrome is found
-- (HIVEGRID_CHROME names a file) unless opts.no_chrome.
local function setup(wz, rows, opts)
  opts = opts or {}
  if opts.no_chrome then
    T.setenv('PATH', T.tmpdir())
    hide_system_chrome()
  else
    T.setenv('HIVEGRID_CHROME', T.write(T.tmpdir() .. '/chrome', 'x'))
  end
  wz.stub.fork = not opts.stock
  wz.stub.web = not opts.stock and not opts.no_web
  local config = {}
  local layouts = require('layouts')(config, wz)
  local sb = require 'sidebar'
  local web = require('web')(config, wz, sb, layouts)
  local win, tab = wz.fake.grid(rows or { { id = 1, width = 200, height = 50 } })
  return config, win, tab, web, layouts
end

local function web_pane(wz, id, url)
  return wz.fake.pane { id = id, user_vars = { hivegrid_web = '1' }, title = 'Page ' .. id, cwd = wz.fake.url(url) }
end

local function sent(pane) return table.concat(pane.sent) end

-- URL normalisation ----------------------------------------------------------------------

-- The cases live in tests/data/web_urls.json, shared with tests/test_agents_web.py.
T.test('normalize_url: the shared cases (URLs, hosts, local hosts, searches, refusals)', function(wz)
  local web = select(4, setup(wz))
  local doc = wz.json_parse(T.read(T.root .. '/tests/data/web_urls.json'))
  T.ok(#doc.cases > 20, 'the fixture loaded')
  for _, c in ipairs(doc.cases) do
    local url, why = web.normalize_url(c.text, c.search and doc.search or nil)
    local label = string.format('%q%s', c.text, c.search and ' (with search)' or '')
    T.eq(url, c.url, label)
    if c.url == nil then T.ok(type(why) == 'string' and why ~= '', label .. ': a reason is given') end
  end
  T.eq(web.normalize_url('word', 'https://no-placeholder.example/'), nil, 'a template needs %s')
end)

-- Opening -------------------------------------------------------------------------------------

T.test('open splits right of a wide last grid pane, below a tall one, in reading order', function(wz)
  local _, win, tab, web = setup(wz, {
    { id = 1, left = 0, width = 100, height = 50 }, { id = 2, left = 101, width = 150, height = 40, active = true },
  })
  local ok, new = web.open(win, wz.mux.get_pane(1), 'https://example.com')
  T.ok(ok)
  T.eq(#wz.mux.get_pane(2).splits, 1, 'the last pane in reading order is split, not the pane asked from')
  T.eq(#wz.mux.get_pane(1).splits, 0)
  local req = wz.mux.get_pane(2).splits[1]
  T.eq(req.direction, 'Right', '150 >= 40 * 2.1')
  T.eq(req.args, { 'hivegrid:web', 'https://example.com' })
  T.eq(req.set_environment_variables, { HIVEGRID_WEB_PROFILE = 'main' })
  T.ok(web.is_web(new))
  T.eq(new.activated, 1, 'the new pane takes the focus')
  T.eq(#tab:panes(), 3)

  local _, win2, _, web2 = setup(wz, { { id = 3, width = 100, height = 50 } })
  web2.open(win2, wz.mux.get_pane(3), 'https://example.com')
  T.eq(wz.mux.get_pane(3).splits[1].direction, 'Bottom', '100 < 50 * 2.1')
  local _, win3, _, web3 = setup(wz, { { id = 4, width = 105, height = 50 } })
  web3.open(win3, wz.mux.get_pane(4), 'https://example.com')
  T.eq(wz.mux.get_pane(4).splits[1].direction, 'Right', 'exactly 2.1 cells wide per row: right')
end)

T.test('open skips the dock when it picks the pane to split', function(wz)
  local _, win, tab, web = setup(wz, { { id = 1, left = 31, width = 80, height = 20 }, { id = 99, left = 0, width = 30, height = 20 } })
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 99 } }
  web.open(win, wz.mux.get_pane(99), 'https://example.com')
  T.eq(#wz.mux.get_pane(1).splits, 1)
  T.eq(#wz.mux.get_pane(99).splits, 0)
end)

T.test('open reuses the tab\'s web pane: it navigates by the control sequence and takes the focus', function(wz)
  local _, win, tab, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  local ok, first = web.open(win, wz.mux.get_pane(1), 'https://example.com')
  T.ok(ok)
  local ok2, again = web.open(win, wz.mux.get_pane(1), 'https://other.test/x?y=1;z')
  T.ok(ok2)
  T.eq(again, nil, 'no new pane')
  T.eq(#wz.mux.get_pane(1).splits, 1)
  T.eq(sent(first), APC .. 'navigate;https://other.test/x?y=1;z\27\\')
  T.eq(first.activated, 2)
  T.eq(wz.GLOBAL.web.last[tostring(tab:tab_id())], first:pane_id())
end)

T.test('open reuses a web pane it did not create, and the remembered one first', function(wz)
  local a, b = web_pane(wz, 7, 'https://a.test/'), web_pane(wz, 8, 'https://b.test/')
  local _, win, tab, web = setup(wz, { { id = 1, width = 100, height = 50 }, { pane = a, left = 101, width = 50, height = 50 },
    { pane = b, left = 152, width = 50, height = 50 } })
  web.open(win, wz.mux.get_pane(1), 'https://c.test')
  T.eq(sent(a), APC .. 'navigate;https://c.test\27\\', 'the first web pane of the tab')
  wz.GLOBAL.web = { last = { [tostring(tab:tab_id())] = 8 } }
  web.open(win, wz.mux.get_pane(1), 'https://d.test')
  T.eq(sent(b), APC .. 'navigate;https://d.test\27\\', 'the remembered one wins')
  T.eq(#wz.mux.get_pane(1).splits + #a.splits + #b.splits, 0)
end)

T.test('a private or profile-named open is always a new pane with its own environment', function(wz)
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  web.open(win, wz.mux.get_pane(1), 'https://example.com')
  web.open(win, wz.mux.get_pane(1), 'https://example.com', { private = true })
  web.open(win, wz.mux.get_pane(1), 'https://example.com', { profile = 'work' })
  local envs = {}
  for _, p in ipairs(win:active_tab():panes()) do
    for _, s in ipairs(p.splits) do envs[#envs + 1] = s.set_environment_variables end
  end
  T.eq(envs, {
    { HIVEGRID_WEB_PROFILE = 'main' },
    { HIVEGRID_WEB_PROFILE = 'main', HIVEGRID_WEB_PRIVATE = '1' },
    { HIVEGRID_WEB_PROFILE = 'work' },
  })
end)

T.test('the default profile comes from the settings file', function(wz)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', '{"web": {"profile": "research"}}'))
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  web.open(win, wz.mux.get_pane(1), 'https://example.com')
  T.eq(wz.mux.get_pane(1).splits[1].set_environment_variables.HIVEGRID_WEB_PROFILE, 'research')
end)

T.test('open in a new tab spawns the sentinel program in it', function(wz)
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  T.ok(web.open(win, wz.mux.get_pane(1), 'https://example.com', { where = 'tab' }))
  local a = win.actions[#win.actions].action
  T.eq(a.kind, 'SpawnCommandInNewTab')
  T.eq(a.args.args, { 'hivegrid:web', 'https://example.com' })
  T.eq(a.args.set_environment_variables, { HIVEGRID_WEB_PROFILE = 'main' })
end)

T.test('open reports a failed split instead of raising', function(wz)
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  local pane = wz.mux.get_pane(1)
  pane.split = function() error('split refused') end
  local ok, why = web.open(win, pane, 'https://example.com')
  T.eq(ok, false)
  T.ok(tostring(why):find('split refused', 1, true))
end)

T.test('control characters never reach the control sequence', function(wz)
  local web = select(4, setup(wz))
  local pane = wz.fake.pane {}
  web.navigate(pane, 'https://a.test/\27\\x\n')
  T.eq(sent(pane), APC .. 'navigate;https://a.test/\\x\27\\')
  web.command(pane, 'zoom', 125)
  web.command(pane, 'reload')
  T.eq(pane.sent[2], APC .. 'zoom;125\27\\')
  T.eq(pane.sent[3], APC .. 'reload\27\\')
end)

-- Links -----------------------------------------------------------------------------------------

T.test('open-uri: an http(s) link of a terminal pane opens in a web pane and stops the default', function(wz)
  local _, win = setup(wz, { { id = 1, width = 200, height = 50 } })
  local pane = wz.mux.get_pane(1)
  T.eq(wz.emit('open-uri', win, pane, 'https://example.com/a'), false)
  T.eq(#pane.splits, 1)
  T.eq(pane.splits[1].args, { 'hivegrid:web', 'https://example.com/a' })
  T.eq(wz.emit('open-uri', win, pane, 'http://example.com/b'), false, 'http too')
  T.eq(#pane.splits, 1, 'the web pane is reused')
end)

T.test('without Chrome: links stay with the system browser and the keys explain', function(wz)
  local config, win, _, web = setup(wz, nil, { no_chrome = true })
  T.eq(web.has_chrome, false)
  local pane = wz.mux.get_pane(1)
  T.eq(wz.emit('open-uri', win, pane, 'https://example.com/a'), true, 'the default open is not stopped')
  T.eq(#pane.splits, 0)
  win.toasts = {}
  wz.stub.perform(wz.stub.key(config.key_tables.web, 'n'), win, pane)
  T.eq(#win.toasts, 1)
  T.eq(win.toasts[1].msg, 'Chrome or Chromium not found')
  T.eq(web.open(win, pane, 'https://example.com'), false)
  T.eq(#pane.splits, 0)
end)

T.test('Chrome is found by HIVEGRID_CHROME alone when set, else by name on PATH', function(wz)
  local bin = T.tmpdir()
  hide_system_chrome()
  T.setenv('PATH', bin)
  T.eq(select(4, setup(wz, nil, { no_chrome = true })).has_chrome, false)
  for _, name in ipairs { 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser' } do
    T.fresh()
    local w = require 'wezterm'
    local d = T.tmpdir()
    T.write(d .. '/' .. name, 'x')
    T.setenv('PATH', '/nonexistent:' .. d)
    w.stub.fork, w.stub.web = true, true
    T.eq(require('web')({}, w, require 'sidebar', require('layouts')({}, w)).has_chrome, true, name)
  end
  T.fresh()
  local w = require 'wezterm'
  local d = T.tmpdir()
  T.write(d .. '/chromium', 'x')
  T.setenv('PATH', d)
  T.setenv('HIVEGRID_CHROME', d .. '/missing')
  w.stub.fork, w.stub.web = true, true
  T.eq(require('web')({}, w, require 'sidebar', require('layouts')({}, w)).has_chrome, false, 'an explicit setting does not fall back')
end)

T.test('open-uri leaves other schemes, web panes, the dock and overlays to the default', function(wz)
  local _, win, tab = setup(wz, { { id = 1, width = 100, height = 50 }, { id = 99, left = 101, width = 30, height = 50 } })
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 99 } }
  local pane = wz.mux.get_pane(1)
  for _, uri in ipairs { 'file:///tmp/x', 'mailto:a@b.co', 'ftp://x.test/', 'ssh://host', 'wezterm-status-click:x', 'https:/', 'HTTPS://x.test' } do
    T.eq(wz.emit('open-uri', win, pane, uri), true, uri)
  end
  T.eq(wz.emit('open-uri', win, wz.mux.get_pane(99), 'https://x.test'), true, 'the dock')
  local overlay = wz.fake.pane { id = 500 } -- a panel or picker: no pane of the tab
  T.eq(wz.emit('open-uri', win, overlay, 'https://x.test'), true, 'an overlay')
  local page = web_pane(wz, 7, 'https://a.test/')
  win:active_tab().rows[#win:active_tab().rows + 1] = { pane = page, left = 0, top = 0, width = 10, height = 10 }
  T.eq(wz.emit('open-uri', win, page, 'https://x.test'), true, 'links inside a web pane stay in the browser')
  T.eq(#pane.splits + #page.sent, 0, 'nothing was opened')
end)

T.test('open-uri is left to the system browser with web.links = system', function(wz)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', '{"web": {"links": "system"}}'))
  local _, win = setup(wz, { { id = 1, width = 200, height = 50 } })
  T.eq(wz.emit('open-uri', win, wz.mux.get_pane(1), 'https://example.com'), true)
  T.eq(#wz.mux.get_pane(1).splits, 0)
end)

T.test('open-uri falls back to the default when the web pane cannot be opened', function(wz)
  local _, win = setup(wz, { { id = 1, width = 200, height = 50 } })
  wz.mux.get_pane(1).split = function() error('no') end
  T.eq(wz.emit('open-uri', win, wz.mux.get_pane(1), 'https://example.com'), true)
end)

-- Key table --------------------------------------------------------------------------------------

T.test('LEADER+w activates the web table (rebindable); its keys drive the tab\'s web pane', function(wz)
  local config, win = setup(wz, { { id = 1, width = 100, height = 50 }, { pane = web_pane(wz, 7, 'https://a.test/'), left = 101, width = 50, height = 50 } })
  local action = wz.stub.key(config.keys, 'w', 'LEADER')
  T.eq(action.kind, 'ActivateKeyTable')
  T.eq(action.args.name, 'web')
  T.ok(action.args.prevent_fallback)
  local term, page = wz.mux.get_pane(1), wz.mux.get_pane(7)
  local expect = { b = 'back', f = 'forward', r = 'reload', s = 'stop', y = 'copy-url' }
  for key, name in pairs(expect) do
    win:perform_action(action, term)
    wz.stub.perform(wz.stub.key(config.key_tables.web, key), win, term)
    T.eq(page.sent[#page.sent], APC .. name .. '\27\\', key)
    T.eq(win:active_key_table(), nil, 'a key leaves the table')
  end
  wz.stub.perform(wz.stub.key(config.key_tables.web, 'R', 'SHIFT'), win, term)
  T.eq(page.sent[#page.sent], APC .. 'hardreload\27\\')
  T.eq(wz.stub.key(config.key_tables.web, 'Escape').kind, 'PopKeyTable')
  T.eq(#term.sent, 0, 'the terminal pane is never written to')
end)

T.test('the web key table needs a web pane and says so', function(wz)
  local config, win = setup(wz, { { id = 1, width = 100, height = 50 } })
  wz.stub.perform(wz.stub.key(config.key_tables.web, 'b'), win, wz.mux.get_pane(1))
  T.eq(#win.toasts, 1)
  T.eq(#wz.mux.get_pane(1).sent, 0)
end)

T.test('o hands the page to the system browser; a pane without a page only toasts', function(wz)
  local config, win = setup(wz, { { pane = web_pane(wz, 7, 'https://a.test/x'), width = 100, height = 50 } })
  local o = wz.stub.key(config.key_tables.web, 'o')
  wz.stub.perform(o, win, wz.mux.get_pane(7))
  T.eq(wz.stub.opened, { 'https://a.test/x' })
  local blank = wz.fake.pane { id = 8, user_vars = { hivegrid_web = '1' } }
  wz.stub.perform(o, win, blank)
  T.eq(#wz.stub.opened, 1)
  T.eq(#win.toasts, 1)
end)

T.test('the web leader follows settings.json', function(wz)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', '{"keys": {"web_leader": "LEADER+x"}}'))
  local config = setup(wz)
  T.eq(wz.stub.key(config.keys, 'x', 'LEADER').args.name, 'web')
  T.eq(wz.stub.key(config.keys, 'w', 'LEADER'), nil)
end)

T.test('the location prompt navigates the web pane, or opens one when there is none', function(wz)
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  local pane = wz.mux.get_pane(1)
  web.prompt_location(win, pane)
  local prompt = win.actions[#win.actions].action
  T.eq(prompt.kind, 'PromptInputLine')
  wz.stub.perform(prompt.args.action, win, wz.fake.pane {}, 'example.com') -- the overlay's own pane
  T.eq(pane.splits[1].args, { 'hivegrid:web', 'https://example.com' }, 'none yet: a new pane')
  web.prompt_location(win, pane)
  local page = pane.splits[1] and win:active_tab():panes()[2]
  wz.stub.perform(win.actions[#win.actions].action.args.action, win, wz.fake.pane {}, 'localhost:3000')
  T.eq(sent(page), APC .. 'navigate;http://localhost:3000\27\\')
  local toasts = #win.toasts
  web.prompt_location(win, pane)
  wz.stub.perform(win.actions[#win.actions].action.args.action, win, wz.fake.pane {}, 'two words')
  T.eq(#win.toasts, toasts + 1, 'a location that is no URL is explained (no search engine)')
  web.prompt_location(win, pane)
  wz.stub.perform(win.actions[#win.actions].action.args.action, win, wz.fake.pane {}, nil)
  T.eq(#win.toasts, toasts + 1, 'Esc is silent')
end)

T.test('the new-pane prompt searches with the configured engine and always splits', function(wz)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', '{"web": {"search": "https://duck.example/?q=%s"}}'))
  local _, win, _, web = setup(wz, { { id = 1, width = 200, height = 50 } })
  local pane = wz.mux.get_pane(1)
  web.open(win, pane, 'https://first.test')
  web.prompt_new(win, pane)
  wz.stub.perform(win.actions[#win.actions].action.args.action, win, wz.fake.pane {}, 'rust ownership')
  local requests = {}
  for _, p in ipairs(win:active_tab():panes()) do
    for _, s in ipairs(p.splits) do requests[#requests + 1] = s.args[2] end
  end
  T.eq(requests, { 'https://first.test', 'https://duck.example/?q=rust%20ownership' })
end)

T.test('o opens http(s) pages only, with the port; a local file page only toasts', function(wz)
  local config, win = setup(wz, { { pane = web_pane(wz, 7, 'http://localhost:3000/a?b=1'), width = 100, height = 50 } })
  local o = wz.stub.key(config.key_tables.web, 'o')
  wz.stub.perform(o, win, wz.mux.get_pane(7))
  T.eq(wz.stub.opened, { 'http://localhost:3000/a?b=1' })
  local file = web_pane(wz, 8, 'file:///tmp/a.html')
  wz.stub.perform(o, win, file)
  T.eq(#wz.stub.opened, 1)
  T.eq(#win.toasts, 1)
end)

-- A process whose environment holds WEZTERM_UNIX_SOCKET=sock (a real one: the config reads
-- /proc/<pid>/environ); returns its pid. Killed by the cleanup of the caller.
local function process_with_socket(sock)
  local p = assert(io.popen('WEZTERM_UNIX_SOCKET=' .. sock .. ' sleep 30 >/dev/null 2>&1 & echo $!'))
  local pid = tonumber(p:read('l'))
  p:close()
  for _ = 1, 100 do
    local f = io.open('/proc/' .. pid .. '/environ', 'rb')
    local env = f and f:read('a') or ''
    if f then f:close() end
    if env:find('WEZTERM_UNIX_SOCKET', 1, true) then break end
    os.execute('sleep 0.02')
  end
  return pid
end

-- The `wezterm cli <verb>` command the config ran last, as argv.
local function cli_argv(wz, verb)
  local found
  for _, argv in ipairs(wz.stub.children) do
    for i = 1, #argv - 1 do
      if argv[i] == 'cli' and argv[i + 1] == verb then found = argv end
    end
  end
  return found
end

T.test('a web pane finds its mux through another pane of the tab (layout and sidebar commands)', function(wz)
  local pid = process_with_socket('/tmp/hivegrid-test.sock')
  local ok, err = pcall(function()
    local page = web_pane(wz, 7, 'https://a.test/')
    local other = web_pane(wz, 8, 'https://b.test/')
    local term = wz.fake.pane { id = 1, process_info = { pid = pid, name = 'bash' } }
    local _, win, _, _, layouts = setup(wz, {
      { pane = other, left = 0, width = 40 }, { pane = term, left = 41, width = 40 }, { pane = page, left = 82, width = 40, active = true },
    })
    local th = require 'theme'
    T.eq(th.mux_socket(term), '/tmp/hivegrid-test.sock')
    T.eq(th.mux_socket(page), '/tmp/hivegrid-test.sock', 'asks the terminal pane of the tab')
    T.eq(th.mux_socket(wz.fake.pane { id = 50 }), nil, 'a pane in no tab: nobody to ask')

    layouts.equalize(win, page)
    local list = cli_argv(wz, 'list')
    T.ok(list, 'equalize ran wezterm cli list')
    T.eq({ list[1], list[2] }, { 'env', 'WEZTERM_UNIX_SOCKET=/tmp/hivegrid-test.sock' })

    wz.stub.children = {}
    require('sidebar')({}, wz).make_cli(page)('list')
    T.eq(wz.stub.children[1][2], 'WEZTERM_UNIX_SOCKET=/tmp/hivegrid-test.sock')
  end)
  os.execute('kill ' .. pid .. ' 2>/dev/null')
  assert(ok, err)
end)

T.test('with no terminal pane to ask, the cli runs without a socket as before', function(wz)
  local page = web_pane(wz, 7, 'https://a.test/')
  local _, win, _, _, layouts = setup(wz, {
    { pane = page, left = 0, width = 40 }, { pane = web_pane(wz, 8, 'https://b.test/'), left = 41, width = 40 },
  })
  layouts.equalize(win, page)
  local list = cli_argv(wz, 'list')
  T.ok(list and list[1] ~= 'env', 'no env prefix')
end)

-- Web panes in the grid ------------------------------------------------------------------------

T.test('grid_of and the reading-order numbering include a web pane', function(wz)
  local _, _, tab, _, layouts = setup(wz, {
    { id = 1, left = 0, width = 40, height = 20 }, { pane = web_pane(wz, 7, 'https://a.test/'), left = 41, width = 40, height = 20 },
    { id = 2, left = 0, top = 21, width = 40, height = 20 },
  })
  local grid = layouts.grid_of(tab)
  T.eq(#grid, 3)
  local order = layouts.in_order(grid)
  T.eq({ order[1].pane:pane_id(), order[2].pane:pane_id(), order[3].pane:pane_id() }, { 1, 7, 2 })
end)

T.test('broadcast types into terminals only, never into a web pane', function(wz)
  local page = web_pane(wz, 7, 'https://a.test/')
  local config, win = setup(wz, { { id = 1, left = 0, width = 40 }, { pane = page, left = 41, width = 40 }, { id = 2, left = 82, width = 40 } })
  wz.GLOBAL.broadcast = { tab_id = win:active_tab():tab_id(), window_id = win:window_id() }
  local bt = config.key_tables.broadcast
  wz.stub.perform(wz.stub.key(bt, 'l', 'NONE'), win, win:active_pane())
  wz.stub.perform(wz.stub.key(bt, 'Enter', 'NONE'), win, win:active_pane())
  T.eq(sent(wz.mux.get_pane(1)), 'l')
  T.eq(sent(wz.mux.get_pane(2)), 'l')
  T.eq(#page.sent, 0)
  local keyed = {}
  win.actions = {}
  wz.stub.perform(wz.stub.key(bt, 'c', 'CTRL'), win, win:active_pane())
  for _, a in ipairs(win.actions) do keyed[#keyed + 1] = a.pane:pane_id() end
  T.eq(keyed, { 1, 2 }, 'keys are fanned out to terminals only')
end)

T.test('broadcast ends instead of fanning out when a web pane has the focus', function(wz)
  local page = web_pane(wz, 7, 'https://a.test/')
  local config, win = setup(wz, { { id = 1, left = 0, width = 40 }, { pane = page, left = 41, width = 40, active = true }, { id = 2, left = 82, width = 40 } })
  T.eq(win:active_pane():pane_id(), 7)
  local bt = config.key_tables.broadcast
  for _, k in ipairs { { 'p', 'NONE' }, { 'c', 'CTRL' }, { 'Enter', 'NONE' }, { 'v', 'CTRL' } } do
    wz.GLOBAL.broadcast = { tab_id = win:active_tab():tab_id(), window_id = win:window_id() }
    win.actions, win.toasts = {}, {}
    wz.stub.perform(wz.stub.key(bt, k[1], k[2]), win, page)
    T.eq(wz.GLOBAL.broadcast, nil, k[1] .. ': the mode is off')
    T.eq(#win.toasts, 1, k[1] .. ': the user is told')
    for _, id in ipairs { 1, 2 } do T.eq(#wz.mux.get_pane(id).sent, 0, k[1] .. ': nothing typed into a terminal') end
    for _, a in ipairs(win.actions) do T.ok(a.pane ~= wz.mux.get_pane(1) and a.pane ~= wz.mux.get_pane(2), 'no key fanned out') end
  end
  T.eq(#page.sent, 0)
end)

T.test('broadcast cannot be started from a web pane, and Ctrl+V from a terminal only explains', function(wz)
  local page = web_pane(wz, 7, 'https://a.test/')
  local config, win = setup(wz, { { id = 1, left = 0, width = 40, active = true }, { pane = page, left = 41, width = 40 } })
  wz.stub.perform(wz.stub.key(config.keys, 'b', 'LEADER'), win, page)
  local prompt = win.actions[#win.actions].action
  T.eq(prompt.kind, 'PromptInputLine')
  win.toasts = {}
  wz.stub.perform(prompt.args.action, win, wz.fake.pane {}, 'broadcast')
  T.eq(wz.GLOBAL.broadcast, nil, 'not started')
  T.eq(#win.toasts, 1)
  wz.GLOBAL.broadcast = { tab_id = win:active_tab():tab_id(), window_id = win:window_id() }
  win.toasts = {}
  wz.stub.perform(wz.stub.key(config.key_tables.broadcast, 'v', 'CTRL'), win, wz.mux.get_pane(1))
  T.ok(wz.GLOBAL.broadcast ~= nil, 'a terminal keeps the mode')
  T.eq(#win.toasts, 1)
end)

T.test('pickers name a web pane by its page host and path', function(wz)
  local config, win = setup(wz, {
    { id = 1, left = 0, width = 40 }, { pane = web_pane(wz, 7, 'https://docs.example.com/guide/intro'), left = 41, width = 40 },
    { pane = web_pane(wz, 8, 'https://example.org/'), left = 82, width = 40 },
  })
  wz.stub.perform(wz.stub.key(config.key_tables.pane, 'g'), win, wz.mux.get_pane(1))
  local rows = wz.stub.picker(win).spec.rows
  T.eq(#rows, 3)
  T.eq(rows[2].cols[1].text, 'docs.example.com/guide/intro')
  T.eq(rows[3].cols[1].text, 'example.org', 'a bare "/" is not shown')
end)

-- Overview rows and the tab/pane state -------------------------------------------------------------

T.test('agentstatus: a web pane is its own state, never working, done or bell', function(wz)
  wz.stub.fork = true
  T.clock(1000000)
  local as = require('agentstatus')({})
  local page = web_pane(wz, 7, 'https://docs.example.com/guide')
  page.o.title = 'Guide · Example'
  page.o.text = 'placeholder'
  local win, tab = wz.fake.grid({
    { pane = wz.fake.pane { id = 1, title = 'π > chat', process_name = '/usr/bin/omp', text = 'x' }, width = 40, height = 12 },
    { pane = page, left = 41, width = 40, height = 12 },
  }, { focused = false })
  local function row()
    for _, r in ipairs(as.overview(win, tab:tab_id())) do
      if r.id == 7 then return r end
    end
  end
  local r = row()
  T.eq({ r.kind, r.glyph, r.text, r.proc, r.cwd, r.name, r.title, r.num }, { 'web', '◍', 'web', 'web', 'docs.example.com', 'docs.example.com', 'Guide · Example', 2 })
  T.eq(r.color, require('theme').STATES.web.color)
  T.eq(as.pane_kind(7), 'web')
  page.o.text = 'the page redrew'
  T.clock(1000010)
  page.o.unseen = true
  T.eq(row().kind, 'web', 'a changing screen is no work')
  as.bell(win, page)
  T.clock(1000020)
  T.eq(row().kind, 'web', 'a bell is no bell')
  local entry = as.overview(win, tab:tab_id())
  T.eq(entry[1].kind, 'idle', 'the omp pane next to it is unaffected')
end)

T.test('agentstatus: a web pane shows in the picker like any pane', function(wz)
  wz.stub.fork = true
  T.clock(1000000)
  local as = require('agentstatus')({})
  local win = wz.fake.grid({
    { pane = wz.fake.pane { id = 1, title = 'π > chat', process_name = '/usr/bin/omp' }, width = 40, height = 12, active = true },
    { pane = web_pane(wz, 7, 'https://a.test/'), left = 41, width = 40, height = 12 },
  })
  wz.stub.perform(as.picker, win, wz.mux.get_pane(1))
  local rows = wz.stub.picker(win).spec.rows
  T.eq(#rows, 2)
  T.eq(rows[2].glyph, '◍')
  T.eq(rows[2].cols[2].text, 'web')
end)

T.test('state tables: web sits between exited and the plain-program states', function()
  local S = require('theme').STATES
  T.eq({ S.web.glyph, S.web.text, S.web.key, S.web.rank }, { '◍', 'web', 'cyan', 8 })
  T.ok(S.exited.rank < S.web.rank and S.web.rank < S.other.rank and S.other.rank < S.shell.rank)
  T.eq(S.web.color, require('theme').P.cyan)
end)

-- Theme helpers and capability ------------------------------------------------------------------

T.test('theme.web_url: Url objects (tostring only, no href), strings, ports and files', function(wz)
  wz.stub.fork = true
  local th = require 'theme'
  local u = th.web_url(wz.fake.url('https://a.test/x'))
  T.eq(u, { href = 'https://a.test/x', scheme = 'https', host = 'a.test', path = '/x' })
  T.eq(th.web_url(wz.fake.url('http://localhost:3000/a/b?q=1#f')),
    { href = 'http://localhost:3000/a/b?q=1#f', scheme = 'http', host = 'localhost:3000', path = '/a/b' })
  T.eq(th.web_url('https://u:p@b.test/y?q=1').host, 'b.test', 'user info is dropped')
  T.eq(th.web_url('data:text/html,hi').scheme, 'data')
  T.eq(th.web_label(th.web_url('data:text/html,hi')), 'data:text/html,hi')
  T.ok(th.web_label(th.web_url('data:text/html,' .. string.rep('x', 60))):len() < 60, 'long opaque URLs are cut')
  for _, nope in ipairs { 'file:///home/u', '/home/u', '', wz.fake.url('file:///x/y'), setmetatable({}, {}) } do
    T.eq(th.web_url(nope), nil, T.show(nope))
  end
  T.eq(th.web_url(nil), nil)
  T.eq(th.web_url(wz.fake.url('file:///x/y'), true).scheme, 'file', 'a web pane may show a local page')
end)

T.test('web_label keeps the port, so two dev servers differ; the path only on request', function(wz)
  wz.stub.fork = true
  local th = require 'theme'
  local a, b = th.web_url(wz.fake.url('http://localhost:3000/')), th.web_url(wz.fake.url('http://localhost:8080/'))
  T.eq({ th.web_label(a), th.web_label(b) }, { 'localhost:3000', 'localhost:8080' })
  local c = th.web_url(wz.fake.url('https://a.test/p/q'))
  T.eq({ th.web_label(c), th.web_label(c, true) }, { 'a.test', 'a.test/p/q' })
  T.eq(th.web_label(th.web_url(wz.fake.url('https://a.test/'), true), true), 'a.test')
end)

T.test('a fork without the web feature (no --url flag) shows no web UI', function(wz)
  wz.stub.fork = true
  wz.stub.web = false
  T.eq(require('theme').has_click, true)
  T.eq(require('theme').has_web, false)
  local config, _, _, web = setup(wz, nil, { no_web = true })
  T.eq(web.enabled, false)
  T.eq(config.key_tables.web, nil)
  T.eq(wz.stub.handlers['open-uri'], nil)
end)

T.test('has_web asks the wezterm cli once, and only on the fork', function(wz)
  wz.stub.fork = false
  wz.stub.web = true
  T.eq(require('theme').has_web, false)
  T.eq(wz.stub.probes, 0, 'stock WezTerm: nothing is asked')
  T.fresh()
  local w2 = require 'wezterm'
  w2.stub.fork = true
  w2.stub.web = true
  T.eq(require('theme').has_web, true)
  T.eq(w2.stub.probes, 1)
end)

T.test('has_web is cached in a file: no child process while the binaries are unchanged', function(wz)
  wz.stub.fork = true
  wz.stub.web = true
  local bin = T.tmpdir()
  T.write(bin .. '/wezterm', 'cli-v1')
  wz.executable_dir = bin
  T.eq(require('theme').has_web, true)
  T.eq(wz.stub.probes, 1)
  local cache = T.read(os.getenv('XDG_CACHE_HOME') .. '/hivegrid/web-probe')
  T.ok(cache and cache:find(bin .. '/wezterm', 1, true), 'the answer is kept under the cache dir, keyed by the executable')
  package.loaded['theme'] = nil -- the next config load
  wz.stub.web = false -- a probe now would say no
  T.eq(require('theme').has_web, true, 'the cached answer is used')
  T.eq(wz.stub.probes, 1, 'no child process for it')
end)

T.test('has_web asks again when the binary changed, and a failed probe is not cached', function(wz)
  wz.stub.fork = true
  wz.stub.web = false
  local bin, cache = T.tmpdir(), T.tmpdir()
  T.setenv('XDG_CACHE_HOME', cache)
  T.write(bin .. '/wezterm', 'old build')
  wz.executable_dir = bin
  T.eq(require('theme').has_web, false)
  T.eq(wz.stub.probes, 1)
  local function again(web_now)
    package.loaded['theme'] = nil
    wz.stub.web = web_now
    return require('theme').has_web
  end
  T.eq(again(true), false, 'unchanged binary: the cached "no" stands')
  T.eq(wz.stub.probes, 1)
  T.write(bin .. '/wezterm', 'a rebuilt, bigger binary')
  T.eq(again(true), true, 'a changed binary is probed again')
  T.eq(wz.stub.probes, 2)
  T.eq(again(false), true, 'and cached')
  T.eq(wz.stub.probes, 2)
end)

T.test('has_web is false on stock WezTerm and without any binary to key the cache on', function(wz)
  wz.stub.fork = true
  wz.stub.web = false
  local cache = T.tmpdir()
  T.setenv('XDG_CACHE_HOME', cache)
  T.eq(require('theme').has_web, false)
  T.eq(T.read(cache .. '/hivegrid/web-probe'), nil, 'no executable found: nothing cached')
end)

T.test('stock WezTerm: nothing is registered and nothing raises', function(wz)
  local config, win, _, web, layouts = setup(wz, { { id = 1, width = 200, height = 50 } }, { stock = true })
  T.eq(web.enabled, false)
  T.eq(config.key_tables.web, nil)
  T.eq(wz.stub.key(config.keys, 'w', 'LEADER'), nil)
  T.eq(wz.stub.handlers['open-uri'], nil)
  local pane = wz.mux.get_pane(1)
  T.eq(wz.emit('open-uri', win, pane, 'https://example.com'), true)
  T.eq(web.open(win, pane, 'https://example.com'), false)
  T.eq(web.navigate(pane, 'https://example.com'), false)
  T.eq(web.command(pane, 'reload'), false)
  web.prompt_new(win, pane)
  web.prompt_location(win, pane)
  T.eq(#win.actions, 0)
  T.eq(#pane.splits + #pane.sent, 0)
  T.eq(web.normalize_url('example.com'), 'https://example.com', 'the pure helper still works')
  T.eq(#layouts.grid_of(win:active_tab()), 1)
end)

-- The main config ------------------------------------------------------------------------------------

local function main_config(wz, fork)
  wz.stub.fork = fork
  wz.stub.web = fork
  local config = dofile(T.root .. '/config/wezterm.lua')
  local win = wz.fake.grid { { id = 1, width = 100, height = 40 } }
  return config, win
end

local function menu_rows(wz, win)
  wz.emit('new-tab-button-click', win, win:active_pane(), 'Right')
  local out = {}
  for _, r in ipairs(wz.stub.picker(win).spec.rows) do out[#out + 1] = { r.cols[1].text, r.meta } end
  return out
end

T.test('the main config on the fork: web table, header pill, link handler and "+" menu rows', function(wz)
  local config, win = main_config(wz, true)
  T.ok(config.key_tables.web)
  T.eq(wz.stub.key(config.keys, 'w', 'LEADER').args.name, 'web')
  T.eq(#wz.stub.handlers['open-uri'], 1)
  local rows, web_rows = menu_rows(wz, win), {}
  for _, r in ipairs(rows) do
    if r[1]:lower():find('web', 1, true) then web_rows[#web_rows + 1] = r end
  end
  T.eq(#web_rows, 2, 'a new web tab and a web pane')
  T.eq(rows[1], { 'New tab with omp', 'LEADER+1' }, 'the omp rows keep their digits')
  T.eq(rows[3], { 'New tab with an omp grid 3x3', 'LEADER+3' })
  win.key_table = 'web'
  wz.emit('update-status', win, win:active_pane())
  T.ok(win.status.right:find('WEB', 1, true), 'a pill shows while the web table is active')
end)

T.test('the main config on stock WezTerm: no web rows, table, handler or pill', function(wz)
  local config, win = main_config(wz, false)
  T.eq(config.key_tables.web, nil)
  T.eq(wz.stub.handlers['open-uri'], nil)
  for _, r in ipairs(menu_rows(wz, win)) do
    T.ok(not r[1]:find('web', 1, true), r[1])
  end
  win.key_table = 'web'
  wz.emit('update-status', win, win:active_pane())
  T.ok(not win.status.right:find('WEB', 1, true))
end)

-- Settings -----------------------------------------------------------------------------------------

local function web_settings(wz, doc)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', type(doc) == 'string' and doc or wz.json_encode(doc)))
  return require('settings')(nil, wz).web
end

T.test('S.web: defaults, valid values, and invalid ones falling back', function(wz)
  T.eq(web_settings(wz, {}), { links = 'pane', search = '', profile = 'main' })
  T.fresh()
  local w2 = require 'wezterm'
  T.eq(web_settings(w2, { web = { links = 'system', search = 'https://x.test/?q=%s', profile = 'work-2' } }),
    { links = 'system', search = 'https://x.test/?q=%s', profile = 'work-2' })
  T.fresh()
  local w3 = require 'wezterm'
  T.eq(web_settings(w3, { web = { links = 'both', search = 'https://x.test/', profile = '../evil' } }),
    { links = 'pane', search = '', profile = 'main' })
  T.fresh()
  local w4 = require 'wezterm'
  T.eq(web_settings(w4, { web = { profile = string.rep('a', 33) } }).profile, 'main', 'at most 32 characters')
  T.fresh()
  local w5 = require 'wezterm'
  T.eq(web_settings(w5, { web = 'text' }), { links = 'pane', search = '', profile = 'main' })
  T.fresh()
  local w6 = require 'wezterm'
  T.eq(web_settings(w6, { web = { search = 'javascript:alert(%s)' } }).search, '', 'a search template must be http(s)')
end)
