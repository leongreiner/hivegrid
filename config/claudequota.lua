-- Claude Code usage windows (5h / 7d) per account.
-- Wire: local cq = require('claudequota')(config, wezterm)
--   cq.visible()           -> boolean (LEADER+q toggles the header chip; default true)
--   cq.tiers(active)       -> header pills richest first as { items, w }: every account with
--                             label, numbers and slim bars, down to dots plus one account; the
--                             account labelled `active` (cq.active) is raised and bold
--   cq.text()              -> plain text of the chip, for tests
--   cq.refresh_if_due(win) -> spawns `claude-quota refresh` at most once per 60 s (call from update-status)
--   cq.select              -> action for ALT+q: the Hivegrid picker (picker.lua) with both windows
--                             and reset countdowns of every account
-- Reads only the numbers cached by ~/.local/bin/claude-quota (io.open, re-read at most every 5 s);
-- the network and credentials are never touched from the GUI thread.
-- The accounts shown come from settings.lua (the panel's list, else today's three, plus any
-- ~/.claude-<name> directory with credentials): one pill per account, in that order, and an
-- account without cached numbers yet shows `--`. When the list changes the config reloads.
-- Picking an account (pill click, ALT+q row, LEADER+u <n>) starts omp with its profile, or,
-- when omp already runs in the pane under another profile, moves that chat over (see
-- switch_account).
-- Marks: `login` (token expired or rejected), `err` (failed, nothing cached), `~` (numbers stale).
return function(config, wezterm)
  local theme = require 'theme'
  local P = theme.P
  local READ_TTL, SPAWN_EVERY, STALE_AFTER = 5, 60, 180
  local HOME = os.getenv('HOME') or ''
  local DIR = os.getenv('CLAUDE_QUOTA_CACHE_DIR') or (HOME .. '/.cache/wezterm-claude-quota')
  local BIN = os.getenv('CLAUDE_QUOTA_BIN') or (HOME .. '/.local/bin/claude-quota')
  local BLOCK, LABEL_MAX = '▮', 4
  local S = require('settings')(config, wezterm)
  local accounts_at_load = S.signature()

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
    -- Only the configured accounts, in their order; a removed account's cache file is ignored.
    local by_label = {}
    for _, e in ipairs(entries) do by_label[tostring(e.label)] = e end
    local shown = {}
    for _, a in ipairs(S.accounts()) do
      local e = by_label[a.label]
      if not e and a.creds then e = { label = a.label, state = 'none' } end
      if e then shown[#shown + 1] = e end
    end
    cache.entries = shown
    return shown
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

  -- Usage first; without numbers a login request ranks above plain no data.
  local function rank(s)
    if s.pct >= 0 then return s.pct end
    return s.login and -0.5 or -1
  end

  -- The account the chip spells out: highest worst-window usage; an account that needs
  -- a login outranks one with merely no data when none has numbers.
  local function most_critical(sums)
    local best
    for _, s in ipairs(sums) do
      if not best or rank(s) > rank(best) then best = s end
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

  local function lab(s, n) return n and s.label:sub(1, n) or s.label end

  -- The worst window's number in its colour; the stale mark `~` is amber in every tier.
  local function worst_items(s)
    if s.pct < 0 then return { { Foreground = { Color = s.color } }, { Text = s.login and 'login' or '--' } } end
    local items = { { Foreground = { Color = s.color } }, { Text = string.format('%.0f%%', s.pct) } }
    if s.stale then
      items[#items + 1] = { Foreground = { Color = P.amber } }
      items[#items + 1] = { Text = '~' }
    end
    return items
  end

  local function label_items(s, crit, n)
    return {
      { Foreground = { Color = crit and P.bright or P.muted } },
      { Attribute = { Intensity = crit and 'Bold' or 'Normal' } },
      { Text = lab(s, n) .. ' ' },
      { Attribute = { Intensity = 'Normal' } },
    }
  end

  -- Richest first. `active` is the label of the account whose omp runs in the focused pane
  -- (api.active): its label is bold and bright and it sits on a raised block. Every tier is
  -- one continuous group with a divider between accounts, each account a click target.
  -- Numbers and bars always sit on the plain pill; an account that needs a login shows
  -- the amber `login` chip and nothing stale beside it.
  local function build_tiers(m, now, active)
    local sums = m.sums
    local function click(s, items) return theme.click('quota:' .. s.label, items) end

    -- opts: label_n (label cut), bar (cells of the slim line after each window),
    -- worst_only (only the window with the highest usage).
    local function account(s, opts)
      local lit = s.label == active
      local bg = lit and P.raised or P.pill
      local items = {}
      if lit then
        theme.extend(items, {
          { Foreground = { Color = P.raised } }, { Background = { Color = P.pill } },
          { Text = theme.CAP_L },
        })
      end
      items[#items + 1] = { Background = { Color = bg } }
      theme.extend(items, label_items(s, lit, opts.label_n))
      if s.login then
        theme.extend(items, theme.chip('login', P.amber, bg))
      elseif #s.windows == 0 then
        items[#items + 1] = { Foreground = { Color = P.grey } }
        items[#items + 1] = { Text = '--' }
      else
        for i, w in ipairs(opts.worst_only and { s.worst } or s.windows) do
          local pct, reset = w[2].pct, w[2].reset_at
          if i > 1 then items[#items + 1] = { Text = ' ' } end
          items[#items + 1] = { Foreground = { Color = P.muted } }
          items[#items + 1] = { Text = w[1] .. ' ' }
          items[#items + 1] = { Foreground = { Color = color(pct) } }
          items[#items + 1] = { Text = string.format('%.0f%%', pct) }
          if opts.bar then
            items[#items + 1] = { Text = ' ' }
            theme.extend(items, theme.meter(pct, opts.bar, color(pct), bg))
          end
          if pct >= 80 and type(reset) == 'number' then
            items[#items + 1] = { Foreground = { Color = P.muted } }
            items[#items + 1] = { Text = ' ↻' .. remaining(reset - now) }
          end
        end
        if s.stale then
          items[#items + 1] = { Foreground = { Color = P.amber } }
          items[#items + 1] = { Text = opts.bar and ' ~' or '~' } -- never glued to a bar
        end
      end
      if lit then
        theme.extend(items, {
          { Text = ' ' }, { Foreground = { Color = P.raised } },
          { Background = { Color = P.pill } }, { Text = theme.CAP_R },
        })
      end
      items[#items + 1] = { Background = { Color = P.pill } }
      return click(s, items)
    end

    local function accounts(opts)
      local parts = {}
      for _, s in ipairs(sums) do parts[#parts + 1] = account(s, opts) end
      return theme.group(parts, P.pill)
    end

    local function compact(label_n)
      local parts = {}
      for _, s in ipairs(sums) do
        local seg = label_items(s, s.label == active, label_n)
        if s.login then
          theme.extend(seg, theme.chip('login', P.amber, P.pill))
        else
          theme.extend(seg, worst_items(s))
        end
        parts[#parts + 1] = click(s, seg)
      end
      return theme.group(parts, P.pill)
    end

    local function dots()
      local body = {}
      for _, s in ipairs(sums) do
        theme.extend(body, click(s, { { Foreground = { Color = s.color } }, { Text = '● ' } }))
      end
      local c
      for _, s in ipairs(sums) do
        if s.label == active then c = s end
      end
      c = c or most_critical(sums)
      local seg = label_items(c, c.label == active, nil)
      if c.pct < 0 or c.login then
        theme.extend(seg, { { Foreground = { Color = c.color } }, { Text = c.login and 'login' or '--' } })
      else
        theme.extend(seg, { { Foreground = { Color = P.muted } }, { Text = c.worst[1] .. ' ' } })
        theme.extend(seg, worst_items(c))
      end
      theme.extend(body, click(c, seg))
      return theme.pill(body, c.label == active and P.raised or P.pill)
    end

    local out = {}
    for _, items in ipairs {
      accounts { label_n = 7, bar = 4 }, accounts { label_n = 7 },
      accounts { label_n = 4, worst_only = true }, compact(4), dots(),
    } do
      out[#out + 1] = { items = items, w = theme.width(items) }
    end
    return out
  end

  local api = {}

  function api.visible()
    return wezterm.GLOBAL.claudequota_visible ~= false
  end

  -- Header pills richest first as { items, w }; empty when no account is cached. `active` is
  -- the label api.active returned (nil: none highlighted). The caller steps down the list
  -- until one fits its free width. Rebuilt when the cache was re-read, the active account
  -- changed, or every 15 s, which keeps the minute-grained countdowns current.
  local memo = { entries = nil, slot = nil, active = nil, tiers = {} }
  function api.tiers(active)
    local now = os.time()
    local entries = load_entries(now)
    local slot = now // 15
    if memo.entries ~= entries or memo.slot ~= slot or memo.active ~= active then
      local m = chip_model(entries, now)
      memo.entries, memo.slot, memo.active = entries, slot, active
      memo.tiers = m and build_tiers(m, now, active) or {}
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
    -- An account came or went (a new ~/.claude-<name>): reload so the key table follows.
    if S.signature() ~= accounts_at_load and os.time() - (wezterm.GLOBAL.claudequota_reloaded or 0) > 10 then
      wezterm.GLOBAL.claudequota_reloaded = os.time()
      pcall(wezterm.reload_configuration)
    end
    if not api.visible() then return end
    local now = os.time()
    if now - (wezterm.GLOBAL.claudequota_spawned or 0) < SPAWN_EVERY then return end
    wezterm.GLOBAL.claudequota_spawned = now
    pcall(wezterm.background_child_process, { BIN, 'refresh' })
  end

  -- Picker rows ------------------------------------------------------------------

  -- One account as a picker row (picker.lua): its number (the LEADER+u digit), the label,
  -- then per window "5h ━━━━━━━━──── 42% ↻2h10m" as coloured segments; stale numbers and the
  -- account of the pane's running omp are marked on the right.
  local function picker_row(s, n, now, active)
    local cols = { { text = s.label, bold = true } }
    if #s.windows == 0 then
      cols[2] = { text = s.login and 'login required: the token expired or was rejected' or 'no data yet',
        color = s.login and 'amber' or 'dim' }
    else
      for _, w in ipairs(s.windows) do
        local pct, reset = w[2].pct, w[2].reset_at
        local segs = { { text = w[1] .. ' ', color = 'muted' } }
        theme.extend(segs, theme.line(pct, 10, color(pct), theme.lerp(P.surface, P.edge_hi, 0.35)))
        segs[#segs + 1] = { text = string.format(' %3.0f%%', pct), color = color(pct) }
        if type(reset) == 'number' then
          segs[#segs + 1] = { text = ' ↻' .. remaining(reset - now), color = 'dim' }
        end
        cols[#cols + 1] = { segs = segs }
      end
    end
    local meta, meta_color
    if s.stale then meta, meta_color = '~ stale', 'amber' end
    if s.label == active then meta, meta_color = (meta and (meta .. ' · ') or '') .. 'in use', 'accent' end
    return {
      id = s.label, key = n <= 9 and tostring(n) or nil, glyph = '●', color = s.color,
      cols = cols, meta = meta, meta_color = meta_color,
    }
  end

  -- Starting omp for an account / switching a running omp -----------------------
  -- omp picks its account with --profile (or OMP_PROFILE), not CLAUDE_CONFIG_DIR; the profile
  -- paired with each account comes from settings.lua.
  local DEFAULT_PROFILE = 'work' -- what a bare `omp` uses (OMP_PROFILE default in ~/.bashrc)
  local SHELLS = { bash = true, zsh = true, fish = true, sh = true, dash = true }
  local SWITCH_BIN = os.getenv('OMP_SWITCH_BIN') or (HOME .. '/.local/bin/omp-switch')
  local EXIT_KEY = '\x04' -- Ctrl+D on an empty prompt quits omp; with a draft it does nothing
  local EXIT_WAIT, EXIT_POLL = 5, 0.25 -- seconds to wait for omp to hand the pane back

  local function toast(window, msg)
    wezterm.log_info('hivegrid: ' .. msg)
    window:toast_notification('Hivegrid', msg, nil, 4000)
  end

  local function basename(p) return (tostring(p or ''):match('([^/]+)$') or ''):gsub('^%-', '') end

  local function profile_of(label)
    for _, a in ipairs(S.accounts()) do
      if a.label == label then return a.profile end
    end
    return nil
  end

  -- The pane to type into: the active one, or the last grid pane when the sidebar or the
  -- overview dock has focus.
  local function target_pane(window, pane)
    local is_dock = require('sidebar').is_dock
    if not is_dock(pane:pane_id()) then return pane end
    local tab = window:active_tab()
    local key = tostring(tab:tab_id())
    local last = ((wezterm.GLOBAL.sidebar or {})[key] or {}).last
      or ((wezterm.GLOBAL.overview or {})[key] or {}).last
    local first
    for _, p in ipairs(tab:panes()) do
      if not is_dock(p:pane_id()) then
        if p:pane_id() == last then return p end
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

  local function shquote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end

  -- Runs an omp-switch subcommand and returns its JSON answer.
  local function omp_switch(args)
    local cmd = { SWITCH_BIN }
    for _, a in ipairs(args) do cmd[#cmd + 1] = tostring(a) end
    local _, out = wezterm.run_child_process(cmd)
    local ok, res = pcall(wezterm.json_parse, out or '')
    if ok and type(res) == 'table' then return res end
    return { state = 'error', reason = 'omp-switch gave no answer' }
  end

  local function foreground_name(pane)
    local ok, info = pcall(function() return pane:get_foreground_process_info() end)
    if not ok or not info then return nil end
    return basename(info.name or (info.argv or {})[1])
  end

  -- Waits until `pane` shows a shell again, calling done(true) then, or done(false) after
  -- EXIT_WAIT seconds. Never kills anything.
  local function wait_for_shell(pane, done)
    local waited = 0
    local function poll()
      if SHELLS[foreground_name(pane) or ''] then return done(true) end
      waited = waited + EXIT_POLL
      if waited >= EXIT_WAIT then return done(false) end
      wezterm.time.call_after(EXIT_POLL, poll)
    end
    wezterm.time.call_after(EXIT_POLL, poll)
  end

  -- Moves the chat of the omp running in `target` from `from` to `profile`:
  --   1. omp-switch locate finds the session (nothing is touched yet);
  --   2. omp is asked to quit with Ctrl+D and given EXIT_WAIT seconds; if it stays (a draft
  --      in the prompt, a turn still running) the switch is abandoned and omp keeps running;
  --   3. omp-switch transfer copies the finished session, its artifacts and blobs into the
  --      other profile (never overwriting, never touching the first profile's files);
  --   4. `omp --profile=<to> --resume <copy>` continues the same chat. No session yet: a
  --      fresh omp under the new profile.
  local function switch_account(window, target, info, from, profile, label)
    local busy = wezterm.GLOBAL.omp_switching or {}
    wezterm.GLOBAL.omp_switching = busy
    local id = tostring(target:pane_id())
    if busy[id] and os.time() - busy[id] < 20 then
      return toast(window, 'An account switch is already running in this pane')
    end

    local loc = omp_switch { 'locate', '--pid', info.pid, '--from', from }
    if loc.state == 'error' then
      return toast(window, 'Account switch aborted, omp keeps running: ' .. tostring(loc.reason))
    end
    busy[id] = os.time()

    local function finish(ok, msg)
      busy[id] = nil
      toast(window, msg)
      return ok
    end

    local function resume_in_shell()
      local cmd = 'omp --profile=' .. profile
      if loc.state == 'ok' then
        local moved = omp_switch { 'transfer', '--session', loc.session, '--from', from, '--to', profile }
        if moved.state ~= 'ok' then
          return finish(false, string.format('omp exited but the chat could not be moved: %s', tostring(moved.reason)))
        end
        cmd = cmd .. ' --resume ' .. shquote(moved.session)
        -- A shell that is not where omp ran would resume the chat in the wrong directory.
        local cwd = target:get_current_working_dir()
        local here = cwd and (cwd.file_path or tostring(cwd)) or nil
        if loc.cwd and here and here:gsub('/+$', '') ~= loc.cwd:gsub('/+$', '') then
          cmd = 'cd ' .. shquote(loc.cwd) .. ' && ' .. cmd
        end
      end
      target:send_text(cmd .. '\r')
      return finish(true, string.format('omp moved to account %s (%s)', label, profile))
    end

    -- A turn in progress is interrupted first (Escape); an idle prompt ignores it.
    if info.working then
      target:send_text('\x1b')
      wezterm.time.call_after(0.4, function() target:send_text(EXIT_KEY) end)
    else
      target:send_text(EXIT_KEY)
    end
    wait_for_shell(target, function(exited)
      if not exited then
        return finish(false, 'omp did not quit (unsent text in its prompt?); account not switched, omp keeps running')
      end
      resume_in_shell()
    end)
  end

  local function start_omp(window, pane, label)
    local profile = profile_of(label)
    if not profile then
      return toast(window, 'No omp profile is paired with account ' .. tostring(label)
        .. '; choose one in the settings panel')
    end
    local target = target_pane(window, pane)
    local info = target and target:get_foreground_process_info()
    if not info then return toast(window, 'Cannot tell what is running in the pane') end
    local name = basename(info.name or (info.argv or {})[1])
    if name == 'omp' then
      local running = running_profile(info)
      if running == profile then return toast(window, 'omp is already running with account ' .. label) end
      if not running or not profile then
        return toast(window, 'Cannot tell which account omp is running with')
      end
      local kind = api.pane_kind and api.pane_kind(target:pane_id())
      local function go(working)
        info.working = working
        switch_account(window, target, info, running, profile, label)
      end
      if kind == 'working' or kind == 'busy' or kind == 'attention' then
        require('picker').open(window, target, {
          title = 'Switch account', crumb = label,
          confirm = {
            text = string.format('omp is %s in this pane. Switch it to %s anyway? %s, omp quits and the chat '
              .. 'resumes under %s.', kind == 'attention' and 'waiting for an answer' or 'working', label,
              kind == 'attention' and 'The question is left unanswered' or 'Its turn is interrupted', label),
            yes = 'Yes, switch now', no = 'No, keep omp running', danger = true,
          },
        }, function(_, _, id)
          if id == 'yes' then go(true) end
        end)
        return
      end
      return go(false)
    end
    if not SHELLS[name] then
      return toast(window, string.format('%s is running in this pane, not a shell prompt', name))
    end
    target:send_text('omp --profile=' .. profile .. '\r')
  end
  api.start_omp = start_omp

  -- The parent hands over agentstatus' per-pane state lookup (as.pane_kind) so a switch can
  -- ask before interrupting a working agent.
  function api.set_pane_kind(fn) api.pane_kind = fn end

  -- Label of the account whose omp runs in the pane the user is working in (the focused pane,
  -- or the last grid pane while the sidebar or dock has focus), nil when that pane runs no omp
  -- or its profile pairs with no account. Read live from the process, so moving focus to a
  -- pane under another profile moves the header highlight within one status tick.
  function api.active(window)
    local ok, pane = pcall(function() return window:active_pane() end)
    if not ok or not pane then return nil end
    local target = target_pane(window, pane)
    if not target then return nil end
    local okf, info = pcall(function() return target:get_foreground_process_info() end)
    if not okf or not info or basename(info.name or (info.argv or {})[1]) ~= 'omp' then return nil end
    local profile = running_profile(info)
    if not profile then return nil end
    for _, a in ipairs(S.accounts()) do
      if a.profile == profile then return a.label end
    end
    return nil
  end

  -- Alt+q: the picker doubles as the account list; Enter, a click or the account's digit (the
  -- LEADER+u one) starts omp with it in the pane, or moves the running omp over.
  api.select = wezterm.action_callback(function(window, pane)
    local now = os.time()
    local active = api.active(window)
    local index = {}
    for i, a in ipairs(S.accounts()) do index[a.label] = i end
    local rows = {}
    for _, e in ipairs(load_entries(now)) do
      local s = summarize(e, now)
      rows[#rows + 1] = picker_row(s, index[s.label] or 99, now, active)
    end
    require('picker').open(window, pane, {
      title = 'Claude quota', crumb = #rows .. (#rows == 1 and ' account' or ' accounts'),
      description = 'Starts omp with the account in this pane, or moves the omp running here to it.',
      verb = 'start', rows = rows, empty = 'No quota data cached yet',
    }, function(win, p, id)
      start_omp(win, p, id)
    end)
  end)

  -- Exposed for tests of the pure model.
  api.model = chip_model

  if wezterm.GLOBAL.claudequota_visible == nil then wezterm.GLOBAL.claudequota_visible = true end
  config.keys = config.keys or {}
  local popup = S.key('quota_popup', 'ALT+q')
  table.insert(config.keys, { key = popup.key, mods = popup.mods, action = api.select })
  -- LEADER+u then 1..9 starts omp for the n-th account (main / 2 / private today) without
  -- the popup. The key is resolved when pressed, so the order follows the account list.
  config.key_tables = config.key_tables or {}
  local account_keys = {}
  for i = 1, math.min(#S.accounts(), 9) do
    account_keys[#account_keys + 1] = {
      key = tostring(i),
      action = wezterm.action_callback(function(w, p)
        local a = S.accounts()[i]
        if a then start_omp(w, p, a.label) else toast(w, 'No account number ' .. i) end
      end),
    }
  end
  account_keys[#account_keys + 1] = { key = 'Escape', action = wezterm.action.PopKeyTable }
  config.key_tables.omp_account = account_keys
  local launch = S.key('quota_launch', 'LEADER+u')
  table.insert(config.keys, {
    key = launch.key,
    mods = launch.mods,
    action = wezterm.action.ActivateKeyTable {
      name = 'omp_account', one_shot = true, timeout_milliseconds = 4000,
    },
  })
  local toggle = S.key('quota_toggle', 'LEADER+q')
  table.insert(config.keys, {
    key = toggle.key,
    mods = toggle.mods,
    action = wezterm.action_callback(function()
      wezterm.GLOBAL.claudequota_visible = not api.visible()
    end),
  })

  return api
end
