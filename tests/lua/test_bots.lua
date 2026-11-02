-- The Bots header chip and toasts (config/bots.lua) over a fake runtime summary file.
local T = require 'tests.lua.t'

local T0 = 1700000000

local function item(id, o)
  o = o or {}
  return {
    id = id, name = o.name or id, kind = 'agent', enabled = o.enabled ~= false, when_text = 'daily',
    next_at = o.next_at, state = o.state or 'schedule', last = o.last, running = o.running or false,
    attention = o.attention or false,
  }
end

-- Writes the summary into a fresh XDG_RUNTIME_DIR; returns the file path.
local function summary(items, heartbeat)
  local dir = T.tmpdir()
  T.setenv('XDG_RUNTIME_DIR', dir)
  return T.write(dir .. '/hivegrid-schedule.json', require('wezterm').json_encode {
    ts = T0, scheduler = { heartbeat = heartbeat or T0 }, counts = {}, items = items,
  })
end

local function plain(items)
  local out = {}
  for _, c in ipairs(T.render(items)) do out[#out + 1] = c.ch end
  return table.concat(out)
end

local function module(wz) return require('bots')({}, wz) end

T.test('missing, invalid and stale summaries give nil and no chip', function(wz)
  T.clock(T0)
  T.setenv('XDG_RUNTIME_DIR', T.tmpdir())
  local bots = module(wz)
  T.eq(bots.summary(), nil)
  T.eq(bots.tiers(bots.summary()), {})
  T.eq(bots.fresh_items(), {})
  T.clock(T0 + 10)
  T.write(os.getenv('XDG_RUNTIME_DIR') .. '/hivegrid-schedule.json', 'not json')
  T.eq(bots.summary(), nil)
  summary({ item('a') }, T0 - 100)
  T.clock(T0 + 20)
  T.eq(bots.summary(), nil, 'heartbeat older than 60 s')
  summary({ item('a') }, T0 + 30 - 59)
  T.clock(T0 + 30)
  T.ok(bots.summary())
end)

T.test('the summary is read at most every 2 seconds', function(wz)
  T.clock(T0)
  local path = summary({ item('a') })
  local bots = module(wz)
  T.eq(#bots.summary().items, 1)
  T.write(path, wz.json_encode { scheduler = { heartbeat = T0 }, items = { item('a'), item('b') } })
  T.eq(#bots.summary().items, 1, 'cached')
  T.clock(T0 + 2)
  T.eq(#bots.summary().items, 2)
end)

T.test('tiers: waiting count and next run, narrowing; hidden without enabled or wanting items', function(wz)
  local theme = require 'theme'
  T.clock(T0)
  local soon = T0 + 60
  local bots = module(wz)
  local doc = { items = {
    item('a', { next_at = soon }), item('b', { next_at = soon + 60 }), item('c', { next_at = soon + 7200 }),
    item('d', { enabled = false, state = 'idle' }),
    item('e', { attention = true, state = 'attention', last = { status = 'failed', seen = false, ended = 1 } }),
  } }
  local tiers = bots.tiers(doc)
  T.eq(#tiers, 3)
  local want = os.date('%H:%M', soon)
  T.ok(plain(tiers[1].items):find('◷ 3 · next ' .. want .. ' ? 1', 1, true), plain(tiers[1].items))
  T.eq(plain(tiers[2].items), '◷ 3 ? 1')
  T.eq(plain(tiers[3].items), '◷3')
  for i, t in ipairs(tiers) do
    T.eq(t.w, theme.width(t.items))
    if i > 1 then T.ok(t.w < tiers[i - 1].w, 'strictly narrower') end
  end
  T.eq(bots.tiers({ items = { item('d', { enabled = false, state = 'idle' }) } }), {})
  T.eq(bots.tiers(nil), {})
  local far = bots.tiers({ items = { item('a', { next_at = T0 + 3 * 86400 }) } })[1]
  T.ok(plain(far.items):find('next %a%a%a %d%d:%d%d'), plain(far.items))
end)

T.test('colours follow the worst unseen state', function(wz)
  local theme = require 'theme'
  local S = theme.STATES
  T.clock(T0)
  local bots = module(wz)
  local function mark(items, ch)
    for _, c in ipairs(T.render(bots.tiers({ items = items })[1].items)) do
      if c.ch == ch then return c.fg end
    end
  end
  local limit = item('l', { attention = true, state = 'attention', last = { status = 'limit', seen = false, ended = 1 } })
  T.eq(mark({ limit }, '⊘'), S.limit.color)
  local bad = item('f', { attention = true, state = 'attention', last = { status = 'failed', seen = false, ended = 1 } })
  T.eq(mark({ bad }, '?'), S.attention.color)
  T.eq(mark({ item('r', { running = true, state = 'working', next_at = T0 + 5 }) }, '↻'), S.working.color)
  local narrow = bots.tiers({ items = { bad, item('a') } })[2]
  T.eq(T.render(narrow.items)[1].fg, S.attention.color, 'the glyph takes the worst state colour')
end)

T.test('the chip is the click target bots on the fork and plain text without it', function(wz)
  T.clock(T0)
  wz.stub.fork = true
  local bots = module(wz)
  local doc = { items = { item('a', { next_at = T0 + 60 }) } }
  local clicks = 0
  for _, it in ipairs(bots.tiers(doc)[1].items) do
    if type(it) == 'table' and it.Click then T.eq(it.Click, 'bots'); clicks = clicks + 1 end
  end
  T.eq(clicks, 1)
  local wz2 = T.fresh()
  for _, it in ipairs(module(wz2).tiers(doc)[1].items) do T.ok(not (type(it) == 'table' and it.Click)) end
end)

local function entry(bot, run, headline, seen)
  return { bot_id = bot, bot_name = bot .. ' bot', kind = 'agent', run = run, level = 'important', status = 'ok',
    headline = headline, ended = run, seen = seen == true }
end

-- A summary file with a Briefing block: `newest` entries (at most 3 in the real file), `unseen` the count.
local function with_briefing(path, wz, unseen, newest, items)
  T.write(path, wz.json_encode { scheduler = { heartbeat = T0 }, items = items or {}, briefing = { unseen = unseen, newest = newest } })
end

T.test('briefing: the summary block, nil without one, malformed entries dropped', function(wz)
  T.clock(T0)
  local path = summary({ item('a') })
  local bots = module(wz)
  T.eq(bots.briefing(), nil)
  T.clock(T0 + 3)
  with_briefing(path, wz, 2, { entry('ci', 5, 'CI fixed'), 'junk' })
  local b = bots.briefing()
  T.eq(b.unseen, 2)
  T.eq(#b.newest, 1)
  T.eq(b.newest[1].headline, 'CI fixed')
end)

T.test('the chip shows the unseen count and the newest unseen headline, and appears for them alone', function(wz)
  T.clock(T0)
  local path = summary({})
  local bots = module(wz)
  T.clock(T0 + 3)
  with_briefing(path, wz, 2, { entry('ci', 6, 'CI x fixed: flaky timeout'), entry('db', 5, 'old one', true) }, { item('a', { next_at = T0 + 60 }) })
  local tiers = bots.tiers(bots.summary())
  T.ok(plain(tiers[1].items):find('● 2 CI x fixed: flaky timeout', 1, true), plain(tiers[1].items))
  T.ok(plain(tiers[2].items):find('● 2', 1, true) and not plain(tiers[2].items):find('CI x', 1, true), plain(tiers[2].items))
  for i, t in ipairs(tiers) do
    if i > 1 then T.ok(t.w < tiers[i - 1].w, 'strictly narrower') end
  end
  T.clock(T0 + 6)
  with_briefing(path, wz, 1, { entry('ci', 6, 'x' .. ('y'):rep(80)) }, {})
  local only = bots.tiers(bots.summary())
  T.ok(#only >= 1, 'an unseen report alone shows the chip')
  T.ok(plain(only[1].items):find('…', 1, true), 'a long headline is cut')
  T.clock(T0 + 9)
  with_briefing(path, wz, 0, { entry('ci', 6, 'seen', true) }, {})
  T.eq(bots.tiers(bots.summary()), {})
end)

T.test('toasts: one per NEW important report, silent at startup, never twice, never for seen ones', function(wz)
  T.clock(T0)
  local path = summary({})
  with_briefing(path, wz, 1, { entry('old', 1, 'already there') })
  local bots = module(wz)
  local win = wz.fake.window {}
  bots.toast_check(win)
  T.eq(#win.toasts, 0, 'what was unseen at startup is remembered, not toasted')
  T.clock(T0 + 3)
  with_briefing(path, wz, 3, { entry('new', 2, 'CI of repo x fixed: flaky timeout'), entry('seen', 3, 'read already', true), entry('old', 1, 'already there') })
  bots.toast_check(win)
  T.eq(#win.toasts, 1)
  T.eq({ win.toasts[1].title, win.toasts[1].msg }, { 'Hivegrid', 'new bot: CI of repo x fixed: flaky timeout' })
  T.clock(T0 + 6)
  bots.toast_check(win)
  T.eq(#win.toasts, 1, 'the same report never toasts twice')
  T.clock(T0 + 9)
  with_briefing(path, wz, 1, { entry('next', 4, 'Backup failed: disk full\nsecond line') })
  bots.toast_check(win)
  T.eq(#win.toasts, 2)
  T.ok(not win.toasts[2].msg:find('%c'), 'control characters of the headline never reach the toast')
end)
