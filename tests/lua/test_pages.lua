-- The page switcher and the Bots page (config/pages.lua) over the stub: the header group, opening the
-- page through `hivegrid-bots-page ensure` and the one-shot page it names, the fallback to the terminal
-- panel (stock WezTerm, no browser, a server that does not come up), going back to the canvas and leaving
-- the page with its tab.
local T = require 'tests.lua.t'

local T0 = 1700000000

local function plain(items)
  local out = {}
  for _, c in ipairs(T.render(items)) do out[#out + 1] = c.ch end
  return table.concat(out)
end

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

-- One window over a grid tab, the modules wired like config/wezterm.lua: opts.stock (stock WezTerm),
-- opts.no_chrome. `panels` records the fallbacks: { name, extra }.
local function setup(wz, opts)
  opts = opts or {}
  local rt = T.tmpdir()
  T.setenv('XDG_RUNTIME_DIR', rt)
  if opts.no_chrome then
    T.setenv('PATH', T.tmpdir())
    hide_system_chrome()
  else
    T.setenv('HIVEGRID_CHROME', T.write(T.tmpdir() .. '/chrome', 'x'))
  end
  T.clock(T0)
  wz.stub.fork = not opts.stock
  wz.stub.web = not opts.stock
  local config = {}
  local layouts = require('layouts')(config, wz)
  local sb = require 'sidebar'
  local web = require('web')(config, wz, sb, layouts)
  local panels = {}
  local pages = require('pages')(config, wz, {
    web = web,
    panel = function(_, _, name, extra) panels[#panels + 1] = { name, extra } end,
  })
  local win, tab = wz.fake.grid({ { id = 1, width = 200, height = 50 } })
  return pages, win, tab, panels, rt, web
end

-- Answers `hivegrid-bots-page ensure` the way the program does: the status file next to the runtime dir.
local function answer(wz, rt, doc)
  T.write(rt .. '/hivegrid-bots-page.json', wz.json_encode(doc))
end

local function overlay_of(win)
  local last
  for _, a in ipairs(win.actions) do
    if a.action.kind == 'ShowOverlayCommand' then last = a.action end
  end
  return last
end

T.test('the switcher: Canvas and Bots chips, the page in front is raised, the count shows on Bots', function(wz)
  local pages = setup(wz)
  local tiers = pages.tiers(2, 'canvas')
  T.eq(#tiers, 2)
  T.ok(plain(tiers[1].items):find('Canvas', 1, true) and plain(tiers[1].items):find('Bots ● 2', 1, true), plain(tiers[1].items))
  T.eq(plain(tiers[2].items), ' Canvas   Bots ')
  T.ok(tiers[2].w < tiers[1].w)
  local theme = require 'theme'
  local function bg_of(items, word)
    local cells = T.render(items)
    local text = plain(items)
    local at = text:find(word, 1, true)
    return cells[at].bg
  end
  T.eq(bg_of(tiers[2].items, 'Canvas'), theme.P.raised)
  T.eq(bg_of(tiers[2].items, 'Bots'), theme.P.pill)
  local on_bots = pages.tiers(0, 'bots')
  T.eq(#on_bots, 1, 'no count, one tier')
  T.eq(bg_of(on_bots[1].items, 'Bots'), theme.P.raised)
  T.eq(bg_of(on_bots[1].items, 'Canvas'), theme.P.pill)
  T.eq(pages.tiers(nil, nil)[1].w, on_bots[1].w)
end)

T.test('the chips are the click targets page:canvas and page:bots; stock WezTerm has no switcher', function(wz)
  local pages = setup(wz)
  local ids = {}
  for _, it in ipairs(pages.tiers(0, 'canvas')[1].items) do
    if type(it) == 'table' and it.Click then ids[#ids + 1] = it.Click end
  end
  T.eq(ids, { 'page:canvas', 'page:bots' })
  local wz2 = T.fresh()
  T.eq(setup(wz2, { stock = true }).tiers(3, 'canvas'), {}, 'a header chip that cannot be clicked would only mislead')
end)

T.test('stock WezTerm: the Bots page is the terminal panel, no web pane, no error', function(wz)
  local pages, win, _, panels = setup(wz, { stock = true })
  T.eq(pages.enabled, false)
  pages.show_bots(win, wz.mux.get_pane(1), { select = 'daily-news' })
  T.eq(panels, { { 'bots', { '--select', 'daily-news' } } })
  pages.show_bots(win, wz.mux.get_pane(1), { new = true })
  T.eq(panels[2], { 'bots', { '--new' } })
  T.eq(#wz.stub.background, 0, 'nothing was started')
  T.eq(overlay_of(win), nil)
  T.eq(win.toasts, {}, 'stock WezTerm is the normal case, not an error')
  pages.show_canvas(win, wz.mux.get_pane(1))
  pages.sync(win)
  T.eq(pages.current(win), 'canvas')
end)

T.test('no browser: the terminal panel and a toast that says why', function(wz)
  local pages, win, _, panels = setup(wz, { no_chrome = true })
  pages.show_bots(win, wz.mux.get_pane(1))
  T.eq(#panels, 1)
  T.ok(win.toasts[1].msg:find('Chrome', 1, true), win.toasts[1].msg)
  T.eq(#wz.stub.background, 0)
end)

T.test('opening: ensure runs in the background, the one-shot page opens as a web overlay in its own profile', function(wz)
  local pages, win, tab, _, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1), { select = 'daily-news' })
  T.eq(wz.stub.background[1], { wz.home_dir .. '/.local/bin/hivegrid-bots-page', 'ensure', '--route', '/bots/daily-news' })
  T.eq(overlay_of(win), nil, 'nothing opens before the server answered')
  answer(wz, rt, { ts = T0, ok = true, url = 'http://127.0.0.1:8787/', open = rt .. '/hivegrid-bots-open.html', state = 'up' })
  wz.stub.run_timers(1)
  local o = overlay_of(win)
  T.ok(o, 'the overlay opened')
  T.eq(o.args.args, { 'hivegrid:web', 'file://' .. rt .. '/hivegrid-bots-open.html' })
  T.eq(o.args.set_environment_variables, { HIVEGRID_WEB_PROFILE = 'hivegrid-bots' })
  -- the window now reports the web pane as active: that is the page
  local page = wz.fake.pane { id = 77, user_vars = { hivegrid_web = '1' }, cwd = wz.fake.url('http://127.0.0.1:8787/') }
  win.active_pane_override = page
  wz.stub.run_timers(1)
  T.eq(pages.current(win), 'bots')
  T.eq(page.sent[1], '\27_hgweb;header;hide\27\\', 'the page has no browser header')
  -- the one-shot page is removed after its life
  T.write(rt .. '/hivegrid-bots-open.html', 'x')
  T.clock(T0 + 25)
  wz.stub.run_timers()
  local f = io.open(rt .. '/hivegrid-bots-open.html', 'r')
  T.eq(f, nil, 'the file with the token is gone')
  T.ok(tab)
end)

T.test('the new-bot sheet and the plain page are routes of ensure', function(wz)
  local pages, win, _, _, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1), { new = true })
  T.eq(wz.stub.background[1], { wz.home_dir .. '/.local/bin/hivegrid-bots-page', 'ensure', '--route', '/bots', '--new' })
  pages.show_bots(win, wz.mux.get_pane(1), { select = '../x' })
  T.eq(wz.stub.background[2], nil, 'a second request while the first one waits is ignored')
  T.ok(rt)
end)

T.test('an id that is no bot id never reaches the command line', function(wz)
  local pages, win = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1), { select = 'a;rm -rf' })
  T.eq(wz.stub.background[1][4], '/bots')
end)

T.test('a server that cannot be started: the terminal panel and the reason', function(wz)
  local pages, win, _, panels, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1))
  answer(wz, rt, { ts = T0, ok = false, state = 'no_token', message = 'There is no access token for the local page yet. Create one with: hivegrid-remote token' })
  wz.stub.run_timers(1)
  T.eq(overlay_of(win), nil)
  T.eq(panels, { { 'bots', nil } })
  T.ok(win.toasts[1].msg:find('hivegrid-remote token', 1, true), win.toasts[1].msg)
  T.eq(pages.current(win), 'canvas')
end)

T.test('a stale answer from before the request is not taken, and silence ends in the panel after 15 s', function(wz)
  local pages, win, _, panels, rt = setup(wz)
  answer(wz, rt, { ts = T0 - 500, ok = true, url = 'http://127.0.0.1:8787/', open = rt .. '/x.html' })
  pages.show_bots(win, wz.mux.get_pane(1))
  wz.stub.run_timers(3)
  T.eq(overlay_of(win), nil, 'an old ok is not an answer')
  T.clock(T0 + 16)
  wz.stub.run_timers(1)
  T.eq(#panels, 1)
  T.ok(win.toasts[1].msg:find('did not answer', 1, true), win.toasts[1].msg)
end)

T.test('a bad file name from the status file is refused', function(wz)
  local pages, win, _, panels, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1))
  answer(wz, rt, { ts = T0, ok = true, url = 'http://127.0.0.1:8787/', open = 'relative.html' })
  wz.stub.run_timers(1)
  T.eq(overlay_of(win), nil)
  T.eq(#panels, 1)
end)

T.test('back to the canvas: the web pane of the page is killed through the cli', function(wz)
  local pages, win, tab, _, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1))
  answer(wz, rt, { ts = T0, ok = true, url = 'http://127.0.0.1:8787/', open = rt .. '/hivegrid-bots-open.html' })
  wz.stub.run_timers(1)
  win.active_pane_override = wz.fake.pane { id = 77, user_vars = { hivegrid_web = '1' } }
  wz.stub.run_timers(1)
  T.eq(pages.current(win), 'bots')
  local before = #wz.stub.background
  pages.show_canvas(win, wz.mux.get_pane(1))
  local argv = wz.stub.background[before + 1]
  T.eq({ argv[#argv - 3], argv[#argv - 2], argv[#argv - 1], argv[#argv] }, { 'cli', 'kill-pane', '--pane-id', '77' })
  T.eq(pages.current(win), 'canvas')
  T.ok(tab)
end)

T.test('another tab in front ends the page; the key toggles', function(wz)
  local pages, win, tab, _, rt = setup(wz)
  local other = wz.fake.tab { panes = { { id = 9, width = 80, height = 24 } } }
  other._window = win
  win.tabs[2] = other
  pages.show_bots(win, wz.mux.get_pane(1))
  answer(wz, rt, { ts = T0, ok = true, url = 'http://127.0.0.1:8787/', open = rt .. '/hivegrid-bots-open.html' })
  wz.stub.run_timers(1)
  win.active_pane_override = wz.fake.pane { id = 77, user_vars = { hivegrid_web = '1' } }
  wz.stub.run_timers(1)
  T.eq(pages.current(win), 'bots')
  win.active = 2
  win.active_pane_override = nil
  T.eq(pages.current(win), 'canvas', 'the page belongs to its tab')
  local before = #wz.stub.background
  pages.sync(win)
  T.ok(#wz.stub.background > before, 'the leftover page is closed')
  win.active = 1
  T.eq(pages.current(win), 'canvas')
  T.ok(tab)
end)

T.test('a bot picked while the page is open navigates it instead of opening a second page', function(wz)
  local pages, win, _, _, rt = setup(wz)
  pages.show_bots(win, wz.mux.get_pane(1))
  answer(wz, rt, { ts = T0, ok = true, url = 'http://127.0.0.1:8787/', open = rt .. '/hivegrid-bots-open.html' })
  wz.stub.run_timers(1)
  local page = wz.fake.pane { id = 77, user_vars = { hivegrid_web = '1' } }
  win.active_pane_override = page
  wz.stub.run_timers(1)
  local started = #wz.stub.background
  pages.show_bots(win, wz.mux.get_pane(1), { select = 'daily-news' })
  T.eq(#wz.stub.background, started, 'nothing started')
  T.ok(table.concat(page.sent):find('navigate;http://127.0.0.1:8787/#/bots/daily-news', 1, true), table.concat(page.sent))
end)
