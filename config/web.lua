-- Web pane: a browser in a pane of the grid (Hivegrid WezTerm fork only). Wire (parent):
--   local web = require('web')(config, wezterm, sb, layouts)
--   LEADER+w   web key table: l type a location in the pane's header (asks and opens a pane when
--              there is none), h show/hide the header, g sign in to Google in a plain Chrome window, n new web pane, b back, f forward, r reload,
--              R hard reload, s stop, y copy the URL, o open in the system browser, Esc leaves
--   The browser button of the sidebar bar: focuses the tab's web pane, else opens a web pane on
--   the home page (settings key web.home, about:blank by default) without asking; the bar shows
--   it only when a browser is found (sb.set_web_button, see sidebar.lua)
--   A click on an http(s) link in a terminal pane opens it in the tab's web pane (settings
--   key web.links = "pane", the default, and only when Chrome or Chromium is found: probed once
--   per config load; "system" leaves links to the system browser)
-- A web pane is spawned by the fork's `hivegrid:web <url>` sentinel program (profile and
-- private mode through the HIVEGRID_WEB_PROFILE / HIVEGRID_WEB_PRIVATE environment) and
-- driven by control sequences written to the pane, `ESC _ hgweb ; <command> [; <arg>] ESC \`
-- (navigate;URL, back, forward, reload, hardreload, stop, restart, copy-url, zoom;PERCENT,
-- header;toggle|show|hide, location, devtools). The fork marks it with the user var
-- hivegrid_web=1; it is a grid pane like a
-- terminal (numbered, split, zoomed, arranged), only broadcast skips it.
-- Without the fork (theme.has_web false) nothing is registered: no key table, no binding, no
-- open-uri handler, and the returned api only reports `enabled = false`.
-- api: { enabled, is_web, has_chrome, normalize_url, open, navigate, command, prompt_new, prompt_location,
--        browser_button }
return function(config, wezterm, sb, layouts)
  local theme = require 'theme'
  local S = require('settings')(config, wezterm)
  local act = wezterm.action

  local api = { enabled = theme.has_web, is_web = theme.is_web }

  -- Pure: what a typed location means --------------------------------------------------
  local SCHEMES = { http = true, https = true, file = true, data = true }

  local function is_local_host(host)
    host = host:lower()
    return host == 'localhost' or host:match('%.localhost$') ~= nil
      or host:match('^%d+%.%d+%.%d+%.%d+$') ~= nil or host:match('^%[.*%]$') ~= nil
  end

  local function url_encode(s)
    return (s:gsub('[^%w%-_.~]', function(c) return string.format('%%%02X', c:byte()) end))
  end

  -- The URL a typed location stands for, or nil and the reason. "https://x" and the schemes
  -- http, https, file and data, and exactly about:blank, are kept as typed; "localhost:3000", "127.0.0.1" and
  -- other local hosts get http://, any other host name ("example.com/a") https://; anything
  -- else is a search when `search` (a template holding %s) is given. Other schemes
  -- (javascript:, chrome:, devtools:, about:flags, ...) are refused: the allowlist is the same
  -- in the Rust layer (hivegrid-web urls.rs) and bin/hivegrid_agents.py.
  function api.normalize_url(text, search)
    text = (tostring(text or ''):gsub('^%s+', ''):gsub('%s+$', ''))
    if text == '' then return nil, 'nothing to open' end
    local spaced = text:find('%s') ~= nil
    local hostport = text:match('^[^/?#:%s]+:%d+$') or text:match('^[^/?#:%s]+:%d+[/?#]')
      or text:match('^%[[%x:.]+%]:%d+$') or text:match('^%[[%x:.]+%]:%d+[/?#]')
    local scheme = text:match('^(%a[%w+.-]*):')
    if scheme and not hostport and not spaced then
      if SCHEMES[scheme:lower()] or text:lower() == 'about:blank' then return text end
      return nil, 'cannot open a ' .. scheme .. ': address in a web pane'
    end
    if not spaced then
      local authority = text:match('^([^/?#]+)') or ''
      local host = authority:gsub(':%d+$', '')
      local plain = host:match('^[%w.-]+$') ~= nil
      if is_local_host(host) or (plain and host:match('%.%a%a+$') ~= nil) then
        return (is_local_host(host) and 'http://' or 'https://') .. text
      end
    end
    if type(search) == 'string' and search:find('%s', 1, true) then
      return (search:gsub('%%s', function() return url_encode(text) end))
    end
    return nil, 'not a web address, and no search engine is set (Settings, Web)'
  end

  -- Stock WezTerm: no web pane exists, so nothing is registered and every call is a no-op.
  if not api.enabled then
    function api.open() return false end
    function api.navigate() return false end
    function api.command() return false end
    function api.prompt_new() end
    function api.prompt_location() end
    function api.browser_button() end
    return api
  end

  -- The browser, probed once per config load with io.open (no child process), by the fork's own
  -- precedence (mux/src/webpane.rs browser_choice, hivegrid-web browser.rs find_chrome):
  -- HIVEGRID_CHROME when set (then it alone counts), else the browser chosen in the settings
  -- (web.browser; a missing one does not fall back), else /opt/google/chrome/chrome and the
  -- usual names on PATH.
  local CHROME_NAMES = { 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser' }

  local function is_file(path)
    local f = io.open(path, 'rb')
    if not f then return false end
    local ok = f:read(0) ~= nil
    f:close()
    return ok
  end

  local function find_chrome()
    local env = os.getenv('HIVEGRID_CHROME')
    if env and env ~= '' then return is_file(env), 'HIVEGRID_CHROME=' .. env end
    if S.web.browser ~= 'auto' then return is_file(S.web.browser), 'the browser chosen in Settings, ' .. S.web.browser end
    if is_file('/opt/google/chrome/chrome') then return true end
    for dir in (os.getenv('PATH') or ''):gmatch('[^:]+') do
      for _, name in ipairs(CHROME_NAMES) do
        if is_file(dir .. '/' .. name) then return true end
      end
    end
    return false
  end

  local has_chrome, chosen = find_chrome()
  api.has_chrome = has_chrome
  local NO_CHROME = chosen and (chosen .. ' is not a file') or 'Chrome or Chromium not found'
  if sb and sb.set_web_button then sb.set_web_button(has_chrome) end

  local APC = { start = '\27_hgweb;', stop = '\27\\' }

  local function toast(window, msg)
    window:toast_notification('Hivegrid', msg, nil, 3000)
  end

  -- Control channel ----------------------------------------------------------------------
  -- Sends one control command to a web pane (control characters of `arg` are dropped: they
  -- would end the sequence early).
  function api.command(pane, name, arg)
    local text = APC.start .. name
    if arg ~= nil then text = text .. ';' .. tostring(arg):gsub('%c', '') end
    return (pcall(pane.send_text, pane, text .. APC.stop))
  end

  function api.navigate(pane, url)
    return api.command(pane, 'navigate', url)
  end

  -- The pane a tab's web commands go to: the web pane last opened or used there while it
  -- still exists, else any web pane of the tab.
  local function remember(tab_id, pane_id)
    local g = wezterm.GLOBAL.web or {}
    local last = g.last or {}
    last[tostring(tab_id)] = pane_id
    g.last = last
    wezterm.GLOBAL.web = g
  end

  local function tab_of(window, pane)
    local ok, tab = pcall(function() return pane:tab() end)
    return ok and tab or window:active_tab()
  end

  local function web_of(tab)
    local panes = tab:panes()
    local g = wezterm.GLOBAL.web
    local want = g and g.last and g.last[tostring(tab:tab_id())]
    local found
    for _, p in ipairs(panes) do
      if theme.is_web(p) then
        if p:pane_id() == want then return p end
        found = found or p
      end
    end
    return found
  end

  -- Opening ---------------------------------------------------------------------------------
  local CELL_ASPECT = 2.1 -- cell height / cell width, the rule of the arrange and the hive's splits

  local function env_of(profile, private)
    local env = { HIVEGRID_WEB_PROFILE = profile }
    if private then env.HIVEGRID_WEB_PRIVATE = '1' end
    return env
  end

  -- Shows `url` in a web pane. opts: profile (default: settings web.profile), private
  -- (an ephemeral profile), where = 'tab' (a new tab), new (never reuse a pane). Without
  -- those the tab's web pane is navigated, else a web pane is split off the last grid pane
  -- (right when it is wide, else below: the hive's rule). Nothing re-tiles the grid after the
  -- split: the new pane stays where the split put it. Returns true, or false and a reason.
  function api.open(window, pane, url, opts)
    opts = opts or {}
    if not has_chrome then return false, NO_CHROME end
    local profile = opts.profile or S.web.profile
    local args = { 'hivegrid:web', url }
    if opts.where == 'tab' then
      local ok, err = pcall(window.perform_action, window, act.SpawnCommandInNewTab {
        args = args, set_environment_variables = env_of(profile, opts.private),
      }, pane)
      if not ok then return false, tostring(err) end
      return true
    end
    local tab = tab_of(window, pane)
    if not tab then return false, 'no tab' end
    if not (opts.new or opts.private or opts.profile) then
      local existing = web_of(tab)
      if existing then
        api.navigate(existing, url)
        remember(tab:tab_id(), existing:pane_id())
        existing:activate()
        return true
      end
    end
    local target, w, h = pane, 80, 24
    local grid = layouts and layouts.grid_of(tab) or {}
    local order = layouts and layouts.in_order(grid) or {}
    local last = order[#order]
    if last then target, w, h = last.pane, last.width, last.height end
    local ok, new = pcall(target.split, target, {
      direction = w >= h * CELL_ASPECT and 'Right' or 'Bottom',
      args = args,
      set_environment_variables = env_of(profile, opts.private),
    })
    if not ok or not new then return false, tostring(new) end
    remember(tab:tab_id(), new:pane_id())
    new:activate()
    return true, new
  end

  -- Prompts ---------------------------------------------------------------------------------
  -- The location an empty answer stands for: settings web.home, about:blank when that is no
  -- usable location.
  local function home_url()
    return api.normalize_url(S.web.home) or 'about:blank'
  end

  -- Esc is nil, an empty line is ignored unless `home`: then it opens the home page.
  local function prompt(window, pane, description, done, home)
    window:perform_action(act.PromptInputLine {
      description = description,
      -- The callback's own pane is the prompt's overlay: the one it was opened from is kept.
      action = wezterm.action_callback(function(win, _, line)
        if line == nil then return end
        if line:match('^%s*$') then
          if home then done(win, home_url()) end
          return
        end
        local url, why = api.normalize_url(line, S.web.search)
        if not url then
          toast(win, why)
          return
        end
        done(win, url)
      end),
    }, pane)
  end

  -- Asks for a location and opens it in a new web pane (opts.where = 'tab': a new tab); Enter on
  -- an empty line opens the home page.
  function api.prompt_new(window, pane, opts)
    opts = opts or {}
    if not has_chrome then
      toast(window, NO_CHROME)
      return
    end
    local what = opts.where == 'tab' and 'web tab' or 'web pane'
    prompt(window, pane, 'New ' .. what .. ': URL or search (Enter alone opens ' .. home_url() .. ', Esc cancels)', function(win, url)
      local o = { where = opts.where, new = true }
      local ok, err = api.open(win, pane, url, o)
      if not ok then toast(win, 'Could not open the web pane: ' .. tostring(err)) end
    end, true)
  end

  -- The browser button of the sidebar bar: the tab's web pane takes the focus (the same
  -- activation as every other focus change: a zoom that hides it ends, the chrome stays), a tab
  -- without one opens a web pane on the home page at once (settings web.home); the header row
  -- of the pane is where the location is typed.
  function api.browser_button(window, pane)
    local tab = tab_of(window, pane)
    local existing = tab and web_of(tab)
    if existing then
      remember(tab:tab_id(), existing:pane_id())
      existing:activate()
      return
    end
    if not has_chrome then
      toast(window, NO_CHROME)
      return
    end
    local ok, err = api.open(window, pane, home_url(), { new = true })
    if not ok then toast(window, 'Could not open the web pane: ' .. tostring(err)) end
  end

  -- The web pane a command acts on: this pane when it is one, else the tab's.
  local function target_of(window, pane)
    if theme.is_web(pane) then return pane end
    local tab = window:active_tab()
    return tab and web_of(tab)
  end

  -- Asks for a location and opens a new web pane with it (the key table's `l` types in the
  -- header of an existing web pane instead; this is the path when the tab has none).
  function api.prompt_location(window, pane)
    prompt(window, pane, 'Location: URL or search (Enter opens, Esc cancels)', function(win, url)
      local target = target_of(win, pane)
      if target then
        api.navigate(target, url)
        target:activate()
        return
      end
      if not has_chrome then
        toast(win, NO_CHROME)
        return
      end
      local ok, err = api.open(win, pane, url)
      if not ok then toast(win, 'Could not open the web pane: ' .. tostring(err)) end
    end)
  end

  -- Key table ---------------------------------------------------------------------------------
  local function on_web(fn)
    return wezterm.action_callback(function(window, pane)
      local target = target_of(window, pane)
      if not target then
        toast(window, 'No web pane in this tab (web keys: n opens one)')
        return
      end
      fn(window, target)
    end)
  end

  local function send(name, arg)
    return on_web(function(_, target) api.command(target, name, arg) end)
  end

  local function once(action)
    return act.Multiple { act.PopKeyTable, action }
  end

  config.key_tables = config.key_tables or {}
  config.key_tables.web = {
    {
      key = 'l',
      action = once(wezterm.action_callback(function(window, pane)
        local target = target_of(window, pane)
        if target then
          api.command(target, 'location')
          target:activate()
        else
          api.prompt_location(window, pane)
        end
      end)),
    },
    { key = 'h', action = once(send 'header;toggle') },
    {
      -- Google refuses to sign in to a browser that software controls (the pane's is): a plain
      -- Chrome window on the pane's profile does the sign-in (bin/hivegrid-web-signin). The tab
      -- stays open when it fails, so its reason can be read.
      key = 'g',
      action = once(wezterm.action_callback(function(window, pane)
        local bin = wezterm.home_dir .. '/.local/bin/hivegrid-web-signin'
        local ok, err = pcall(window.perform_action, window, act.SpawnCommandInNewTab {
          args = { 'bash', '-c', bin .. ' || { printf "\\nPress Enter to close "; read -r _; }' },
        }, pane)
        if not ok then toast(window, 'Could not start the sign-in: ' .. tostring(err)) end
      end)),
    },
    { key = 'n', action = once(wezterm.action_callback(function(window, pane) api.prompt_new(window, pane) end)) },
    -- Tabs inside the web pane (one browser pane, several pages): t opens one and types its
    -- address (without a web pane it opens the pane), x closes the active one, ] and [ move,
    -- 1-9 pick. Ctrl+T, Ctrl+W, Ctrl+Tab and Ctrl+1-9 do the same inside the pane.
    {
      key = 't',
      action = once(wezterm.action_callback(function(window, pane)
        local target = target_of(window, pane)
        if target then
          api.command(target, 'tab;new')
          target:activate()
        else
          api.prompt_new(window, pane)
        end
      end)),
    },
    { key = 'x', action = once(send 'tab;close') },
    { key = ']', action = once(send 'tab;next') },
    { key = '[', action = once(send 'tab;prev') },
    { key = 'b', action = once(send 'back') },
    { key = 'f', action = once(send 'forward') },
    { key = 'r', action = once(send 'reload') },
    { key = 'R', mods = 'SHIFT', action = once(send 'hardreload') },
    { key = 's', action = once(send 'stop') },
    { key = 'y', action = once(send 'copy-url') },
    {
      key = 'o',
      action = once(on_web(function(window, target)
        local ok, cwd = pcall(target.get_current_working_dir, target)
        local url = ok and theme.web_url(cwd)
        if url and (url.scheme == 'http' or url.scheme == 'https') then
          wezterm.open_with(url.href)
        else
          toast(window, 'The system browser opens http(s) pages only')
        end
      end)),
    },
    { key = 'Escape', action = act.PopKeyTable },
  }

  for n = 1, 9 do
    table.insert(config.key_tables.web, {
      key = tostring(n),
      action = once(send('tab;select', n)),
    })
  end

  config.keys = config.keys or {}
  local leader = S.key('web_leader', 'LEADER+w')
  table.insert(config.keys, {
    key = leader.key, mods = leader.mods,
    action = act.ActivateKeyTable {
      name = 'web', one_shot = false, prevent_fallback = true, timeout_milliseconds = 4000,
    },
  })

  -- WezTerm's default bindings switch its own tabs with these chords. In a web pane they are the
  -- browser's tab keys (the pane handles them), everywhere else they keep that default.
  for _, chord in ipairs {
    { key = 'Tab', mods = 'CTRL', tab = 1 },
    { key = 'Tab', mods = 'CTRL|SHIFT', tab = -1 },
    { key = 'PageDown', mods = 'CTRL', tab = 1 },
    { key = 'PageUp', mods = 'CTRL', tab = -1 },
  } do
    table.insert(config.keys, {
      key = chord.key, mods = chord.mods,
      action = wezterm.action_callback(function(window, pane)
        if theme.is_web(pane) then
          window:perform_action(act.SendKey { key = chord.key, mods = chord.mods }, pane)
        else
          window:perform_action(act.ActivateTabRelative(chord.tab), pane)
        end
      end),
    })
  end

  -- Links ---------------------------------------------------------------------------------------
  -- A clicked http(s) link of a terminal pane of the active tab opens in the tab's web pane
  -- (returning false stops WezTerm's own open). Links inside a web pane stay in the browser;
  -- the sidebar, the dock and overlays (panels, pickers; no pane of the tab) are left to the
  -- system browser, and so is everything when the setting is "system", Chrome is not found or
  -- opening failed.
  wezterm.on('open-uri', function(window, pane, uri)
    if S.web.links ~= 'pane' or not has_chrome or type(uri) ~= 'string' or not uri:match('^https?://') then return end
    local id = pane:pane_id()
    if theme.is_web(pane) or (sb and sb.is_dock(id)) then return end
    local tab = window:active_tab()
    local here = false
    for _, p in ipairs(tab and tab:panes() or {}) do
      here = here or p:pane_id() == id
    end
    if not here then return end
    local ok, opened = pcall(api.open, window, pane, uri)
    if ok and opened then return false end
  end)

  return api
end
