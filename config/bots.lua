-- Bots (scheduled omp agents) in the header: one quiet chip with what is scheduled and what wants a
-- look, the Briefing's unseen count and newest headline, and desktop toasts for new important reports.
-- The scheduler daemon (bin/hivegrid_schedule.py,
-- docs/schedules.md) writes the runtime summary this module reads; nothing here talks to it.
-- Wire: local bots = require('bots')(config, wezterm)
--   bots.summary()          -> the decoded summary table, or nil when the file is missing or
--                              invalid or the scheduler is not running (no heartbeat, or one
--                              older than 60 s). Read at most every 2 s.
--   bots.fresh_items()      -> the summary's items ({} without a summary)
--   bots.briefing()         -> { unseen = n, newest = { entries } } of the summary's Briefing block (the
--                              important reports of all bots), or nil without a summary
--   bots.tiers(summary)     -> header group D, richest first as { items, w }: `◷ 3 · next 09:00 ● 2 CI fixed`,
--                              `◷ 3 · next 09:00 ? 1 ● 2`, `◷ 3 ? 1`, `◷3`; {} when there is nothing enabled, wanting
--                              a look or unseen in the Briefing. The whole chip is the click target `bots`.
--   bots.toast_check(window) -> one toast per NEW important Briefing entry (title Hivegrid, body
--                              `<bot>: <headline>`); the header's update-status calls it. Entries that were already
--                              there when Hivegrid started, and seen ones, never toast.
return function(_config, wezterm)
  local theme = require 'theme'
  local P = theme.P
  local api = {}

  local TTL, STALE, MAX_BYTES = 2, 60, 4 * 1024 * 1024
  local HEADLINE_CELLS = 28 -- the newest headline in the chip

  -- The same path as summary_path() in bin/hivegrid_schedule.py.
  local uid
  local function summary_path()
    local rt = os.getenv('XDG_RUNTIME_DIR')
    if rt and rt:sub(1, 1) == '/' then return rt:gsub('/+$', '') .. '/hivegrid-schedule.json' end
    if not uid then
      local f = io.open('/proc/self/status', 'r')
      local text = f and f:read('a') or ''
      if f then f:close() end
      uid = text:match('Uid:%s*(%d+)') or '0'
    end
    return '/tmp/hivegrid-schedule-' .. uid .. '/summary.json'
  end

  local cache = { at = -math.huge, path = nil, doc = nil }

  local function load()
    local path = summary_path()
    local f = io.open(path, 'r')
    if not f then return nil end
    local ok, text = pcall(f.read, f, MAX_BYTES)
    f:close()
    if not ok or not text or text == '' then return nil end
    local parsed, doc = pcall(wezterm.json_parse, text)
    if not parsed or type(doc) ~= 'table' or type(doc.items) ~= 'table' then return nil end
    local hb = type(doc.scheduler) == 'table' and tonumber(doc.scheduler.heartbeat) or nil
    if not hb or os.time() - hb > STALE then return nil end
    return doc
  end

  function api.summary()
    local now, path = os.time(), summary_path()
    if cache.path == path and now - cache.at < TTL and now >= cache.at then return cache.doc end
    cache.at, cache.path, cache.doc = now, path, load()
    return cache.doc
  end

  function api.fresh_items()
    local doc = api.summary()
    return doc and doc.items or {}
  end

  -- The Briefing block of the summary: { unseen, newest = { { bot_id, bot_name, run, headline, seen, ... } } }.
  function api.briefing()
    local doc = api.summary()
    local b = doc and type(doc.briefing) == 'table' and doc.briefing or nil
    if not b then return nil end
    local newest = {}
    for _, e in ipairs(type(b.newest) == 'table' and b.newest or {}) do
      if type(e) == 'table' then newest[#newest + 1] = e end
    end
    return { unseen = math.max(0, math.floor(tonumber(b.unseen) or 0)), newest = newest }
  end

  local function clean(s)
    return (tostring(s or ''):gsub('%c', ' '))
  end

  -- Header chip ----------------------------------------------------------------------------

  local function next_text(t, now)
    local day = os.date('%Y%m%d', t)
    if day == os.date('%Y%m%d', now) then return os.date('%H:%M', t) end
    if t > now and t - now < 6 * 86400 then return os.date('%a %H:%M', t) end
    return os.date('%b ', t) .. tonumber(os.date('%d', t)) .. os.date(' %H:%M', t)
  end

  local function counts(summary)
    local c = { enabled = 0, running = 0, attention = 0, limit = 0, next_at = nil }
    for _, it in ipairs(summary and summary.items or {}) do
      if type(it) == 'table' then
        local last = type(it.last) == 'table' and it.last or nil
        local wants = it.attention == true or it.state == 'attention'
        if wants then
          if last and last.status == 'limit' then c.limit = c.limit + 1 else c.attention = c.attention + 1 end
        end
        if it.enabled == true and not wants then
          c.enabled = c.enabled + 1
          if it.running == true or it.state == 'working' then c.running = c.running + 1 end
          local n = tonumber(it.next_at)
          if n and (not c.next_at or n < c.next_at) then c.next_at = n end
        end
      end
    end
    return c
  end

  local STATES = theme.STATES

  function api.tiers(summary)
    if not summary then return {} end
    local c = counts(summary)
    local brief = api.briefing()
    local unseen = brief and brief.unseen or 0
    if c.enabled + c.attention + c.limit + unseen == 0 then return {} end
    local now = os.time()
    local headline
    for _, e in ipairs(brief and brief.newest or {}) do
      if e.seen == false and e.headline and e.headline ~= '' then
        headline = clean(e.headline)
        break
      end
    end
    -- ` ● 2` in the accent colour, then (richest tier) the newest unseen headline in muted text.
    local function briefing_items(with_headline)
      if unseen == 0 then return {} end
      local m = { { Text = ' ' }, { Foreground = { Color = P.accent } }, { Text = '● ' .. unseen } }
      if with_headline and headline then
        local text = (utf8.len(headline) or #headline) > HEADLINE_CELLS
          and (wezterm.truncate_right(headline, HEADLINE_CELLS - 1) .. '…') or headline
        theme.extend(m, { { Foreground = { Color = P.muted } }, { Text = ' ' .. text } })
      end
      return m
    end
    local function marks(running)
      local m = {}
      for _, mk in ipairs {
        { 'attention', c.attention }, { 'limit', c.limit }, { 'working', running and c.running or 0 },
      } do
        if mk[2] > 0 then
          local s = STATES[mk[1]]
          theme.extend(m, {
            { Text = ' ' }, { Foreground = { Color = s.color } }, { Text = s.glyph .. ' ' .. mk[2] },
          })
        end
      end
      return m
    end
    local sched = STATES.schedule
    local worst = (c.attention > 0 and 'attention') or (c.limit > 0 and 'limit') or (c.running > 0 and 'working') or nil
    local function chip(items)
      return { items = theme.click('bots', items), w = theme.width(items) }
    end
    local head = {
      { Foreground = { Color = sched.color } }, { Text = sched.glyph },
      { Foreground = { Color = P.muted } }, { Text = ' ' .. c.enabled },
    }
    local out = {}
    local function push(tier)
      if #out == 0 or tier.w < out[#out].w then out[#out + 1] = tier end
    end
    local nxt = {}
    if c.next_at then
      theme.extend(nxt, { { Foreground = { Color = P.dim } }, { Text = ' · next ' },
        { Foreground = { Color = P.muted } }, { Text = next_text(c.next_at, now) } })
    end
    local function tier(with_next, with_headline)
      local items = theme.extend({}, head)
      if with_next then theme.extend(items, nxt) end
      theme.extend(items, marks(true))
      theme.extend(items, briefing_items(with_headline))
      push(chip(items))
    end
    if headline then tier(c.next_at ~= nil, true) end
    if c.next_at then tier(true, false) end
    tier(false, false)
    push(chip({
      { Foreground = { Color = worst and STATES[worst].color or sched.color } }, { Text = sched.glyph },
      { Foreground = { Color = P.muted } }, { Text = tostring(c.enabled) },
    }))
    return out
  end

  -- Toasts ---------------------------------------------------------------------------------

  -- Keys (`<bot>:<run>`) of the Briefing entries already looked at; the first look after the start only fills it.
  local seen, seen_n, seeded = {}, 0, false

  function api.toast_check(window)
    local brief = api.briefing()
    if not brief then return end
    for _, e in ipairs(brief.newest) do
      if e.seen == false then
        local key = tostring(e.bot_id) .. ':' .. tostring(e.run)
        if not seen[key] then
          if seen_n >= 200 then seen, seen_n = {}, 0 end
          seen[key], seen_n = true, seen_n + 1
          if seeded then
            pcall(window.toast_notification, window, 'Hivegrid',
              clean(e.bot_name or e.bot_id) .. ': ' .. clean(e.headline or e.status), nil, 6000)
          end
        end
      end
    end
    seeded = true
  end

  return api
end
