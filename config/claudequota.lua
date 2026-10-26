-- Claude Code usage windows (5h / 7d) per account for the header's right status.
-- Wire: local cq = require('claudequota')(config, wezterm)
--   cq.visible()           -> boolean (ALT+q toggles; default true)
--   cq.segments(opts)      -> wezterm.format items; opts.compact drops 7d and countdowns
--   cq.text(opts)          -> plain text of the same content, for tests
--   cq.refresh_if_due(win) -> spawns `claude-quota refresh` at most once per 60 s (call from update-status)
-- Reads only the numbers cached by ~/.local/bin/claude-quota (io.open, re-read at most every 5 s);
-- the network and credentials are never touched from the GUI thread.
-- Marks: `login` (token expired or rejected), `err` (failed, nothing cached), `~` (numbers stale).
return function(config, wezterm)
  local MUTED, GREEN, AMBER, RED = '#8b949e', '#23d18b', '#e5a50a', '#f14c4c'
  local BARS = { '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█' }
  local READ_TTL, SPAWN_EVERY, STALE_AFTER, SHOW_RESET_AT = 5, 60, 180, 80
  local HOME = os.getenv('HOME') or ''
  local DIR = os.getenv('CLAUDE_QUOTA_CACHE_DIR') or (HOME .. '/.cache/wezterm-claude-quota')
  local BIN = HOME .. '/.local/bin/claude-quota'

  local cache = { t = -math.huge, entries = {} }

  local function slurp(path)
    local f = io.open(path, 'r')
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s
  end

  local function load_entries(now)
    if now - cache.t < READ_TTL then return cache.entries end
    cache.t = now
    local entries = {}
    local ok, files = pcall(wezterm.read_dir, DIR)
    for _, path in ipairs(ok and files or {}) do
      local name = path:match('([^/]+)%.json$')
      if name then
        local text = slurp(path)
        local pok, e = pcall(wezterm.json_parse, text or '')
        if pok and type(e) == 'table' then
          e.label = e.label or name
          entries[#entries + 1] = e
        end
      end
    end
    table.sort(entries, function(a, b) return (a.order or 0) < (b.order or 0) end)
    cache.entries = entries
    return entries
  end

  local function color(pct)
    if pct >= 80 then return RED end
    if pct >= 50 then return AMBER end
    return GREEN
  end

  local function bar(pct)
    return BARS[math.floor(math.max(0, math.min(99.9, pct)) / 100 * 8) + 1]
  end

  local function remaining(secs)
    if secs <= 0 then return 'now' end
    local m = math.floor(secs / 60)
    if m >= 1440 then return string.format('%dd%dh', m // 1440, m % 1440 // 60) end
    if m >= 60 then return string.format('%dh%02dm', m // 60, m % 60) end
    return string.format('%dm', math.max(m, 1))
  end

  local function windows(e)
    local w = {}
    if type(e.five_hour) == 'table' and e.five_hour.pct then w[#w + 1] = { '5h', e.five_hour } end
    if type(e.seven_day) == 'table' and e.seven_day.pct then w[#w + 1] = { '7d', e.seven_day } end
    return w
  end

  -- Pure: one list of { fg, text } per account; segments() and text() both render from it.
  local function model(entries, now, opts)
    local compact = opts and opts.compact
    local out = {}
    for _, e in ipairs(entries) do
      local p = { { MUTED, tostring(e.label) .. ' ' } }
      local ws = windows(e)
      if #ws == 0 then
        local login = e.state == 'login'
        p[#p + 1] = { login and AMBER or RED, login and 'login' or 'err' }
      else
        local stale = e.state ~= 'ok' or now - (e.updated_at or 0) > STALE_AFTER
        for _, w in ipairs(ws) do
          if type(w[2].reset_at) == 'number' and w[2].reset_at < now then stale = true end
        end
        if compact then
          local best = ws[1]
          for _, w in ipairs(ws) do
            if w[2].pct > best[2].pct then best = w end
          end
          ws = { best }
        end
        for i, w in ipairs(ws) do
          local pct, reset = w[2].pct, w[2].reset_at
          if i > 1 then p[#p + 1] = { MUTED, ' ▪ ' } end
          p[#p + 1] = { MUTED, w[1] .. ' ' }
          p[#p + 1] = { color(pct), string.format(compact and '%s%.0f%%' or '%s %.0f%%', bar(pct), pct) }
          local want_reset = not compact and type(reset) == 'number' and (w[1] == '5h' or pct >= SHOW_RESET_AT)
          if want_reset then p[#p + 1] = { MUTED, ' ' .. remaining(reset - now) } end
        end
        if stale then p[#p + 1] = { AMBER, '~' } end
        if e.state == 'login' then p[#p + 1] = { AMBER, ' login' } end
      end
      out[#out + 1] = p
    end
    return out
  end

  local api = {}

  function api.visible()
    return wezterm.GLOBAL.claudequota_visible ~= false
  end

  function api.segments(opts)
    local items = {}
    for _, p in ipairs(model(load_entries(os.time()), os.time(), opts)) do
      for _, s in ipairs(p) do
        items[#items + 1] = { Foreground = { Color = s[1] } }
        items[#items + 1] = { Text = s[2] }
      end
      items[#items + 1] = { Text = '  ' }
    end
    return items
  end

  function api.text(opts)
    local strs = {}
    for _, p in ipairs(model(load_entries(os.time()), os.time(), opts)) do
      local t = {}
      for _, s in ipairs(p) do t[#t + 1] = s[2] end
      strs[#strs + 1] = table.concat(t)
    end
    return table.concat(strs, '  ')
  end

  -- Per-account due-ness and backoff live in the helper; this only paces the spawns.
  function api.refresh_if_due(_window)
    if not api.visible() then return end
    local now = os.time()
    if now - (wezterm.GLOBAL.claudequota_spawned or 0) < SPAWN_EVERY then return end
    wezterm.GLOBAL.claudequota_spawned = now
    pcall(wezterm.background_child_process, { BIN, 'refresh' })
  end

  -- Exposed for tests of the pure model.
  api.model = model

  -- Plain ALT+q, deliberately not behind the leader.
  if wezterm.GLOBAL.claudequota_visible == nil then wezterm.GLOBAL.claudequota_visible = true end
  config.keys = config.keys or {}
  table.insert(config.keys, {
    key = 'q',
    mods = 'ALT',
    action = wezterm.action_callback(function()
      wezterm.GLOBAL.claudequota_visible = not api.visible()
    end),
  })

  return api
end
