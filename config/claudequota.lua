-- Claude Code usage windows (5h / 7d) per account.
-- Wire: local cq = require('claudequota')(config, wezterm)
--   cq.visible()           -> boolean (LEADER+q toggles the header chip; default true)
--   cq.segments()          -> wezterm.format items, width in cells: one coloured block per
--                             account plus the text of the most critical one (about 12 cells)
--   cq.text()              -> plain text of the chip, for tests
--   cq.refresh_if_due(win) -> spawns `claude-quota refresh` at most once per 60 s (call from update-status)
--   cq.select              -> action for ALT+q: an InputSelector with both windows and reset
--                             countdowns of every account
-- Reads only the numbers cached by ~/.local/bin/claude-quota (io.open, re-read at most every 5 s);
-- the network and credentials are never touched from the GUI thread.
-- Marks: `login` (token expired or rejected), `err` (failed, nothing cached), `~` (numbers stale).
return function(config, wezterm)
  local theme = require 'theme'
  local P = theme.P
  local READ_TTL, SPAWN_EVERY, STALE_AFTER = 5, 60, 180
  local HOME = os.getenv('HOME') or ''
  local DIR = os.getenv('CLAUDE_QUOTA_CACHE_DIR') or (HOME .. '/.cache/wezterm-claude-quota')
  local BIN = os.getenv('CLAUDE_QUOTA_BIN') or (HOME .. '/.local/bin/claude-quota')
  local BLOCK, LABEL_MAX = '▮', 4

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
    if pct >= 80 then return P.red end
    if pct >= 50 then return P.amber end
    return P.green
  end

  local function remaining(secs)
    if secs <= 0 then return 'now' end
    local m = math.floor(secs / 60)
    if m >= 1440 then return string.format('%dd%02dh', m // 1440, m % 1440 // 60) end
    if m >= 60 then return string.format('%dh%02dm', m // 60, m % 60) end
    return string.format('%dm', math.max(m, 1))
  end

  local function windows(e)
    local w = {}
    if type(e.five_hour) == 'table' and e.five_hour.pct then w[#w + 1] = { '5h', e.five_hour } end
    if type(e.seven_day) == 'table' and e.seven_day.pct then w[#w + 1] = { '7d', e.seven_day } end
    return w
  end

  -- Pure summary of one account: the colour of its worst window, that window, and
  -- whether the numbers are stale or need a login.
  local function summarize(e, now)
    local ws = windows(e)
    local login = e.state == 'login'
    local s = { label = tostring(e.label), windows = ws, login = login, e = e }
    if #ws == 0 then
      s.color = login and P.amber or P.grey
      s.pct = -1
      return s
    end
    s.stale = e.state ~= 'ok' or now - (e.updated_at or 0) > STALE_AFTER
    s.worst = ws[1]
    for _, w in ipairs(ws) do
      if type(w[2].reset_at) == 'number' and w[2].reset_at < now then s.stale = true end
      if w[2].pct > s.worst[2].pct then s.worst = w end
    end
    s.pct = s.worst[2].pct
    s.color = login and P.amber or color(s.pct)
    return s
  end

  -- The account the chip spells out: highest worst-window usage; an account that needs
  -- a login outranks one with merely no data when none has numbers.
  local function most_critical(sums)
    local best
    for _, s in ipairs(sums) do
      if not best or s.pct > best.pct then best = s end
    end
    return best
  end

  -- Chip: { { fg, text } ... } pieces after the blocks, for chip() and text().
  local function chip_model(entries, now)
    local sums = {}
    for _, e in ipairs(entries) do sums[#sums + 1] = summarize(e, now) end
    if #sums == 0 then return nil end
    local worst = most_critical(sums)
    local label = worst.label:sub(1, LABEL_MAX)
    local text
    if worst.pct < 0 then
      text = { { worst.color, worst.login and 'login' or 'err' } }
    else
      text = {
        { worst.color, string.format('%s%s ', worst.login and 'login ' or '', worst.worst[1]) },
        { worst.color, string.format('%.0f%%', worst.pct) },
      }
      if worst.stale then text[#text + 1] = { P.amber, '~' } end
    end
    return { sums = sums, label = label, text = text }
  end

  local api = {}

  function api.visible()
    return wezterm.GLOBAL.claudequota_visible ~= false
  end

  function api.segments()
    local now = os.time()
    local m = chip_model(load_entries(now), now)
    if not m then return {}, 0 end
    local items = {}
    for _, s in ipairs(m.sums) do
      items[#items + 1] = { Foreground = { Color = s.color } }
      items[#items + 1] = { Text = BLOCK }
    end
    items[#items + 1] = { Foreground = { Color = P.muted } }
    items[#items + 1] = { Text = ' ' .. m.label .. ' ' }
    for _, t in ipairs(m.text) do
      items[#items + 1] = { Foreground = { Color = t[1] } }
      items[#items + 1] = { Text = t[2] }
    end
    local chip = theme.chip(items)
    return chip, theme.width(chip)
  end

  function api.text()
    local now = os.time()
    local m = chip_model(load_entries(now), now)
    if not m then return '' end
    local out = string.rep(BLOCK, #m.sums) .. ' ' .. m.label .. ' '
    for _, t in ipairs(m.text) do out = out .. t[2] end
    return out
  end

  -- Per-account due-ness and backoff live in the helper; this only paces the spawns.
  function api.refresh_if_due(_window)
    if not api.visible() then return end
    local now = os.time()
    if now - (wezterm.GLOBAL.claudequota_spawned or 0) < SPAWN_EVERY then return end
    wezterm.GLOBAL.claudequota_spawned = now
    pcall(wezterm.background_child_process, { BIN, 'refresh' })
  end

  -- Selector rows ------------------------------------------------------------

  -- The selector reverses the highlighted row, so neutral text takes the default
  -- colour (muted bits use faint intensity) and only the gauge and number carry colour.
  local function row_label(s, now)
    local FAINT, NORMAL = { Attribute = { Intensity = 'Half' } }, { Attribute = { Intensity = 'Normal' } }
    local items = {
      { Foreground = { Color = s.color } }, { Text = BLOCK .. ' ' },
      'ResetAttributes', { Attribute = { Intensity = 'Bold' } },
      { Text = string.format('%-8s', s.label) }, NORMAL,
    }
    if #s.windows == 0 then
      items[#items + 1] = { Foreground = { Color = s.color } }
      items[#items + 1] = { Text = s.login and 'login required: the token expired or was rejected' or 'no data yet' }
    else
      for i, w in ipairs(s.windows) do
        local pct, reset = w[2].pct, w[2].reset_at
        if i > 1 then items[#items + 1] = 'ResetAttributes'; items[#items + 1] = FAINT; items[#items + 1] = { Text = '   │   ' } end
        items[#items + 1] = 'ResetAttributes'
        items[#items + 1] = FAINT
        items[#items + 1] = { Text = w[1] .. ' ' }
        theme.extend(items, theme.line(pct, 12, color(pct), P.dim))
        items[#items + 1] = 'ResetAttributes'
        items[#items + 1] = { Foreground = { Color = color(pct) } }
        items[#items + 1] = { Text = string.format(' %3.0f%%', pct) }
        items[#items + 1] = 'ResetAttributes'
        items[#items + 1] = FAINT
        items[#items + 1] = {
          Text = type(reset) == 'number' and string.format('  resets in %-6s', remaining(reset - now)) or '',
        }
      end
      if s.stale then
        items[#items + 1] = 'ResetAttributes'
        items[#items + 1] = { Foreground = { Color = P.amber } }
        items[#items + 1] = { Text = '  ~ stale' }
      end
    end
    return wezterm.format(items)
  end

  api.select = wezterm.action_callback(function(window, pane)
    local now = os.time()
    local choices = {}
    for _, e in ipairs(load_entries(now)) do
      choices[#choices + 1] = { id = tostring(e.label), label = row_label(summarize(e, now), now) }
    end
    if #choices == 0 then
      choices[1] = { id = 'none', label = wezterm.format {
        { Foreground = { Color = P.muted } }, { Text = 'No quota data cached yet' },
      } }
    end
    window:perform_action(
      wezterm.action.InputSelector {
        title = 'Claude quota',
        description = 'Usage per account and window. Esc closes.',
        choices = choices,
        action = wezterm.action_callback(function() end),
      },
      pane
    )
  end)

  -- Exposed for tests of the pure model.
  api.model = chip_model

  if wezterm.GLOBAL.claudequota_visible == nil then wezterm.GLOBAL.claudequota_visible = true end
  config.keys = config.keys or {}
  table.insert(config.keys, { key = 'q', mods = 'ALT', action = api.select })
  table.insert(config.keys, {
    key = 'q',
    mods = 'LEADER',
    action = wezterm.action_callback(function()
      wezterm.GLOBAL.claudequota_visible = not api.visible()
    end),
  })

  return api
end
