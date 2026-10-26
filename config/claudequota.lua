-- Claude Code usage windows (5h / 7d) per account.
-- Wire: local cq = require('claudequota')(config, wezterm)
--   cq.visible()           -> boolean (LEADER+q toggles the header chip; default true)
--   cq.tiers()             -> header pills richest first as { items, w }: every account with
--                             label, gauge and numbers, down to dots plus the critical account
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

  -- Header tiers ---------------------------------------------------------------

  local GAUGE = 6 -- cells, caps included

  local function lab(s, n) return n and s.label:sub(1, n) or s.label end

  -- Items of one account's numbers: every window with its own colour, a reset
  -- countdown only for a window at or above 80%, `login` / `--` when nothing is cached.
  local function numbers(s, now)
    local items = {}
    if #s.windows == 0 then
      items[#items + 1] = { Foreground = { Color = s.color } }
      items[#items + 1] = { Text = s.login and 'login' or '--' }
      return items
    end
    if s.login then
      items[#items + 1] = { Foreground = { Color = P.amber } }
      items[#items + 1] = { Text = 'login ' }
    end
    for i, w in ipairs(s.windows) do
      local pct, reset = w[2].pct, w[2].reset_at
      if i > 1 then items[#items + 1] = { Text = ' ' } end
      items[#items + 1] = { Foreground = { Color = P.muted } }
      items[#items + 1] = { Text = w[1] .. ' ' }
      items[#items + 1] = { Foreground = { Color = color(pct) } }
      items[#items + 1] = { Text = string.format('%.0f%%', pct) }
      if pct >= 80 and type(reset) == 'number' then
        items[#items + 1] = { Foreground = { Color = P.dim } }
        items[#items + 1] = { Text = ' ↻' .. remaining(reset - now) }
      end
    end
    if s.stale then
      items[#items + 1] = { Foreground = { Color = P.amber } }
      items[#items + 1] = { Text = '~' }
    end
    return items
  end

  local function worst_text(s)
    if s.pct < 0 then return s.login and 'login' or '--' end
    return string.format('%.0f%%', s.pct) .. (s.stale and '~' or '')
  end

  local function label_items(s, crit, n)
    return {
      { Foreground = { Color = crit and P.bright or P.muted } },
      { Attribute = { Intensity = crit and 'Bold' or 'Normal' } },
      { Text = lab(s, n) .. ' ' },
      { Attribute = { Intensity = 'Normal' } },
    }
  end

  -- Richest first. `crit` is the most critical account, lifted onto the raised
  -- colour with a bold bright label when it is at least amber.
  local function build_tiers(m, now)
    local sums, crit = m.sums, nil
    for _, s in ipairs(sums) do
      if s.pct >= 50 and (not crit or s.pct > crit.pct) then crit = s end
    end
    local function bg_of(s) return s == crit and P.raised or P.pill end
    local function gauge(s)
      return theme.gauge(math.max(s.pct, 0), GAUGE, s.color, bg_of(s))
    end

    local function per_account(label_n, with_numbers)
      local items = {}
      for _, s in ipairs(sums) do
        if #items > 0 then items[#items + 1] = { Text = ' ' } end
        local body = label_items(s, s == crit, label_n)
        theme.extend(body, gauge(s))
        body[#body + 1] = { Text = ' ' }
        if with_numbers then
          theme.extend(body, numbers(s, now))
        else
          theme.extend(body, { { Foreground = { Color = s.color } }, { Text = worst_text(s) } })
        end
        theme.extend(items, theme.pill(body, bg_of(s)))
      end
      return items
    end

    local function one_pill(label_n)
      local body = {}
      for _, s in ipairs(sums) do
        if #body > 0 then body[#body + 1] = { Text = '  ' } end
        theme.extend(body, label_items(s, s == crit, label_n))
        theme.extend(body, { { Foreground = { Color = s.color } }, { Text = worst_text(s) } })
      end
      return theme.pill(body, P.pill)
    end

    local function dots()
      local body = {}
      for _, s in ipairs(sums) do
        body[#body + 1] = { Foreground = { Color = s.color } }
        body[#body + 1] = { Text = '● ' }
      end
      local c = crit
      if not c then
        for _, s in ipairs(sums) do if not c or s.pct > c.pct then c = s end end
      end
      theme.extend(body, label_items(c, true))
      if c.pct < 0 then
        theme.extend(body, { { Foreground = { Color = c.color } }, { Text = worst_text(c) } })
      else
        theme.extend(body, {
          { Foreground = { Color = P.muted } }, { Text = c.worst[1] .. ' ' },
          { Foreground = { Color = c.color } }, { Text = worst_text(c) },
        })
      end
      return theme.pill(body, crit and P.raised or P.pill)
    end

    local out = {}
    for _, items in ipairs {
      per_account(nil, true), per_account(4, false), one_pill(4), one_pill(1), dots(),
    } do
      out[#out + 1] = { items = items, w = theme.width(items) }
    end
    return out
  end

  local api = {}

  function api.visible()
    return wezterm.GLOBAL.claudequota_visible ~= false
  end

  -- Header pills richest first as { items, w }; empty when no account is cached. The
  -- caller steps down the list until one fits its free width. Rebuilt when the cache
  -- was re-read or every 15 s, which keeps the minute-grained countdowns current.
  local memo = { entries = nil, slot = nil, tiers = {} }
  function api.tiers()
    local now = os.time()
    local entries = load_entries(now)
    local slot = now // 15
    if memo.entries ~= entries or memo.slot ~= slot then
      local m = chip_model(entries, now)
      memo.entries, memo.slot, memo.tiers = entries, slot, m and build_tiers(m, now) or {}
    end
    return memo.tiers
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

  -- Starting omp for an account -----------------------------------------------
  -- omp picks its account with --profile (or OMP_PROFILE), not CLAUDE_CONFIG_DIR; the
  -- profiles pair with the Claude config dirs ~/.claude, ~/.claude-2, ~/.claude-private
  -- exactly as the claude-work / claude-team / claude-private aliases do.
  local PROFILES = { main = 'work', ['2'] = 'team', private = 'private' }
  local DEFAULT_PROFILE = 'work' -- what a bare `omp` uses (OMP_PROFILE default in ~/.bashrc)
  local SHELLS = { bash = true, zsh = true, fish = true, sh = true, dash = true }

  local function toast(window, msg) window:toast_notification('Hivegrid', msg, nil, 4000) end

  local function basename(p) return (tostring(p or ''):match('([^/]+)$') or ''):gsub('^%-', '') end

  -- The pane to type into: the active one, or the last non-sidebar pane when the
  -- sidebar has focus.
  local function target_pane(window, pane)
    local is_sidebar = require('sidebar').is_sidebar
    if not is_sidebar(pane:pane_id()) then return pane end
    local tab = window:active_tab()
    local rec = (wezterm.GLOBAL.sidebar or {})[tostring(tab:tab_id())] or {}
    local first
    for _, p in ipairs(tab:panes()) do
      if not is_sidebar(p:pane_id()) then
        if p:pane_id() == rec.last then return p end
        first = first or p
      end
    end
    return first
  end

  -- Profile of a running omp: --profile on its command line, else OMP_PROFILE from
  -- its environment (only that variable is read), else the bare-omp default.
  local function running_profile(info)
    local argv = info.argv or {}
    for i, a in ipairs(argv) do
      local v = a:match('^%-%-profile=(.+)$') or (a == '--profile' and argv[i + 1])
      if v then return v end
    end
    local f = info.pid and io.open('/proc/' .. info.pid .. '/environ', 'rb')
    if not f then return nil end
    local env = f:read('*a') or ''
    f:close()
    return env:match('%f[^%z]OMP_PROFILE=([^%z]*)') or env:match('^OMP_PROFILE=([^%z]*)') or DEFAULT_PROFILE
  end

  local function start_omp(window, pane, label)
    local profile = PROFILES[label]
    if not profile then
      return toast(window, 'No omp profile is paired with account ' .. tostring(label))
    end
    local target = target_pane(window, pane)
    local info = target and target:get_foreground_process_info()
    if not info then return toast(window, 'Cannot tell what is running in the pane') end
    local name = basename(info.name or (info.argv or {})[1])
    if name == 'omp' then
      local running = running_profile(info)
      if running == profile then return toast(window, 'omp is already running with account ' .. label) end
      local account = running
      for l, p in pairs(PROFILES) do
        if p == running then account = string.format('%s (%s)', l, p) end
      end
      return toast(window, string.format('omp is running with account %s in this pane',
        account or 'unknown'))
    end
    if not SHELLS[name] then
      return toast(window, string.format('%s is running in this pane, not a shell prompt', name))
    end
    target:send_text('omp --profile=' .. profile .. '\r')
  end
  api.start_omp = start_omp

  -- Alt+q: the selector doubles as the account list; Enter or a click on a row starts omp.
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
        description = 'Enter or click: start omp with that account in the active pane. Esc closes.',
        choices = choices,
        action = wezterm.action_callback(function(win, p, id)
          if id and id ~= 'none' then start_omp(win, p, id) end
        end),
      },
      pane
    )
  end)

  -- Exposed for tests of the pure model.
  api.model = chip_model

  if wezterm.GLOBAL.claudequota_visible == nil then wezterm.GLOBAL.claudequota_visible = true end
  config.keys = config.keys or {}
  table.insert(config.keys, { key = 'q', mods = 'ALT', action = api.select })
  -- LEADER+u then 1/2/3 starts omp for main / 2 / private without the popup.
  config.key_tables = config.key_tables or {}
  config.key_tables.omp_account = {
    { key = '1', action = wezterm.action_callback(function(w, p) start_omp(w, p, 'main') end) },
    { key = '2', action = wezterm.action_callback(function(w, p) start_omp(w, p, '2') end) },
    { key = '3', action = wezterm.action_callback(function(w, p) start_omp(w, p, 'private') end) },
    { key = 'Escape', action = wezterm.action.PopKeyTable },
  }
  table.insert(config.keys, {
    key = 'u',
    mods = 'LEADER',
    action = wezterm.action.ActivateKeyTable {
      name = 'omp_account', one_shot = true, timeout_milliseconds = 4000,
    },
  })
  table.insert(config.keys, {
    key = 'q',
    mods = 'LEADER',
    action = wezterm.action_callback(function()
      wezterm.GLOBAL.claudequota_visible = not api.visible()
    end),
  })

  return api
end
