-- Continues an omp agent that stopped on its account's usage limit once that limit has reset.
-- Wire: local ac = require('autocontinue')(config, wezterm)   (registers an update-status handler)
--   ac.tick(now?) -> { pane id, ... } of the panes continued by this call (the handler calls it
--                    every EVERY seconds; tests call it directly)
--   ac.internals  -> { quota_open, limit_of }, for the tests
--
-- Why here: the GUI is the one process that is always running while Hivegrid is, it already
-- ticks (update-status), holds every pane object (pane:send_paste, no `wezterm cli` from an
-- event handler) and knows the omp foreground process. Nothing depends on hivegrid-remote or on
-- an omp that loaded the extension late; a restart of the window loses nothing because every
-- decision is written to $XDG_RUNTIME_DIR/hivegrid-agents/autocontinue.json first.
--
-- What it does: a pane whose extension state file (ext/hivegrid-agent-state.ts) says `limit`
-- with a fresh heartbeat, that was not acknowledged, whose foreground program is omp, gets the
-- line `continue` and Enter (the phone's prompt path, bin/hivegrid_agents.py send_prompt) when
--   - its `limit.reset_at` is known and DELAY seconds are past it, or
--   - the account's claude-quota cache (the account whose omp profile the agent runs under) proves
--     the window open again: numbers taken after the limit was hit and below OPEN_PCT, or a window
--     whose reset time lies after the hit and DELAY seconds back.
-- A limit without a reset time and without that proof is never touched. It never switches account.
--
-- Once per limit episode: the episode is the state's `since`; the ledger remembers it, so
-- repeated ticks and a restart type nothing twice, and a new limit (a new `since`) is a new
-- episode. At most CAP continues per pane in CAP_WINDOW seconds, however the episodes go.
--
-- settings.json: "auto_continue": { "enabled": false } turns it off (the settings panel does not
-- edit it; any other value, or no file, leaves it on).
--
-- The ledger is also what the hive and the phone page read (bin/hivegrid_agents.py
-- autocontinue_at): `panes[<id>] = { since, status = waiting | sent | capped, at (epoch s when
-- a waiting one continues, absent when that is not known), fires = { epoch s, ... } }`, plus
-- `enabled` and `heartbeat`, which readers use to ignore a ledger nobody keeps any more.
local wezterm = require 'wezterm'

local DELAY = 30 -- seconds after the reset before the prompt is typed
local CAP, CAP_WINDOW = 3, 3600 -- at most this many continues per pane per hour
local EVERY = 5 -- seconds between checks
local HEARTBEAT = 10 -- the ledger is rewritten at least this often
local STALE_SECS = 20 -- mirrors hivegrid_agents.STALE_SECS: an older state file is not trusted
local OPEN_PCT = 95 -- a quota window below this is open
local TEXT = 'continue'

local STATE_DIR = (os.getenv('XDG_RUNTIME_DIR') or '/tmp') .. '/hivegrid-agents'
local LEDGER = STATE_DIR .. '/autocontinue.json'

local function num(v)
  return type(v) == 'number' and v == v and v ~= math.huge and v ~= -math.huge and v or nil
end

local function read_json(path)
  local f = io.open(path, 'r')
  if not f then return nil end
  local text = f:read('*a')
  f:close()
  local ok, v = pcall(wezterm.json_parse, text or '')
  return ok and type(v) == 'table' and v or nil
end

local function write_json(path, doc)
  local tmp = path .. '.tmp.lua'
  local f = io.open(tmp, 'w')
  if not f then return false end
  f:write(wezterm.json_encode(doc))
  f:close()
  return os.rename(tmp, path) == true
end

-- A limit's reset time as hivegrid_agents.limit_expired reads it: a finite number below 1e12.
local function reset_of(limit)
  local r = type(limit) == 'table' and num(limit.reset_at) or nil
  return r and math.abs(r) < 1e12 and r or nil
end

-- The account's cached windows say it answers again: every window present is below OPEN_PCT in
-- numbers taken after `hit`, or has reset after `hit` (and DELAY seconds ago). One window
-- proven and another not (a weekly one still full) is not open.
local function quota_open(entry, hit, now)
  hit = num(hit)
  if type(entry) ~= 'table' or not hit then return false end
  local updated = num(entry.updated_at)
  local seen = 0
  for _, key in ipairs { 'five_hour', 'seven_day' } do
    local w = entry[key]
    if type(w) == 'table' and num(w.pct) then
      seen = seen + 1
      local reset = num(w.reset_at)
      local reset_after_hit = reset and reset > hit and reset + DELAY <= now
      local fresh_and_open = updated and updated > hit and w.pct < OPEN_PCT
      if not (reset_after_hit or fresh_and_open) then return false end
    end
  end
  return seen > 0
end

local LABEL_OK = '^[%w][%w._-]*$'

return function(config, wezterm_)
  local wz = wezterm_ or wezterm
  local S = require('settings')(config, wz)
  local HOME = os.getenv('HOME') or ''
  local QUOTA_DIR = os.getenv('CLAUDE_QUOTA_CACHE_DIR') or (HOME .. '/.cache/wezterm-claude-quota')
  local api = { internals = { quota_open = quota_open } }

  local ledger = nil -- { panes = { [id string] = record } }, loaded from the file on the first tick
  local last_sig, last_write, last_run = nil, -math.huge, -math.huge

  local function enabled()
    local a = S.auto_continue
    return not (type(a) == 'table' and a.enabled == false)
  end

  -- The cached quota entry of the account the agent's omp profile belongs to (nil without one).
  local function quota_entry(profile)
    if type(profile) ~= 'string' or profile == '' then return nil end
    for _, a in ipairs(S.accounts()) do
      if a.profile == profile and type(a.label) == 'string' and #a.label <= 16 and a.label:match(LABEL_OK) then
        return read_json(QUOTA_DIR .. '/' .. a.label .. '.json')
      end
    end
    return nil
  end

  -- The extension's record of a pane when it is a fresh `limit`: { since, limit, profile }.
  local function limit_of(id, now)
    local doc = read_json(STATE_DIR .. '/' .. id .. '.json')
    if not doc or doc.state ~= 'limit' or doc.pending ~= nil then return nil end
    local updated, since = num(doc.updated), num(doc.since)
    if not updated or not since or now - updated > STALE_SECS then return nil end
    return {
      since = since,
      limit = type(doc.limit) == 'table' and doc.limit or nil,
      profile = type(doc.profile) == 'string' and doc.profile or nil,
    }
  end
  api.internals.limit_of = limit_of

  local function all_panes()
    local out = {}
    local ok, windows = pcall(wz.mux.all_windows)
    for _, w in ipairs(ok and windows or {}) do
      local okt, tabs = pcall(w.tabs, w)
      for _, tab in ipairs(okt and tabs or {}) do
        local okp, list = pcall(tab.panes, tab)
        for _, p in ipairs(okp and list or {}) do out[#out + 1] = p end
      end
    end
    return out
  end

  local function is_omp(pane)
    local ok, name = pcall(pane.get_foreground_process_name, pane)
    return ok and type(name) == 'string' and (name:match('([^/\\]+)[/\\]*$') or '') == 'omp'
  end

  local function recent(fires, now)
    local out = {}
    for _, t in ipairs(type(fires) == 'table' and fires or {}) do
      if num(t) and now - t < CAP_WINDOW then out[#out + 1] = t end
    end
    return out
  end

  -- What changes between ticks, as a string: the ledger is rewritten when it differs (or on the
  -- heartbeat), so a quiet machine writes one small file every HEARTBEAT seconds.
  local function signature(on, panes)
    local parts = { tostring(on) }
    for id, r in pairs(panes) do
      parts[#parts + 1] = string.format('%s:%s:%s:%s:%d', id, tostring(r.since), tostring(r.status), tostring(r.at), #r.fires)
    end
    table.sort(parts)
    return table.concat(parts, ',')
  end

  local function publish(on, panes, now)
    local sig = signature(on, panes)
    if sig == last_sig and now - last_write < HEARTBEAT then return true end
    local out = {}
    for id, r in pairs(panes) do
      out[id] = { since = r.since, status = r.status, at = r.at, fires = r.fires }
    end
    local ok = write_json(LEDGER, { heartbeat = now, enabled = on, panes = out })
    if ok then last_sig, last_write = sig, now end
    return ok
  end

  function api.tick(now)
    now = now or os.time()
    local on = enabled()
    if not ledger then
      local doc = read_json(LEDGER)
      ledger = { panes = {} }
      for id, r in pairs(type(doc) == 'table' and type(doc.panes) == 'table' and doc.panes or {}) do
        if type(r) == 'table' then
          ledger.panes[tostring(id)] = { since = num(r.since), status = r.status, at = num(r.at), fires = recent(r.fires, now) }
        end
      end
    end
    local acks = read_json(STATE_DIR .. '/acks.json') or {}
    local nextp, sent = {}, {}
    for _, pane in ipairs(all_panes()) do
      local id = tostring(pane:pane_id())
      local old = ledger.panes[id]
      old = type(old) == 'table' and old or {}
      local rec = { fires = recent(old.fires, now) }
      local ext = limit_of(id, now)
      if ext and acks[id] ~= ext.since then
        if old.since == ext.since and (old.status == 'sent' or old.status == 'capped') then
          rec.since, rec.status, rec.at = old.since, old.status, num(old.at)
          rec.fires = recent(old.fires, now)
        else
          rec.since, rec.status = ext.since, 'waiting'
          local reset = reset_of(ext.limit)
          rec.at = reset and reset + DELAY or nil
          local hit = ext.limit and num(ext.limit.hit_at)
          local ready = on and ((rec.at and now >= rec.at) or (hit and quota_open(quota_entry(ext.profile), hit, now)))
          if ready and is_omp(pane) then
            if #rec.fires >= CAP then
              rec.status = 'capped'
            else
              rec.fires[#rec.fires + 1] = now
              rec.status = 'sent'
              -- Written before typing: a crash after the type cannot make the next start type again.
              nextp[id] = rec
              local merged = {}
              for k, v in pairs(ledger.panes) do merged[k] = v end
              for k, v in pairs(nextp) do merged[k] = v end
              publish(on, merged, now)
              local ok = pcall(function()
                pane:send_paste(TEXT)
                pane:send_text('\r')
              end)
              if ok then sent[#sent + 1] = pane:pane_id() end
            end
          end
        end
      end
      if rec.status or #rec.fires > 0 then nextp[id] = rec end
    end
    ledger.panes = nextp
    publish(on, nextp, now)
    return sent
  end

  wz.on('update-status', function()
    local now = os.time()
    if now - last_run < EVERY then return end
    last_run = now
    pcall(api.tick, now)
  end)

  return api
end
