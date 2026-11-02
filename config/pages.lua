-- Pages: the window's page switcher in the header, [Canvas] [Bots]. Canvas is the window as it always
-- was (tabs, the pane grid, bar, dock); Bots is one fixed page over the active tab, the phone's Bots page
-- (Briefing, the list of bots, a conversation with each) shown in a web pane of the Hivegrid fork. It is no
-- tab and no pane of the grid: it covers the tab that was active (a tab overlay, like the panels) and goes away
-- with a click on Canvas, the same key again, or a click on another tab.
-- Wire (after web): local pages = require('pages')(config, wezterm, { web = web,
--                                                                   panel = function(window, pane, name, extra) end })
--   pages.show_bots(window, pane[, { select = <bot id>, new = true }])   open the Bots page
--   pages.show_canvas(window, pane)                                      back to the canvas
--   pages.toggle_bots(window, pane)                                      LEADER+t: Bots, or back to Canvas
--   pages.current(window)  -> 'canvas' | 'bots'
--   pages.tiers(unseen)    -> header group P, richest first as { items, w } (clicks: page:canvas, page:bots)
--   pages.sync(window)     -> status tick: forgets the Bots page when its tab is no longer in front
-- The page needs the web pane (theme.has_web), a Chromium-family browser (web.has_chrome) and a local
-- hivegrid-remote. `hivegrid-bots-page ensure` (bin/hivegrid_botspage.py) starts the server when it is down
-- and writes the one-shot page that sends the web pane to it with the access token (never in a command line or
-- a visible address); the result is read from $XDG_RUNTIME_DIR/hivegrid-bots-page.json. Anything that is
-- missing or fails opens the Bots panel (`hivegrid-panel bots`, the terminal page) instead, with the reason in a
-- toast. Without the fork nothing here uses a web pane and no Lua error is raised.
return function(_config, wezterm, deps)
  deps = deps or {}
  local theme = require 'theme'
  local P = theme.P
  local act = wezterm.action
  local web = deps.web

  local api = { enabled = theme.has_web and web ~= nil and web.enabled == true }

  local HOME = os.getenv('HOME') or ''
  local BIN = HOME .. '/.local/bin/hivegrid-bots-page'
  local PROFILE = 'hivegrid-bots' -- its own browser profile: the Bots page never shares cookies with a web pane
  local POLL, GIVE_UP, STATUS_FRESH = 0.25, 15, 60
  local OPEN_LIFE = 20 -- seconds the one-shot page stays on disk

  -- The same paths as status_path() / open_path() in bin/hivegrid_botspage.py.
  local uid
  local function status_path()
    local rt = os.getenv('XDG_RUNTIME_DIR')
    if rt and rt:sub(1, 1) == '/' then return rt:gsub('/+$', '') .. '/hivegrid-bots-page.json' end
    if not uid then
      local f = io.open('/proc/self/status', 'r')
      local text = f and f:read('a') or ''
      if f then f:close() end
      uid = text:match('Uid:%s*(%d+)') or '0'
    end
    return '/tmp/hivegrid-bots-page-' .. uid .. '.json'
  end

  local function read_status()
    local f = io.open(status_path(), 'r')
    if not f then return nil end
    local ok, text = pcall(f.read, f, 65536)
    f:close()
    if not ok or not text then return nil end
    local parsed, doc = pcall(wezterm.json_parse, text)
    return parsed and type(doc) == 'table' and doc or nil
  end

  -- State per window, in wezterm.GLOBAL (it survives a config reload): { page, tab, overlay }.
  local function wid(window) return tostring(window:window_id()) end
  local function get(window)
    local all = wezterm.GLOBAL.pages -- a table in the stub, a proxy userdata in WezTerm: index it, never type-check it
    local rec = all and all[wid(window)]
    if not rec then return { page = 'canvas' } end
    return { page = rec.page, tab = rec.tab, overlay = rec.overlay, pending = rec.pending, started = rec.started, port_url = rec.port_url }
  end
  local function put(window, rec)
    local all = wezterm.GLOBAL.pages or {}
    all[wid(window)] = rec
    wezterm.GLOBAL.pages = all
  end

  local cli_bin = wezterm.executable_dir .. '/wezterm'
  do
    local f = io.open(cli_bin, 'rb')
    if f then f:close() else cli_bin = 'wezterm' end
  end

  -- Ends the page's web pane. `wezterm cli` runs in the background (never inside an event handler's
  -- own thread of work) and is aimed at this GUI's mux through a terminal pane of the tab.
  local function kill(pane_id, anchor)
    if not pane_id then return end
    local cmd = {}
    local sock = anchor and theme.mux_socket(anchor)
    if sock then cmd = { 'env', 'WEZTERM_UNIX_SOCKET=' .. sock } end
    for _, a in ipairs { cli_bin, 'cli', 'kill-pane', '--pane-id', tostring(pane_id) } do cmd[#cmd + 1] = a end
    pcall(wezterm.background_child_process, cmd)
  end

  local function tab_anchor(window)
    local ok, tab = pcall(function() return window:active_tab() end)
    local ok2, pane = pcall(function() return ok and tab and tab:active_pane() end)
    return ok2 and pane or nil
  end

  local function toast(window, msg)
    pcall(window.toast_notification, window, 'Hivegrid', msg, nil, 6000)
  end

  -- Pages ------------------------------------------------------------------------------------

  -- 'bots' while the Bots page is the front of this window: its tab is the active tab and the pane the window
  -- reports as active is a web pane that is not one of the tab's own.
  function api.current(window)
    local rec = get(window)
    if rec.page ~= 'bots' or not rec.overlay then return 'canvas' end
    local ok, tab = pcall(function() return window:active_tab() end)
    if not ok or not tab or tab:tab_id() ~= rec.tab then return 'canvas' end
    local okp, ap = pcall(function() return window:active_pane() end)
    if not okp or not ap or ap:pane_id() ~= rec.overlay then return 'canvas' end
    return 'bots'
  end

  function api.show_canvas(window)
    local rec = get(window)
    if rec.page == 'bots' then
      kill(rec.overlay, tab_anchor(window))
      put(window, { page = 'canvas' })
    end
  end

  -- Run when the status tick sees a Bots page whose tab was left (a click on another tab) or whose pane is gone.
  function api.sync(window)
    local rec = get(window)
    if rec.page ~= 'bots' or not rec.overlay then return end
    if rec.pending then return end
    local ok, tab = pcall(function() return window:active_tab() end)
    local gone = not wezterm.mux.get_pane(rec.overlay)
    if gone then
      put(window, { page = 'canvas' })
    elseif ok and tab and tab:tab_id() ~= rec.tab then
      -- Another tab came to the front: the page leaves with it (its overlay would otherwise wait on the old tab).
      kill(rec.overlay, tab_anchor(window))
      put(window, { page = 'canvas' })
    end
  end

  local function fallback(window, pane, why, extra)
    if why then toast(window, why) end
    if deps.panel then deps.panel(window, pane, 'bots', extra) end
  end

  local function valid_id(id)
    return type(id) == 'string' and #id >= 3 and #id <= 32 and id:match('^[a-z0-9-]+$') ~= nil
  end

  local function route_for(opts)
    if opts and valid_id(opts.select) then return '/bots/' .. opts.select end
    return '/bots'
  end

  local function panel_extra(opts)
    if opts and opts.new then return { '--new' } end
    if opts and valid_id(opts.select) then return { '--select', opts.select } end
    return nil
  end

  -- Opens the Bots page over the active tab once `hivegrid-bots-page ensure` answered.
  local function open_overlay(window, pane, doc, tab_id)
    local url = type(doc.open) == 'string' and doc.open:sub(1, 1) == '/' and not doc.open:find('[%c%s]') and doc.open or nil
    if not url then return fallback(window, pane, 'Bots page: no page to open', nil) end
    local ok, err = pcall(window.perform_action, window, act.ShowOverlayCommand {
      args = { 'hivegrid:web', 'file://' .. url },
      cwd = HOME,
      set_environment_variables = { HIVEGRID_WEB_PROFILE = PROFILE },
    }, pane)
    if not ok then return fallback(window, pane, 'Bots page: ' .. tostring(err), nil) end
    put(window, { page = 'bots', tab = tab_id, pending = true, started = os.time() })
    wezterm.time.call_after(OPEN_LIFE, function() os.remove(url) end)
    -- The overlay becomes the window's active pane a moment later: that is its id.
    local tries = 0
    local function find()
      tries = tries + 1
      local okp, ap = pcall(function() return window:active_pane() end)
      if okp and ap and theme.is_web(ap) then
        put(window, { page = 'bots', tab = tab_id, overlay = ap:pane_id(), port_url = doc.url })
        -- The page is the whole content: no browser header or address row over it.
        web.command(ap, 'header', 'hide')
        return
      end
      if tries < 40 then return wezterm.time.call_after(0.1, find) end
      put(window, { page = 'canvas' })
    end
    wezterm.time.call_after(0.1, find)
  end

  function api.show_bots(window, pane, opts)
    opts = opts or {}
    if not api.enabled or not web.has_chrome then
      local why = api.enabled and 'Bots page: no Chrome or Chromium found, showing the terminal page' or nil
      return fallback(window, pane, why, panel_extra(opts))
    end
    local ok, tab = pcall(function() return window:active_tab() end)
    if not ok or not tab then return end
    local rec = get(window)
    -- Already there: a bot picked elsewhere (the dock) is only a new address in the open page.
    if api.current(window) == 'bots' then
      if valid_id(opts.select) then
        local overlay = wezterm.mux.get_pane(rec.overlay)
        local base = type(rec.port_url) == 'string' and rec.port_url:match('^http://127%.0%.0%.1:%d+/$')
        if overlay and base then web.navigate(overlay, base .. '#/bots/' .. opts.select) end
      end
      return
    end
    if rec.page == 'bots' and rec.pending and os.time() - (rec.started or 0) < GIVE_UP then return end
    local since = os.time()
    local tab_id = tab:tab_id()
    put(window, { page = 'bots', tab = tab_id, pending = true, started = since })
    local argv = { BIN, 'ensure', '--route', route_for(opts) }
    if opts.new then argv[#argv + 1] = '--new' end
    local started, err = pcall(wezterm.background_child_process, argv)
    if not started then
      put(window, { page = 'canvas' })
      return fallback(window, pane, 'Bots page: could not run hivegrid-bots-page (' .. tostring(err) .. ')', panel_extra(opts))
    end
    local function poll()
      local now = os.time()
      local doc = read_status()
      local fresh = doc and tonumber(doc.ts) and tonumber(doc.ts) >= since - 1 and now - tonumber(doc.ts) <= STATUS_FRESH
      if fresh then
        if doc.ok == true then return open_overlay(window, pane, doc, tab_id) end
        put(window, { page = 'canvas' })
        local msg = type(doc.message) == 'string' and doc.message:gsub('%c', ' ') or 'the local server is not available'
        return fallback(window, pane, 'Bots page: ' .. msg, panel_extra(opts))
      end
      if now - since >= GIVE_UP then
        put(window, { page = 'canvas' })
        return fallback(window, pane, 'Bots page: the local server did not answer in ' .. GIVE_UP .. ' s', panel_extra(opts))
      end
      wezterm.time.call_after(POLL, poll)
    end
    wezterm.time.call_after(POLL, poll)
  end

  function api.toggle_bots(window, pane)
    if api.current(window) == 'bots' then return api.show_canvas(window, pane) end
    return api.show_bots(window, pane)
  end

  -- Header ------------------------------------------------------------------------------------

  -- The switcher as one group: the page in front is a raised chip, the other muted. `unseen` is the Briefing's
  -- unseen count (nil or 0: none), shown on the Bots chip. Richest first: with the count, then without.
  function api.tiers(unseen, current)
    if not api.enabled then return {} end
    current = current or 'canvas'
    local function chip(id, label, tail)
      local on = current == id
      local items = {
        { Background = { Color = on and P.raised or P.pill } },
        { Foreground = { Color = on and P.bright or P.muted } },
        { Attribute = { Intensity = on and 'Bold' or 'Normal' } },
        { Text = ' ' .. label .. ' ' },
      }
      if tail then
        theme.extend(items, { { Foreground = { Color = P.accent } }, { Text = tail .. ' ' } })
      end
      items[#items + 1] = 'ResetAttributes'
      return theme.click('page:' .. id, items)
    end
    local function group(tail)
      local items = chip('canvas', 'Canvas')
      theme.extend(items, { { Text = ' ' } })
      theme.extend(items, chip('bots', 'Bots', tail))
      return { items = items, w = theme.width(items) }
    end
    local out = {}
    if (tonumber(unseen) or 0) > 0 then out[#out + 1] = group('● ' .. math.floor(unseen)) end
    out[#out + 1] = group(nil)
    return out
  end

  return api
end
