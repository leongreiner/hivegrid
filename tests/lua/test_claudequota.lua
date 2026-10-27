-- claudequota: cache files -> header chip (accounts in settings order, login / stale / no
-- data), the refresh pacing, and starting or switching omp for an account.
local T = require 'tests.lua.t'

local T0 = 1800000000
local ACCOUNTS = {
  { label = 'main', dir = '~/.claude', profile = 'alpha' },
  { label = '2', dir = '~/.claude-2', profile = 'beta' },
  { label = 'private', dir = '~/.claude-private', profile = 'private' },
}

-- Builds the module over a temp cache dir. opts.accounts (default ACCOUNTS), opts.files
-- { name = doc table | raw text }, opts.creds { dir names under HOME }, opts.no_cache_dir.
local function setup(wz, opts)
  opts = opts or {}
  T.clock(T0)
  wz.stub.fork = true -- clickable regions, so each account's part can be read on its own
  local home = os.getenv('HOME')
  local cache = T.tmpdir() .. '/quota'
  if not opts.no_cache_dir then os.execute('mkdir -p ' .. T.sh_quote(cache)) end
  T.setenv('CLAUDE_QUOTA_CACHE_DIR', cache)
  T.setenv('CLAUDE_QUOTA_BIN', '/fake/bin/claude-quota')
  T.setenv('OMP_SWITCH_BIN', '/fake/bin/omp-switch')
  local settings = T.tmpdir() .. '/settings.json'
  T.setenv('HIVEGRID_SETTINGS', settings)
  T.write(settings, wz.json_encode { accounts = opts.accounts or ACCOUNTS })
  for _, d in ipairs(opts.creds or {}) do T.write(home .. '/' .. d .. '/.credentials.json', '{}') end
  for name, doc in pairs(opts.files or {}) do
    T.write(cache .. '/' .. name .. '.json', type(doc) == 'string' and doc or wz.json_encode(doc))
  end
  local config = {}
  local cq = require('claudequota')(config, wz)
  return cq, config, cache
end

local function ok_entry(label, five, seven, o)
  o = o or {}
  local e = { label = label, state = o.state or 'ok', updated_at = o.updated_at or T0 - 30 }
  if five then e.five_hour = { pct = five, reset_at = o.reset5 or T0 + 3600 } end
  if seven then e.seven_day = { pct = seven, reset_at = o.reset7 or T0 + 86400 } end
  return e
end

-- Text inside each clickable region of a tier: { ['quota:main'] = 'main 5h 30% ...', ... }
-- plus the order the regions appear in.
local function regions(wz, items)
  local out, order, cur, buf = {}, {}, nil, {}
  for _, it in ipairs(items) do
    if type(it) == 'table' and it.Click then
      if cur ~= it.Click then
        cur, buf = it.Click, {}
        if not out[cur] then order[#order + 1] = cur end
      end
    elseif it == 'ClickEnd' then
      out[cur] = (out[cur] or '') .. table.concat(buf)
      cur, buf = nil, {}
    elseif cur and type(it) == 'table' and it.Text then
      buf[#buf + 1] = it.Text
    end
  end
  return out, order
end

local function key(config, k, mods)
  for _, b in ipairs(config.keys) do
    if b.key == k and b.mods == mods then return b.action end
  end
  error('no binding ' .. tostring(mods) .. '+' .. k)
end

-- Chip -----------------------------------------------------------------------------------

T.test('chip names the most used account by its worst window; pills follow the settings order', function(wz)
  local cq = setup(wz, { files = {
    private = ok_entry('private', 10, 20),
    main = ok_entry('main', 30, 85),
    ['2'] = ok_entry('2', 60, nil),
  } })
  T.eq(cq.text(), '▮▮▮ main 7d 85%')
  local tiers = cq.tiers()
  T.eq(#tiers, 5)
  for i, tier in ipairs(tiers) do
    local _, order = regions(wz, tier.items)
    if i < 5 then T.eq(order, { 'quota:main', 'quota:2', 'quota:private' }, 'tier ' .. i) end
    T.eq(tier.w, require('theme').width(tier.items), 'tier ' .. i .. ' width')
    if i > 1 then T.ok(tier.w <= tiers[i - 1].w, 'tiers get narrower: ' .. i) end
  end
  local r = regions(wz, tiers[1].items)
  T.ok(r['quota:main']:find('5h 30%', 1, true) and r['quota:main']:find('7d 85%', 1, true), r['quota:main'])
  T.ok(r['quota:2']:find('5h 60%', 1, true) and not r['quota:2']:find('7d', 1, true), r['quota:2'])
end)

T.test('cache files of accounts not in the list are ignored and malformed ones skipped', function(wz)
  local cq = setup(wz, { files = {
    gone = ok_entry('gone', 99, 99),
    main = '{"label": "main", "five_hour": ',
    ['2'] = ok_entry('2', 40, 10),
  } })
  T.eq(cq.text(), '▮ 2 5h 40%')
  local r, order = regions(wz, cq.tiers()[1].items)
  T.eq(order, { 'quota:2' })
  T.eq(r['quota:gone'], nil)
end)

T.test('the cache file label wins over its file name, a missing label falls back to it', function(wz)
  local cq = setup(wz, { files = {
    ['acct-a'] = ok_entry('private', 70, nil),
    ['2'] = { state = 'ok', updated_at = T0, five_hour = { pct = 5, reset_at = T0 + 60 } },
  } })
  local _, order = regions(wz, cq.tiers()[1].items)
  T.eq(order, { 'quota:2', 'quota:private' })
end)

T.test('an account with credentials but no cached numbers yet shows --; without credentials it is left out', function(wz)
  local cq = setup(wz, { creds = { '.claude-private' }, files = { ['2'] = ok_entry('2', 20, 30) } })
  local r, order = regions(wz, cq.tiers()[1].items)
  T.eq(order, { 'quota:2', 'quota:private' })
  T.ok(r['quota:private']:find('--', 1, true), r['quota:private'])
  T.ok(not r['quota:private']:find('%', 1, true), r['quota:private'])
end)

T.test('an account that needs a login shows only the login chip, never stale numbers', function(wz)
  local cq = setup(wz, { files = {
    main = ok_entry('main', 50, 97, { state = 'login', updated_at = T0 - 7200 }),
    ['2'] = ok_entry('2', 20, 30),
  } })
  for i, tier in ipairs(cq.tiers()) do
    local r = regions(wz, tier.items)
    local m = r['quota:main']
    if m then
      T.ok(m:find('login', 1, true), 'tier ' .. i .. ': ' .. m)
      T.ok(not m:find('%', 1, true) and not m:find('~', 1, true), 'tier ' .. i .. ' shows numbers: ' .. m)
    end
  end
  local all = wz.format(cq.tiers()[1].items)
  T.ok(not all:find('97', 1, true), 'the login account numbers are hidden: ' .. all)
end)

T.test('with no numbers anywhere, an account needing a login is spelled out before one with no data', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1], ACCOUNTS[2] }, creds = { '.claude' },
    files = { ['2'] = { label = '2', state = 'login' } } })
  T.eq(cq.text(), '▮▮ 2 login')
  local tiers = cq.tiers()
  local dots = wz.format(tiers[#tiers].items)
  T.ok(dots:find('2 login', 1, true), 'narrowest tier: ' .. dots)
end)

T.test('numbers older than 3 minutes are marked stale', function(wz)
  local cq = setup(wz, { files = {
    main = ok_entry('main', 40, nil, { updated_at = T0 - 181 }),
    private = ok_entry('private', 20, nil, { updated_at = T0 - 170 }),
  } })
  T.eq(cq.text(), '▮▮ main 5h 40%~')
  local r = regions(wz, cq.tiers()[1].items)
  T.ok(r['quota:main']:find('~', 1, true), r['quota:main'])
  T.ok(not r['quota:private']:find('~', 1, true), r['quota:private'])
end)

T.test('a window whose reset has passed reads 0% and is not stale', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1] }, files = {
    main = ok_entry('main', 90, 30, { reset5 = T0 - 1 }),
  } })
  T.eq(cq.text(), '▮ main 7d 30%')
  local r = regions(wz, cq.tiers()[1].items)
  T.ok(r['quota:main']:find('5h 0%', 1, true), r['quota:main'])
  T.ok(not r['quota:main']:find('~', 1, true), r['quota:main'])
end)

T.test('an expired token keeps the numbers, marked stale, instead of a login chip', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1] }, files = {
    main = ok_entry('main', 40, 7, { state = 'expired', updated_at = T0 - 7200 }),
  } })
  T.eq(cq.text(), '▮ main 5h 40%~')
  local all = wz.format(cq.tiers()[1].items)
  T.ok(not all:find('login', 1, true), all)
  local bare = setup(wz, { accounts = { ACCOUNTS[1] }, files = { main = { label = 'main', state = 'expired' } } })
  T.eq(bare.text(), '▮ main login')
end)

T.test('a failed refresh keeps the last numbers but marks them stale', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1] }, files = {
    main = ok_entry('main', 40, nil, { state = 'error', updated_at = T0 - 10 }),
  } })
  T.eq(cq.text(), '▮ main 5h 40%~')
end)

T.test('no cache directory and no credentials: no chip at all', function(wz)
  local cq = setup(wz, { no_cache_dir = true })
  T.eq(cq.text(), '')
  T.eq(cq.tiers(), {})
end)

T.test('a usage at or above 80% shows the reset countdown', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1] }, files = {
    main = ok_entry('main', 80, 79, { reset5 = T0 + 2 * 3600 + 5 * 60 + 30, reset7 = T0 + 3 * 86400 }),
  } })
  local m = regions(wz, cq.tiers()[1].items)['quota:main']
  T.ok(m:find('↻2h05m', 1, true), m)
  T.ok(not m:find('↻3d', 1, true), 'no countdown below 80%: ' .. m)
end)

-- Colour of the first cell of `needle` in a tier's rendered text.
local function fg_of(items, needle)
  local cells, chars = require('tests.lua.t').render(items), {}
  for i, c in ipairs(cells) do chars[i] = c.ch end
  local text = table.concat(chars)
  local at = text:find(needle, 1, true)
  T.ok(at, needle .. ' in ' .. text)
  -- the text is ASCII up to the needle except for the cap glyph of a highlighted account
  local bytes = 0
  for i, c in ipairs(cells) do
    if bytes >= at - 1 then return c.fg end
    bytes = bytes + #chars[i]
  end
end

T.test('the header colours only past a threshold: muted numbers below 50%, amber from 50, red from 80', function(wz)
  local cq = setup(wz, { files = {
    main = ok_entry('main', 20, 55), ['2'] = ok_entry('2', 85, 49), private = ok_entry('private', 0, 0),
  } })
  local P = require('theme').P
  local items = cq.tiers()[2].items -- the richest tier without bars
  T.eq(fg_of(items, '20%'), P.muted, 'below 50')
  T.eq(fg_of(items, '55%'), P.amber)
  T.eq(fg_of(items, '85%'), P.red)
  T.eq(fg_of(items, '49%'), P.muted)
  T.eq(fg_of(items, 'main'), P.dim, 'labels are dim')
  for _, tier in ipairs(cq.tiers()) do
    for _, it in ipairs(tier.items) do
      local c = type(it) == 'table' and it.Foreground and it.Foreground.Color
      T.ok(c ~= P.green, 'green never shows in the header')
    end
  end
end)

T.test('the quota sits on the plain header band: no pill background anywhere', function(wz)
  local cq = setup(wz, { accounts = { ACCOUNTS[1] }, files = { main = ok_entry('main', 20, 30) } })
  local P = require('theme').P
  local items = cq.tiers()[1].items
  for _, it in ipairs(items) do
    T.ok(not (type(it) == 'table' and it.Background and it.Background.Color == P.pill), 'no pill background')
  end
end)

T.test('updated cache numbers show within the re-read interval, not on every tick', function(wz)
  local cq, _, cache = setup(wz, { accounts = { ACCOUNTS[1] }, files = { main = ok_entry('main', 10, nil) } })
  T.eq(cq.text(), '▮ main 5h 10%')
  T.write(cache .. '/main.json', wz.json_encode(ok_entry('main', 55, nil)))
  T.clock(T0 + 1)
  T.eq(cq.text(), '▮ main 5h 10%', 'cached for a few seconds')
  T.clock(T0 + 6)
  T.eq(cq.text(), '▮ main 5h 55%')
end)

-- Refresh pacing and visibility ------------------------------------------------------------

T.test('refresh spawns the helper at most once a minute and not while the chip is hidden', function(wz)
  local cq, config = setup(wz)
  local win = wz.fake.grid { { id = 1 } }
  cq.refresh_if_due(win)
  T.eq(wz.stub.background, { { '/fake/bin/claude-quota', 'refresh' } })
  T.clock(T0 + 59)
  cq.refresh_if_due(win)
  T.eq(#wz.stub.background, 1, 'within a minute')
  T.clock(T0 + 60)
  cq.refresh_if_due(win)
  T.eq(#wz.stub.background, 2)
  -- LEADER+q hides the chip: no more spawning until it is shown again.
  local toggle = key(config, 'q', 'LEADER')
  wz.stub.perform(toggle, win, wz.mux.get_pane(1))
  T.eq(cq.visible(), false)
  T.clock(T0 + 200)
  cq.refresh_if_due(win)
  T.eq(#wz.stub.background, 2)
  wz.stub.perform(toggle, win, wz.mux.get_pane(1))
  cq.refresh_if_due(win)
  T.eq(#wz.stub.background, 3)
end)

T.test('a new account directory reloads the config once, not on every tick', function(wz)
  local cq = setup(wz)
  local win = wz.fake.grid { { id = 1 } }
  cq.refresh_if_due(win)
  T.eq(wz.stub.reloads, 0)
  T.write(os.getenv('HOME') .. '/.claude-work/.credentials.json', '{}')
  T.clock(T0 + 11) -- past the settings rescan interval
  cq.refresh_if_due(win)
  T.eq(wz.stub.reloads, 1)
  T.clock(T0 + 12)
  cq.refresh_if_due(win)
  T.eq(wz.stub.reloads, 1, 'no reload storm while the new config loads')
end)

-- Starting omp ------------------------------------------------------------------------------

local function shell_pane(wz, id, o)
  o = o or {}
  o.id = id
  o.process_info = o.process_info or { name = 'bash', argv = { '-bash' }, pid = 100 + id }
  return wz.fake.pane(o)
end

local function omp_info(pid, profile)
  return { name = 'omp', argv = { 'omp', '--profile=' .. profile }, pid = pid }
end

T.test('Alt+q lists the accounts and picking one starts omp with its profile in a shell', function(wz)
  local _, config = setup(wz, { files = { main = ok_entry('main', 10, 20), ['2'] = ok_entry('2', 50, 60) } })
  local p = shell_pane(wz, 1)
  local win = wz.fake.grid { { pane = p } }
  wz.stub.perform(key(config, 'q', 'ALT'), win, p)
  local pick = wz.stub.picker(win)
  local ids, keys = {}, {}
  for i, r in ipairs(pick.spec.rows) do ids[i], keys[i] = r.id, r.key end
  T.eq(ids, { 'main', '2' })
  T.eq(keys, { '1', '2' }, 'each row carries its LEADER+u digit')
  pick.answer(nil)
  T.eq(p.sent, {}, 'Esc starts nothing')
  wz.stub.perform(key(config, 'q', 'ALT'), win, p)
  wz.stub.picker(win).answer('2')
  T.eq(p.sent, { 'omp --profile=beta\r' })
end)

T.test('LEADER+u then a digit starts omp for that account in list order', function(wz)
  local _, config = setup(wz)
  local p = shell_pane(wz, 1)
  local win = wz.fake.grid { { pane = p } }
  local digits = {}
  for _, k in ipairs(config.key_tables.omp_account) do digits[#digits + 1] = k.key end
  T.eq(digits, { '1', '2', '3', 'Escape' })
  wz.stub.perform(config.key_tables.omp_account[3].action, win, p)
  T.eq(p.sent, { 'omp --profile=private\r' })
end)

T.test('starting omp refuses a pane running another program or omp with that account already', function(wz)
  local cq = setup(wz)
  local vim = shell_pane(wz, 1, { process_info = { name = 'vim', argv = { 'vim' }, pid = 9 } })
  local same = shell_pane(wz, 2, { process_info = omp_info(10, 'alpha') })
  local win = wz.fake.grid { { pane = vim, active = true }, { pane = same, left = 41 } }
  cq.start_omp(win, vim, 'main')
  cq.start_omp(win, same, 'main')
  T.eq({ vim.sent, same.sent }, { {}, {} })
  T.eq(#win.toasts, 2)
  T.eq(#wz.stub.children, 0, 'omp-switch never ran')
end)

T.test('an account without a paired profile starts a bare omp', function(wz)
  local cq = setup(wz, { accounts = { { label = 'work', dir = '~/.claude-work' } } })
  local p = shell_pane(wz, 1)
  local win = wz.fake.grid { { pane = p } }
  cq.start_omp(win, p, 'work')
  T.eq(p.sent, { 'omp\r' })
  T.eq(#win.toasts, 0)
end)

T.test('a running omp is not moved to an account without a profile', function(wz)
  local cq = setup(wz, { accounts = { { label = 'work', dir = '~/.claude-work' } } })
  local p = wz.fake.pane { id = 1, process_info = omp_info(11, 'alpha') }
  local win = wz.fake.grid { { pane = p } }
  cq.start_omp(win, p, 'work')
  T.eq(p.sent, {})
  T.eq(#win.toasts, 1)
  T.eq(#wz.stub.children, 0, 'omp-switch never ran')
end)

T.test('from the sidebar the account starts in the grid pane used last', function(wz)
  local cq = setup(wz)
  local bar = shell_pane(wz, 1)
  local a, b = shell_pane(wz, 2), shell_pane(wz, 3)
  local win, tab = wz.fake.grid {
    { pane = bar, width = 5, active = true }, { pane = a, left = 6 }, { pane = b, left = 47 },
  }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 1, last = 3 } }
  cq.start_omp(win, bar, 'main')
  T.eq({ bar.sent, a.sent, b.sent }, { {}, {}, { 'omp --profile=alpha\r' } })
end)

-- Active account ----------------------------------------------------------------------------

local function profile_pane(wz, id, profile)
  return wz.fake.pane { id = id, process_info = omp_info(100 + id, profile) }
end

-- Labels whose clickable region carries the bold label of the highlighted account.
local function bold_labels(items)
  local out, cur = {}, nil
  for _, it in ipairs(items) do
    if type(it) == 'table' and it.Click then
      cur = it.Click:match('^quota:(.+)$')
    elseif it == 'ClickEnd' then
      cur = nil
    elseif cur and type(it) == 'table' and it.Attribute and it.Attribute.Intensity == 'Bold' then
      out[#out + 1] = cur
    end
  end
  return out
end

T.test('the account of the focused omp pane is highlighted and follows focus', function(wz)
  local cq = setup(wz, { files = {
    main = ok_entry('main', 90, 95), ['2'] = ok_entry('2', 10, 20), private = ok_entry('private', 30, 40),
  } })
  local alpha, priv = profile_pane(wz, 1, 'alpha'), profile_pane(wz, 2, 'private')
  local win = wz.fake.grid { { pane = alpha, active = true }, { pane = priv, left = 41 } }
  T.eq(cq.active(win), 'main')
  T.eq(bold_labels(cq.tiers(cq.active(win))[1].items), { 'main' })
  priv:activate()
  T.eq(cq.active(win), 'private')
  T.eq(bold_labels(cq.tiers(cq.active(win))[1].items), { 'private' }, 'the highlight moved with focus')
end)

T.test('high usage alone highlights nothing; a pane without omp highlights nothing', function(wz)
  local cq = setup(wz, { files = { main = ok_entry('main', 90, 95), ['2'] = ok_entry('2', 10, 20) } })
  local win = wz.fake.grid { { pane = shell_pane(wz, 1) } }
  T.eq(cq.active(win), nil)
  for i, tier in ipairs(cq.tiers(nil)) do
    if i < 5 then T.eq(bold_labels(tier.items), {}, 'tier ' .. i) end
  end
end)

T.test('from the sidebar the highlight follows the grid pane used last', function(wz)
  local cq = setup(wz)
  local bar = shell_pane(wz, 1)
  local a, b = profile_pane(wz, 2, 'alpha'), profile_pane(wz, 3, 'beta')
  local win, tab = wz.fake.grid {
    { pane = bar, width = 5, active = true }, { pane = a, left = 6 }, { pane = b, left = 47 },
  }
  wz.GLOBAL.sidebar = { [tostring(tab:tab_id())] = { handle = 1, last = 3 } }
  T.eq(cq.active(win), '2')
end)

-- Switching a running omp --------------------------------------------------------------------

-- An omp pane that hands the prompt back to bash when it receives Ctrl+D (unless `stuck`).
local function omp_pane(wz, id, profile, stuck)
  local p = wz.fake.pane { id = id, cwd = '/elsewhere', process_info = omp_info(500 + id, profile) }
  function p:send_text(s)
    self.sent[#self.sent + 1] = s
    if s == '\x04' and not stuck then self.o.process_info = { name = 'bash', argv = { 'bash' }, pid = 1 } end
  end
  return p
end

local function switch_script(wz, loc, moved)
  wz.stub.on_child = function(argv)
    if argv[2] == 'locate' then return true, wz.json_encode(loc) end
    if argv[2] == 'transfer' then return true, wz.json_encode(moved) end
    return false, ''
  end
end

T.test('a busy omp is only switched after a Yes; Yes interrupts, quits, moves the chat and resumes it', function(wz)
  local cq = setup(wz)
  local p = omp_pane(wz, 7, 'alpha')
  local win = wz.fake.grid { { pane = p } }
  cq.set_pane_kind(function(id) return id == 7 and 'working' or nil end)
  switch_script(wz, { state = 'ok', session = '/s/a.jsonl', cwd = '/w' }, { state = 'ok', session = '/s2/a.jsonl' })
  cq.start_omp(win, p, '2')
  local ask = wz.stub.picker(win)
  T.ok(ask.spec.confirm and ask.spec.confirm.danger, 'a destructive Yes / No question')
  T.eq({ p.sent, #wz.stub.children }, { {}, 0 }, 'nothing happens before the answer')
  ask.answer('no')
  T.eq({ p.sent, #wz.stub.children }, { {}, 0 }, 'No keeps omp running')
  cq.start_omp(win, p, '2')
  wz.stub.picker(win).answer('yes')
  T.eq(p.sent, { '\x1b', '\x04', "cd '/w' && omp --profile=beta --resume '/s2/a.jsonl'\r" })
  T.eq(wz.stub.children, {
    { '/fake/bin/omp-switch', 'locate', '--pid', '507', '--from', 'alpha' },
    { '/fake/bin/omp-switch', 'transfer', '--session', '/s/a.jsonl', '--from', 'alpha', '--to', 'beta' },
  })
end)

T.test('an idle omp with no chat yet restarts fresh under the new profile', function(wz)
  local cq = setup(wz)
  local p = omp_pane(wz, 8, 'alpha')
  local win = wz.fake.grid { { pane = p } }
  cq.set_pane_kind(function() return 'idle' end)
  switch_script(wz, { state = 'none' }, nil)
  cq.start_omp(win, p, 'private')
  wz.stub.run_timers()
  T.eq(p.sent, { '\x04', 'omp --profile=private\r' })
  T.eq(#wz.stub.children, 1, 'no transfer without a session')
end)

T.test('omp that does not quit keeps running and a later switch is not blocked', function(wz)
  local cq = setup(wz)
  local p = omp_pane(wz, 9, 'alpha', true)
  local win = wz.fake.grid { { pane = p } }
  switch_script(wz, { state = 'ok', session = '/s/a.jsonl', cwd = '/elsewhere' }, { state = 'ok', session = '/s2/b' })
  cq.start_omp(win, p, '2')
  wz.stub.run_timers()
  T.eq(p.sent, { '\x04' }, 'only the polite Ctrl+D, never a resume over a live omp')
  T.eq(#wz.stub.children, 1, 'no transfer')
  T.eq(#win.toasts, 1)
  -- The pane is free for another attempt (no "already running" refusal).
  cq.start_omp(win, p, '2')
  T.eq(#wz.stub.children, 2, 'second attempt reached omp-switch locate')
end)

T.test('a failed locate aborts before omp is touched', function(wz)
  local cq = setup(wz)
  local p = omp_pane(wz, 10, 'alpha')
  local win = wz.fake.grid { { pane = p } }
  switch_script(wz, { state = 'error', reason = 'several omp in this terminal' }, nil)
  cq.start_omp(win, p, '2')
  wz.stub.run_timers()
  T.eq(p.sent, {})
  T.ok(win.toasts[1] and win.toasts[1].msg:find('several omp', 1, true), 'the reason is shown')
end)

T.test('a failed transfer after omp quit reports it and resumes nothing', function(wz)
  local cq = setup(wz)
  local p = omp_pane(wz, 11, 'alpha')
  local win = wz.fake.grid { { pane = p } }
  switch_script(wz, { state = 'ok', session = '/s/a.jsonl', cwd = '/elsewhere' },
    { state = 'error', reason = 'different chat under that name' })
  cq.start_omp(win, p, '2')
  wz.stub.run_timers()
  T.eq(p.sent, { '\x04' })
  T.ok(win.toasts[#win.toasts].msg:find('different chat', 1, true), win.toasts[#win.toasts].msg)
end)
