-- autocontinue: a limited omp agent gets `continue` once its limit has reset, once per episode.
local T = require 'tests.lua.t'

local T0 = 1800000000
local DELAY = 30
local ACCOUNTS = { { label = 'work', dir = '~/.claude-work', profile = 'work' } }

local function ledger_path() return os.getenv('XDG_RUNTIME_DIR') .. '/hivegrid-agents/autocontinue.json' end

local function state_dir() return os.getenv('XDG_RUNTIME_DIR') .. '/hivegrid-agents' end

-- Builds the module with a temp settings file (opts.settings: extra keys) and a temp quota cache.
-- Returns api, pane (an omp pane, id 11), quota cache dir.
local function setup(wz, opts)
  opts = opts or {}
  T.clock(T0)
  local settings = T.tmpdir() .. '/settings.json'
  T.setenv('HIVEGRID_SETTINGS', settings)
  local doc = { accounts = ACCOUNTS }
  for k, v in pairs(opts.settings or {}) do doc[k] = v end
  T.write(settings, wz.json_encode(doc))
  local cache = T.tmpdir() .. '/quota'
  os.execute('mkdir -p ' .. T.sh_quote(cache))
  T.setenv('CLAUDE_QUOTA_CACHE_DIR', cache)
  local pane = wz.fake.pane { id = 11, process_name = opts.process_name or '/usr/bin/omp' }
  wz.fake.grid { { pane = pane } }
  local api = require('autocontinue')({}, wz)
  return api, pane, cache
end

-- The extension's state file of pane 11, fresh as of the fake clock.
local function write_state(wz, o)
  o = o or {}
  local doc = {
    state = o.state or 'limit', updated = o.updated or T.now(), since = o.since or (T0 - 600), profile = o.profile,
    pending = o.pending,
  }
  if o.limit ~= false then doc.limit = o.limit or { hit_at = T0 - 600, reset_at = T0 + 100 } end
  T.write(state_dir() .. '/11.json', wz.json_encode(doc))
end

local function advance(dt) T.clock(T.now() + dt) end

local function ledger(wz)
  return wz.json_parse(T.read(ledger_path()))
end

local TYPED = { 'continue', '\r' }

T.test('continues once, DELAY seconds after the known reset, and never again for the same limit', function(wz)
  local api, pane = setup(wz)
  write_state(wz) -- resets at T0 + 100
  T.eq(api.tick(), {})
  T.eq(pane.sent, {}, 'the reset is still ahead')
  advance(100)
  write_state(wz)
  T.eq(api.tick(), {})
  T.eq(pane.sent, {}, 'reset reached but the safety delay is not over')
  advance(DELAY)
  write_state(wz)
  T.eq(api.tick(), { 11 })
  T.eq(pane.sent, TYPED)
  for _ = 1, 5 do
    advance(10)
    write_state(wz)
    T.eq(api.tick(), {})
  end
  T.eq(pane.sent, TYPED, 'repeated polls type nothing more')
  T.eq(#wz.stub.children, 0, 'no child process, so no omp-switch')
end)

T.test('a restart of the window does not type twice (the ledger remembers the episode)', function(wz)
  local api, pane = setup(wz)
  advance(200)
  write_state(wz)
  T.eq(api.tick(), { 11 })
  T.eq(ledger(wz).panes['11'].status, 'sent')
  package.loaded['autocontinue'] = nil
  local again = require('autocontinue')({}, wz)
  advance(10)
  write_state(wz)
  T.eq(again.tick(), {})
  T.eq(pane.sent, TYPED)
end)

T.test('a new limit of the same pane is a new episode and continues again', function(wz)
  local api, pane = setup(wz)
  advance(200)
  write_state(wz)
  T.eq(api.tick(), { 11 })
  advance(10)
  write_state(wz, { state = 'working' })
  T.eq(api.tick(), {})
  advance(10)
  write_state(wz, { since = T.now() - 5, limit = { hit_at = T.now() - 5, reset_at = T.now() + 60 } })
  T.eq(api.tick(), {})
  advance(60 + DELAY)
  write_state(wz, { since = T0 + 220 - 5, limit = { hit_at = T0 + 215, reset_at = T0 + 280 } })
  T.eq(api.tick(), { 11 })
  T.eq(pane.sent, { 'continue', '\r', 'continue', '\r' })
end)

T.test('an unknown reset time is never touched without a quota proof', function(wz)
  local api, pane = setup(wz)
  for _, limit in ipairs { { hit_at = T0 - 600 }, { hit_at = T0 - 600, reset_at = 1e13 }, false } do
    write_state(wz, { limit = limit })
    advance(1000)
    write_state(wz, { limit = limit })
    T.eq(api.tick(), {})
  end
  T.eq(pane.sent, {})
end)

T.test('only a fresh, unacknowledged limit state of an omp in front is continued', function(wz)
  local api, pane = setup(wz)
  advance(200)
  for _, state in ipairs { 'working', 'busy', 'done', 'idle', 'attention' } do
    write_state(wz, { state = state })
    T.eq(api.tick(), {}, state)
  end
  write_state(wz, { pending = { kind = 'plan_review' } })
  T.eq(api.tick(), {}, 'waiting for an answer')
  write_state(wz, { updated = T.now() - 60 })
  T.eq(api.tick(), {}, 'stale heartbeat')
  T.write(state_dir() .. '/acks.json', wz.json_encode { ['11'] = T0 - 600 })
  write_state(wz)
  T.eq(api.tick(), {}, 'acknowledged by the user')
  T.eq(pane.sent, {})
end)

T.test('a shell or another program in front gets nothing', function(wz)
  local api, pane = setup(wz, { process_name = '/usr/bin/bash' })
  advance(200)
  write_state(wz)
  T.eq(api.tick(), {})
  T.eq(pane.sent, {})
end)

T.test('settings.json auto_continue.enabled = false turns it off; anything else leaves it on', function(wz)
  local api, pane = setup(wz, { settings = { auto_continue = { enabled = false } } })
  advance(200)
  write_state(wz)
  T.eq(api.tick(), {})
  T.eq(pane.sent, {})
  T.eq(ledger(wz).enabled, false)
  for _, v in ipairs { { enabled = true }, { enabled = 'no' }, 'off', {} } do
    T.fresh()
    wz = require 'wezterm'
    api = (setup(wz, { settings = { auto_continue = v } }))
    advance(200)
    write_state(wz)
    T.eq(api.tick(), { 11 }, wz.json_encode { v })
  end
end)

T.test('at most three continues per pane per hour', function(wz)
  local api, pane = setup(wz)
  T.write(ledger_path(), wz.json_encode { heartbeat = T0, enabled = true, panes = {
    ['11'] = { since = T0 - 5000, status = 'sent', fires = { T0 - 3000, T0 - 2000, T0 - 1000 } } } })
  advance(200)
  write_state(wz)
  T.eq(api.tick(), {})
  T.eq(pane.sent, {})
  T.eq(ledger(wz).panes['11'].status, 'capped')
  advance(10)
  write_state(wz)
  T.eq(api.tick(), {}, 'a capped episode stays capped')
end)

T.test('fires older than an hour do not count towards the cap', function(wz)
  local api, pane = setup(wz)
  T.write(ledger_path(), wz.json_encode { heartbeat = T0, enabled = true, panes = {
    ['11'] = { since = T0 - 9000, status = 'sent', fires = { T0 - 7000, T0 - 6000, T0 - 3700 } } } })
  advance(200)
  write_state(wz)
  T.eq(api.tick(), { 11 })
  T.eq(pane.sent, TYPED)
end)

T.test('the ledger says when a waiting agent continues, for the hive and the phone', function(wz)
  local api = setup(wz)
  write_state(wz, { since = T0 - 600, limit = { hit_at = T0 - 600, reset_at = T0 + 100 } })
  api.tick()
  local doc = ledger(wz)
  T.eq(doc.enabled, true)
  T.eq(doc.heartbeat, T0)
  T.eq(doc.panes['11'], { since = T0 - 600, status = 'waiting', at = T0 + 100 + DELAY, fires = {} })
end)

T.test('quota proof: the account window open again continues an agent whose reset is unknown', function(wz)
  local api, pane, cache = setup(wz)
  local function entry(five, seven, o)
    o = o or {}
    return wz.json_encode { label = 'work', state = 'ok', updated_at = o.updated or (T0 - 100),
      five_hour = { pct = five, reset_at = o.reset5 or (T0 + 9000) },
      seven_day = seven and { pct = seven, reset_at = o.reset7 or (T0 + 90000) } or nil }
  end
  local limit = { hit_at = T0 - 600 } -- no reset time
  local function try()
    write_state(wz, { profile = 'work', limit = limit })
    return api.tick()
  end
  T.eq(try(), {}, 'no cache')
  T.write(cache .. '/work.json', entry(100, 40))
  T.eq(try(), {}, 'still full')
  T.write(cache .. '/work.json', entry(10, 99))
  T.eq(try(), {}, 'the weekly window is full')
  T.write(cache .. '/work.json', entry(10, 40, { updated = T0 - 700 }))
  T.eq(try(), {}, 'numbers from before the limit prove nothing')
  T.write(cache .. '/work.json', entry(10, 40))
  write_state(wz, { limit = limit }) -- another account's profile (none): no proof
  T.eq(api.tick(), {})
  T.eq(pane.sent, {})
  T.eq(try(), { 11 })
  T.eq(pane.sent, TYPED)
end)

T.test('quota proof: a window that reset after the hit counts, and it can beat a reset time still ahead', function(wz)
  local api, pane, cache = setup(wz)
  T.write(cache .. '/work.json', wz.json_encode { label = 'work', state = 'ok', updated_at = T0 - 700,
    five_hour = { pct = 100, reset_at = T0 - 100 }, seven_day = { pct = 20, reset_at = T0 + 90000 } })
  -- the 7d numbers predate the hit and the 5h window reset 100 s ago: the 7d part is unproven
  write_state(wz, { profile = 'work', limit = { hit_at = T0 - 600, reset_at = T0 + 5000 } })
  T.eq(api.tick(), {})
  T.write(cache .. '/work.json', wz.json_encode { label = 'work', state = 'ok', updated_at = T0 - 50,
    five_hour = { pct = 100, reset_at = T0 - 100 }, seven_day = { pct = 20, reset_at = T0 + 90000 } })
  write_state(wz, { profile = 'work', limit = { hit_at = T0 - 600, reset_at = T0 + 5000 } })
  T.eq(api.tick(), { 11 })
  T.eq(pane.sent, TYPED)
end)

T.test('quota_open is false for no entry, no hit time and an entry without windows', function(wz)
  local api = setup(wz)
  local q = api.internals.quota_open
  T.eq(q(nil, T0 - 600, T0), false)
  T.eq(q({ updated_at = T0 - 100, five_hour = { pct = 1 } }, nil, T0), false)
  T.eq(q({ updated_at = T0 - 100 }, T0 - 600, T0), false)
  T.eq(q({ updated_at = T0 - 100, five_hour = { pct = 94.9 } }, T0 - 600, T0), true)
  T.eq(q({ updated_at = T0 - 100, five_hour = { pct = 95 } }, T0 - 600, T0), false)
end)
