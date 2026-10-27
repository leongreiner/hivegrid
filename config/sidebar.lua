-- Left sidebar: file explorer (yazi) or git view (hivegrid-git: every repository of the
-- directory, lazygit in the one you pick) docked beside a permanent
-- narrow bar pane. Wire: local sb = require('sidebar')(config, wezterm)
--   Ctrl+F explorer   Ctrl+G git view   LEADER+Tab hide/show, remembering the view
-- The bar is permanent: every tab gets one (see ensure_handle), with buttons for the
-- explorer, the git view, the overview dock and the settings panel, and a browser button on the
-- Hivegrid build when a browser is installed (config/web.lua).
-- Hivegrid build: [bar][view][grid...], the bar and the view one full-height column left of
-- the whole grid (tree: Split(Split(bar, view), grid)). The bar is a `--top-level` split;
-- the view is made by widening the bar by the view's width plus a divider and splitting the
-- bar `--right`, so the grid shrinks once and as a whole (the build resizes it in proportion,
-- so an even grid stays even). The view carries the user var hivegrid_fixed_width like the
-- bar, so the overview dock and zoom leave the column alone. Its width is 22% of the window
-- (28 to 60 columns) and follows window resizes, until a divider drag sets one: that width
-- stays for the tab, and tabs without a drag of their own take it too, both scaled with the
-- window width. Closing the view gives its columns straight back to the grid (the mux does it,
-- see wezterm/HIVEGRID.md: the bar keeps its width, no round trip through settle). Switching
-- the view, or the git view following another directory, replaces it in place (the new view
-- split off the old one, which then closes), so the grid does not move.
-- Stock WezTerm 20240203: [bar][view][leftmost pane...]: the view is an ordinary leaf split
-- of the leftmost grid pane, so a killed view's columns return to that pane. There
-- `--top-level` on a tab with 2+ panes leaves the pane tree (cells + 1) columns wider than
-- the window, so neither the bar nor the view uses it.
-- The bar script turns on mouse reporting, so WezTerm forwards clicks to it and
-- no mouse_bindings are needed; every default selection/link/bypass binding stays.
-- The script announces a left press on a button as the user var `sidebar_click`
-- (files, git, overview, settings or browser, plus a salt so repeated clicks are changes).
-- The explorer asks for a terminal (user var hivegrid_open_dir) or an editor on files
-- (hivegrid_open_file) in a new grid pane; see open_in_grid.
local M = {}

local function build(config, wezterm)
  config.keys = config.keys or {}
  config.mouse_bindings = config.mouse_bindings or {}

  local HANDLE_COLS, MIN_COLS, MAX_COLS, FRACTION, MIN_GRID = 5, 28, 60, 0.22, 20
  local MIN_PANE = 20 -- columns each grid column keeps when the view column takes its room
  local RUNTIME = os.getenv('XDG_RUNTIME_DIR') or '/tmp'
  local VIEWS = {
    files = { bin = wezterm.home_dir .. '/.local/bin/yazi' },
    git = { bin = wezterm.home_dir .. '/.local/bin/hivegrid-git' },
  }
  local theme = require 'theme'

  -- The bar pane runs bin/hivegrid-bar (buttons, highlights, hover pointer, clicks; see
  -- there) with the theme's colours. It reads the state file "<open|closed> <files|git>"
  -- that write_state keeps, and the flag file "<state file>.ov" overview.lua keeps while the
  -- dock is open, and announces a click as the user var sidebar_click.
  local P = theme.P
  local BAR_COMMAND = {
    wezterm.home_dir .. '/.local/bin/hivegrid-bar',
    '--surface', P.surface, '--raised', P.raised, '--dim', P.dim, '--bright', P.bright,
    '--accent', P.accent,
  }

  -- Pure layout logic: operates on plain rows, so it is testable with fake data. -------
  local function rows(infos)
    local out = {}
    for _, i in ipairs(infos) do
      out[#out + 1] = {
        id = i.pane_id or i.pane:pane_id(), left = i.left, top = i.top,
        width = i.width, height = i.height, active = i.is_active, pane = i.pane,
      }
    end
    return out
  end

  local function find(rs, id)
    for _, r in ipairs(rs) do
      if r.id == id then return r end
    end
  end

  -- Top-left pane among those `skip` does not exclude.
  local function leftmost(rs, skip)
    local best
    for _, r in ipairs(rs) do
      if not (skip and skip(r.id)) and (not best or r.left < best.left
          or (r.left == best.left and r.top < best.top)) then
        best = r
      end
    end
    return best
  end

  -- Pane the sidebar is split from: the handle's right neighbour once a handle exists
  -- (the leftmost pane then can be a taller pane under the handle), else the top-left pane.
  local function anchor_for(rs, rec, skip)
    local h = rec.handle and find(rs, rec.handle)
    for _, r in ipairs(h and rs or {}) do
      if not skip(r.id) and r.left == h.left + h.width + 1 and r.top == h.top then return r end
    end
    return leftmost(rs, skip)
  end

  local function total_width(rs)
    local total = 0
    for _, r in ipairs(rs) do total = math.max(total, r.left + r.width) end
    return total
  end

  -- Width of a new view on a window `total` columns wide: 22% of it, 28 to 60 columns.
  local function default_width(total)
    return math.max(MIN_COLS, math.min(MAX_COLS, math.floor(total * FRACTION)))
  end

  -- Stock layout: columns for the sidebar, or nil when the anchor pane cannot spare them.
  -- `saved` is the last width; the cap keeps MIN_GRID columns for the anchor itself.
  local function width_for(rs, anchor, needs_handle, saved)
    local room = anchor.width - (needs_handle and HANDLE_COLS + 1 or 0) - 1 - MIN_GRID
    local w = math.min(saved or default_width(total_width(rs)), room)
    if w >= 8 then return w end
  end

  -- Column layout: columns for the view, or nil when the grid cannot spare 8. The grid (the
  -- panes `skip` does not exclude) gives up the view's width and a divider and keeps
  -- MIN_PANE columns for each of its columns (the most panes side by side on any row), at
  -- least MIN_GRID in all. `own` is what an open view already has (its width and divider).
  local function column_width(rs, skip, saved, own)
    local x0, x1, across = math.huge, 0, 0
    for _, r in ipairs(rs) do
      if not skip(r.id) then
        x0, x1 = math.min(x0, r.left), math.max(x1, r.left + r.width)
        local n = 0
        for _, q in ipairs(rs) do
          if not skip(q.id) and q.top <= r.top and r.top < q.top + q.height then n = n + 1 end
        end
        across = math.max(across, n)
      end
    end
    if across == 0 then return nil end
    local room = (own or 0) + (x1 - x0) - 1 - math.max(MIN_GRID, across * MIN_PANE)
    local w = math.min(saved or default_width(total_width(rs)), room)
    if w >= 8 then return w end
  end

  -- Width the content pane should have: the saved one, capped so the pane right of it
  -- keeps MIN_GRID columns, floored at 8 when the window is too small for the saved width.
  local function content_target(rs, c, saved)
    if not saved then return nil end
    local a
    for _, r in ipairs(rs) do
      if r.left == c.left + c.width + 1 and r.top == c.top then a = r end
    end
    if not a then return nil end
    local w = math.max(8, math.min(saved, c.width + a.width - MIN_GRID))
    return math.max(1, math.min(w, c.width + a.width - 1))
  end

  -- The next resize that brings the sidebar panes to their intended widths, or nil.
  -- `target(rs, c)` is the width the view `c` should have (nil: leave it). The handle goes
  -- first: AdjustPaneSize on it only trades columns with the subtree beside it, so the
  -- content width is untouched by that step.
  local function next_step(rs, rec, target)
    local h = rec.handle and find(rs, rec.handle)
    if not h then return nil end
    if h.width ~= HANDLE_COLS then return { id = h.id, delta = HANDLE_COLS - h.width } end
    local c = rec.content and find(rs, rec.content)
    local w = c and target(rs, c)
    if w and w ~= c.width then return { id = c.id, delta = w - c.width } end
  end

  -- Drops ids of panes that no longer exist (killed, or quit with `q`). `width` is the tab's
  -- view width: the last divider drag (stock WezTerm also: the width the first view opened with).
  -- `nudge` is passed on to the bar (see gear_nudge).
  local function reconcile(rec, rs)
    local r = {
      view = rec.view, handle = rec.handle, content = rec.content, last = rec.last, dir = rec.dir,
      width = rec.width, width_of = rec.width_of, nudge = rec.nudge, web = rec.web,
    }
    for _, k in ipairs { 'handle', 'content', 'last' } do
      if r[k] and not find(rs, r[k]) then r[k] = nil end
    end
    if r.last and (r.last == r.handle or r.last == r.content) then r.last = nil end
    return r
  end

  -- Persistent state (per tab, in wezterm.GLOBAL) ------------------------------------
  local function records() return wezterm.GLOBAL.sidebar or {} end
  local function load(tab_id) return records()[tostring(tab_id)] or {} end
  local function save(tab_id, rec)
    local all = records()
    all[tostring(tab_id)] = rec
    wezterm.GLOBAL.sidebar = all
  end
  local function same(a, b)
    return a.view == b.view and a.handle == b.handle and a.content == b.content
      and a.last == b.last and a.dir == b.dir and a.width == b.width and a.width_of == b.width_of
      and a.nudge == b.nudge and a.web == b.web
  end

  -- The bar's state file: "<open|closed> <view>" and then, in any order, the nudge (an integer) and
  -- `web` (the browser button is to be shown: config/web.lua found a browser, which also means this
  -- is the Hivegrid build; bin/hivegrid-bar is a process of its own and cannot tell).
  local function write_state(rec)
    local f = rec.handle and io.open(('%s/wezterm-sidebar-%d'):format(RUNTIME, rec.handle), 'w')
    if f then
      local nudge = (rec.nudge or 0) ~= 0 and (' ' .. rec.nudge) or ''
      f:write((rec.content and 'open ' or 'closed ') .. (rec.view or 'files') .. nudge .. (rec.web and ' web' or '') .. '\n')
      f:close()
    end
  end

  -- Pixels the window edge is farther below the last text row than above the first (the
  -- window padding differs, and the rows leave the leftover pixels at the bottom). The bar
  -- moves its bottom button by that much so the button stack sits centred. Needs the Hivegrid
  -- build's content_top / content_bottom; nil elsewhere.
  local function gear_nudge(window)
    local ok, d = pcall(function() return window:get_dimensions() end)
    if not ok or type(d) ~= 'table' or not d.content_top or not d.content_bottom then return nil end
    local cfg_ok, cfg = pcall(function() return window:effective_config() end)
    local bar_h = d.tab_bar_height or 0
    local at_bottom = cfg_ok and cfg and cfg.tab_bar_at_bottom
    local above = d.content_top - (at_bottom and 0 or bar_h)
    local below = d.pixel_height - d.content_bottom - (at_bottom and bar_h or 0)
    return math.floor(below - above + 0.5)
  end

  -- `wezterm cli`, aimed at the mux this pane belongs to (theme.mux_socket) so several GUI
  -- instances cannot mix up pane ids.
  local cli_bin = wezterm.executable_dir .. '/wezterm'
  do
    local f = io.open(cli_bin, 'rb')
    if f then f:close() else cli_bin = 'wezterm' end
  end
  local function make_cli(pane)
    local sock = theme.mux_socket(pane)
    return function(...)
      local cmd = sock and { 'env', 'WEZTERM_UNIX_SOCKET=' .. sock } or {}
      cmd[#cmd + 1] = cli_bin
      cmd[#cmd + 1] = 'cli'
      for _, a in ipairs { ... } do cmd[#cmd + 1] = tostring(a) end
      return wezterm.run_child_process(cmd)
    end
  end

  local function is_dir(path)
    if type(path) ~= 'string' or path:sub(1, 1) ~= '/' then return false end
    local f = io.open(path .. '/.', 'r')
    if f then f:close() end
    return f ~= nil
  end

  -- The shell's OSC 7 directory, unless it names a path on another host (an ssh session
  -- reports the remote path, which is meaningless to a local split).
  local function reported_cwd(pane)
    local ok, path = pcall(function()
      local cwd = pane:get_current_working_dir()
      if not cwd then return nil end
      local host = (cwd.host or ''):lower():gsub('%..*$', '')
      local me = wezterm.hostname():lower():gsub('%..*$', '')
      if host ~= '' and host ~= 'localhost' and host ~= me then return nil end
      return cwd.file_path
    end)
    return ok and path or nil
  end

  -- Existing local directory of a pane: the reported cwd, else the cwd of the foreground
  -- process or of its ancestors up to the pane's shell (a pane whose shell emits no OSC 7,
  -- or an ssh pane), else nil.
  local function pane_dir(pane)
    local path = reported_cwd(pane)
    if is_dir(path) then return path end
    local ok, info = pcall(function() return pane:get_foreground_process_info() end)
    for _ = 1, 6 do
      if not ok or not info or tostring(info.name):find('^wezterm') then return nil end
      if is_dir(info.cwd) then return info.cwd end
      ok, info = pcall(wezterm.procinfo.get_info_for_pid, info.ppid)
    end
  end

  -- Directory the sidebar view starts in: the pane's directory, else home (also for a web
  -- pane: its working directory is a page URL). The git view scans it for repositories itself
  -- (the one around it and those below it).
  local function dir_of(pane)
    return pane and not theme.is_web(pane) and pane_dir(pane) or wezterm.home_dir
  end

  -- Window rescales and layout changes ------------------------------------------------
  -- WezTerm redistributes columns across the pane tree when the window changes size (a
  -- move to another monitor), which rescales the handle and the content; a split or a
  -- closed pane elsewhere (the overview dock's top-level split above all) squeezes them
  -- too. Only a divider drag may set the saved width: it is the one change that keeps the
  -- tab's total width and its set of panes. `seen` is the state after the last settle; a
  -- width saved from any other change would stick.
  local function seen_all() return wezterm.GLOBAL.sidebar_seen or {} end
  local function pane_set(rs)
    local ids = {}
    for _, r in ipairs(rs) do ids[#ids + 1] = r.id end
    table.sort(ids)
    return table.concat(ids, ',')
  end
  local function rescale_pending(tab_id, rs)
    local s = seen_all()[tostring(tab_id)]
    return s ~= nil and (s.total ~= total_width(rs) or s.panes ~= pane_set(rs))
  end

  local settling = false
  -- The Hivegrid build lays the view out as a full-height column (see the header).
  local COLUMN = theme.has_click
  -- Column layout: AdjustPaneSize on the view moves its right edge (the grid's divider) on
  -- current Hivegrid builds; an older build moves the bar's divider instead, which settle
  -- notices once and then leaves the view's width alone for the session.
  local edge_ok = true

  -- Column layout: the view width a divider drag set (this tab's, else the last one in any
  -- tab), scaled from the window width it was set at to `total`; nil before any drag.
  local function dragged_width(rec, total)
    local w, of = rec.width, rec.width_of
    if not w then w, of = wezterm.GLOBAL.sidebar_width, wezterm.GLOBAL.sidebar_width_of end
    if w and of and of > 0 then return math.floor(w * total / of + 0.5) end
    return w
  end

  -- The width the view `c` should have. Column layout: the dragged width (dragged_width), else
  -- the default for this window size, as far as the grid can spare it.
  -- Stock layout: the width it opened with or was dragged to (content_target).
  local function view_target(rec)
    return function(rs, c)
      if not COLUMN then return content_target(rs, c, rec.width) end
      if not edge_ok then return nil end
      local function skip(id) return id == rec.handle or id == rec.content or M.is_dock(id) end
      return column_width(rs, skip, dragged_width(rec, total_width(rs)), c.width + 1)
    end
  end

  -- Brings handle and content back to their intended widths and records what was seen.
  -- Idempotent: runs `wezterm cli` only when a width is off. AdjustPaneSize acts on the
  -- tab's active pane in WezTerm releases whatever --pane-id says (the Hivegrid build
  -- resizes the given pane), so the pane to resize is activated first and the user's restored.
  -- A divider drag sets the tab's view width (rec.width, with the window width it was set at)
  -- and the width views of other tabs take.
  -- Every handler (status tick, window resize, a click) reads the tab and then calls something
  -- that lets the others run, so what a caller read can be old by the time it gets here: the
  -- same resize ran twice from the same rows, the second aimed at columns the first had already
  -- moved (it closed the view up to its minimum), and the rows read as a divider drag. settle
  -- therefore reads the tab again itself and takes the width another handler may have pinned.
  local stuck = {} -- tab id -> the widths a step was tried at without moving anything
  local function widths_of(rs, rec)
    local h, c = find(rs, rec.handle), rec.content and find(rs, rec.content)
    return table.concat({ h and h.width or '', c and c.width or '', total_width(rs) }, ':')
  end

  local function settle(window, tab, rec)
    if settling or not rec.handle then return end
    -- layouts.lua rebuilds or resizes the grid through `wezterm cli` as well; the two must
    -- not interleave their activate/adjust pairs. The next status tick settles.
    if wezterm.GLOBAL.layout_busy then return end
    local rs = rows(tab:panes_with_info())
    if not find(rs, rec.handle) then return end
    local saved = load(tab:tab_id())
    rec.width, rec.width_of = saved.width, saved.width_of
    local key = tostring(tab:tab_id())
    local seen = seen_all()[key]
    local c = rec.content and find(rs, rec.content)
    local dragged = seen and not rescale_pending(tab:tab_id(), rs)
      and c and seen.content_id == c.id and seen.content_w ~= c.width
    if dragged then
      rec.width, rec.width_of = c.width, total_width(rs)
      wezterm.GLOBAL.sidebar_width, wezterm.GLOBAL.sidebar_width_of = rec.width, rec.width_of
      save(tab:tab_id(), rec)
    end

    local target = view_target(rec)
    local step = next_step(rs, rec, target)
    -- A step that moved nothing (the divider is at a minimum) is not tried again on every
    -- status tick; it is, as soon as a width changes.
    local before = widths_of(rs, rec)
    if step and stuck[key] == before then step = nil end
    if step then
      settling = true
      local ok = pcall(function()
        local pane = window:active_pane()
        local cli = make_cli(pane)
        local prev
        for _ = 1, 3 do
          if not step or (prev and prev.id == step.id and prev.delta == step.delta) then break end
          if not COLUMN then cli('activate-pane', '--pane-id', step.id) end
          cli('adjust-pane-size', '--pane-id', step.id, '--amount', math.abs(step.delta),
            step.delta > 0 and 'Right' or 'Left')
          prev = step
          rs = rows(tab:panes_with_info())
          if COLUMN and step.id == rec.content and find(rs, rec.handle).width ~= HANDLE_COLS then
            edge_ok = false
          end
          step = next_step(rs, rec, target)
        end
        if not COLUMN then cli('activate-pane', '--pane-id', pane:pane_id()) end
      end)
      settling = false
      if not ok then return end
      rs = rows(tab:panes_with_info())
      c = rec.content and find(rs, rec.content)
      stuck[key] = widths_of(rs, rec) == before and before or nil
    end

    local all = seen_all()
    all[key] = {
      total = total_width(rs), panes = pane_set(rs),
      content_id = c and c.id or nil, content_w = c and c.width or nil,
    }
    wezterm.GLOBAL.sidebar_seen = all
  end

  local last_resize = {}
  wezterm.on('window-resized', function(window)
    local now = os.time()
    local id = window:window_id()
    if last_resize[id] and now - last_resize[id] < 1 then return end
    last_resize[id] = now
    local tab = window:active_tab()
    if not tab then return end
    local rs = rows(tab:panes_with_info())
    settle(window, tab, reconcile(load(tab:tab_id()), rs))
  end)

  -- Actions ---------------------------------------------------------------------------
  -- The width is not saved here: settle learns a divider drag on the status tick the drag
  -- itself triggers, and a width squeezed by a layout change must not be kept.
  local function hide(cli, rec)
    cli('kill-pane', '--pane-id', rec.content)
    rec.content = nil
  end

  -- The bar is a top-level split on the Hivegrid build, so it spans the full window height
  -- beside the whole grid; stock WezTerm 20240203 mis-sizes top-level splits of a
  -- multi-pane tab (see the header), so there it docks beside the leftmost pane.
  local function new_handle(cli, anchor)
    local args = { 'split-pane', '--left', '--cells', HANDLE_COLS, '--pane-id', anchor.id }
    if COLUMN then args[#args + 1] = '--top-level' end
    args[#args + 1] = '--'
    for _, a in ipairs(BAR_COMMAND) do args[#args + 1] = a end
    local ok, out = cli(table.unpack(args))
    return ok and tonumber(out) or nil
  end

  -- Every tab gets the bar, not only after the first Ctrl+F. A failed attempt is retried
  -- after 10 s, not on every status tick.
  local last_try = {}
  local function ensure_handle(window, tab, rs, rec)
    local id = tab:tab_id()
    local now = os.time()
    if last_try[id] and now - last_try[id] < 10 then return end
    last_try[id] = now
    local anchor = leftmost(rs, M.is_dock)
    if not anchor then return end
    local pane = window:active_pane()
    local cli = make_cli(pane)
    rec.handle = new_handle(cli, anchor)
    -- The split focuses the new pane; the keyboard stays where it was.
    if rec.handle then cli('activate-pane', '--pane-id', pane:pane_id()) end
  end

  -- The view's command line, started in `dir`. PWD is set explicitly because tools such as
  -- yazi prefer the inherited $PWD over the cwd. The explorer's own config home
  -- (explorer.lua: the static config plus a theme from the palette) keeps the sidebar's keys
  -- and click handling out of a global ~/.config/yazi.
  local function view_command(view, dir)
    local cmd = { 'env', 'PWD=' .. dir }
    if view == 'files' then
      cmd[#cmd + 1] = 'YAZI_CONFIG_HOME=' .. require('explorer').config_home()
    end
    cmd[#cmd + 1] = VIEWS[view].bin
    return cmd
  end

  -- Column layout: the view keeps its columns when the tab is resized (Hivegrid build; the
  -- program in it cannot set the user var before it starts, so it is injected here).
  local function mark_fixed(id)
    pcall(function()
      wezterm.mux.get_pane(id):inject_output('\27]1337;SetUserVar=hivegrid_fixed_width=MQ==\7')
    end)
  end

  local function too_narrow(window)
    window:toast_notification('Hivegrid', 'Not enough room for the sidebar', nil, 3000)
  end

  -- Column layout: widen the bar by the view's width and a divider (the grid shrinks as a
  -- whole), then split the view off the bar's right side.
  local function show_column(cli, tab, rec, src, window)
    local rs = rows(tab:panes_with_info())
    local bar = rec.handle and find(rs, rec.handle)
    if not bar then
      local anchor = leftmost(rs, M.is_dock)
      rec.handle = anchor and new_handle(cli, anchor)
      if not rec.handle then return end
      rs = rows(tab:panes_with_info())
      bar = find(rs, rec.handle)
      if not bar then return end
    end
    local function skip(id) return id == rec.handle or M.is_dock(id) end
    local w = column_width(rs, skip, dragged_width(rec, total_width(rs)))
    if not w then return too_narrow(window) end
    local grow = HANDLE_COLS + 1 + w - bar.width
    if grow ~= 0 then
      cli('adjust-pane-size', '--pane-id', bar.id, '--amount', math.abs(grow), grow > 0 and 'Right' or 'Left')
      bar = find(rows(tab:panes_with_info()), rec.handle)
    end
    local cells = bar and bar.width - HANDLE_COLS - 1
    if not cells or cells < 8 then return too_narrow(window) end -- settle narrows the bar again
    local dir = dir_of(src)
    local ok, out = cli('split-pane', '--right', '--cells', cells, '--cwd', dir,
      '--pane-id', bar.id, '--', table.unpack(view_command(rec.view, dir)))
    local id = ok and tonumber(out)
    if not id then return end
    mark_fixed(id)
    rec.content, rec.dir = id, dir
    cli('activate-pane', '--pane-id', id)
  end

  -- Column layout: `view` in place of the open view, same width: split off the old view
  -- (all but 1 column and the divider), then the old one closes and the new one takes its
  -- columns. Nothing outside the view moves. False when it could not be done.
  local function replace_view(cli, tab, rec, src, view)
    local c = find(rows(tab:panes_with_info()), rec.content)
    if not c or c.width < 10 then return false end
    local dir = dir_of(src)
    local ok, out = cli('split-pane', '--right', '--cells', c.width - 2, '--cwd', dir,
      '--pane-id', c.id, '--', table.unpack(view_command(view, dir)))
    local id = ok and tonumber(out)
    if not id then return false end
    mark_fixed(id)
    cli('kill-pane', '--pane-id', c.id)
    rec.content, rec.dir, rec.view = id, dir, view
    return true
  end

  -- Stock layout: the view splits off the anchor pane (the bar's right neighbour).
  local function show_inline(cli, rec, rs, src, anchor, window)
    local w = anchor and width_for(rs, anchor, not rec.handle, rec.width or wezterm.GLOBAL.sidebar_width)
    if not w then return too_narrow(window) end
    if not rec.handle then
      rec.handle = new_handle(cli, anchor)
      if not rec.handle then return end
    end
    local dir = dir_of(src)
    local ok, out = cli('split-pane', '--left', '--cells', w, '--cwd', dir,
      '--pane-id', anchor.id, '--', table.unpack(view_command(rec.view, dir)))
    if ok then
      rec.content, rec.dir, rec.width = tonumber(out), dir, rec.width or w
      cli('activate-pane', '--pane-id', rec.content)
    end
  end

  -- While the sidebar rearranges panes, a second call keeps out: a `wezterm cli` call can
  -- let other callbacks run before it returns.
  local busy = false
  local function exclusive(fn)
    return function(...)
      if busy then return end
      busy = true
      local ok, err = pcall(fn, ...)
      busy = false
      if not ok then wezterm.log_error('sidebar: ' .. tostring(err)) end
    end
  end

  -- want = 'files' | 'git' to show that view (or hide it if already shown); nil to
  -- toggle whatever view was last used.
  local apply = exclusive(function(window, pane, want)
    local tab = window:active_tab()
    local rs = rows(tab:panes_with_info())
    local rec = reconcile(load(tab:tab_id()), rs)
    local function skip(id) return id == rec.handle or id == rec.content or M.is_dock(id) end
    local cli = make_cli(pane)

    local src = not skip(pane:pane_id()) and pane
      or (find(rs, rec.last) or leftmost(rs, skip) or { pane = pane }).pane
    if not skip(src:pane_id()) then rec.last = src:pane_id() end

    if rec.content and (want == nil or want == rec.view) then
      hide(cli, rec)
      local back = find(rs, rec.last) or leftmost(rs, skip)
      if back then cli('activate-pane', '--pane-id', back.id) end
    elseif rec.content and COLUMN and replace_view(cli, tab, rec, src, want) then
      cli('activate-pane', '--pane-id', rec.content)
    else
      if rec.content then
        hide(cli, rec)
        rs = rows(tab:panes_with_info())
      end
      rec.view = want or rec.view or 'files'
      if COLUMN then
        show_column(cli, tab, rec, src, window)
      else
        show_inline(cli, rec, rs, src, anchor_for(rs, rec, skip), window)
      end
    end
    save(tab:tab_id(), rec)
    write_state(rec)
    -- Showing or hiding the view changes the bar's width for a moment; put it back at once.
    settle(window, tab, rec)
  end)

  local S = require('settings')(config, wezterm)
  for _, k in ipairs {
    { 'sidebar_files', 'CTRL+f', 'files' },
    { 'sidebar_git', 'CTRL+g', 'git' },
    { 'sidebar_toggle', 'LEADER+Tab', nil },
  } do
    local bind = S.key(k[1], k[2])
    table.insert(config.keys, {
      key = bind.key, mods = bind.mods,
      action = wezterm.action_callback(function(window, pane) apply(window, pane, k[3]) end),
    })
  end

  local api = {
    internals = {
      rows = rows, leftmost = leftmost, anchor_for = anchor_for,
      width_for = width_for, reconcile = reconcile, total_width = total_width,
      content_target = content_target, next_step = next_step, column_width = column_width,
      default_width = default_width,
    },
  }

  api.is_sidebar = M.is_sidebar
  api.is_dock = M.is_dock
  api.set_web_button = M.set_web_button
  api.make_cli = make_cli

  function api.toggle(window, pane, view) apply(window, pane, view) end

  -- Puts the bar (and the view) back at their widths now. A top-level split elsewhere in the
  -- tab (the overview dock) squeezes the first columns, the bar's among them, until the next
  -- status tick would notice; callers that changed the layout call this instead of waiting.
  function api.restore(window, tab)
    local rs = rows(tab:panes_with_info())
    settle(window, tab, reconcile(load(tab:tab_id()), rs))
  end

  -- True while `name` runs in the pane: its foreground process or one below it
  -- (hivegrid-git runs lazygit as a child in its own process group).
  local function runs(pane, name)
    local ok, info = pcall(function() return pane:get_foreground_process_info() end)
    local function walk(p, depth)
      if type(p) ~= 'table' or depth > 4 then return false end
      if tostring(p.name or ''):match('[^/]*$') == name then return true end
      for _, c in pairs(p.children or {}) do
        if walk(c, depth + 1) then return true end
      end
      return false
    end
    return ok and walk(info, 0)
  end

  -- The git view lists the repositories of the directory it was started in, so it is
  -- restarted when the active grid pane moves to another directory (another pane gets
  -- focus, or its shell cd's). The keyboard stays in the grid pane. The directory is
  -- recorded before the restart so a failed one is not retried on every status tick.
  -- Never while lazygit runs in the view: the change waits until lazygit quits, then the
  -- list is restarted for the directory the grid is in by then. A web pane has no
  -- directory: focusing one leaves the view where it is.
  local last_follow = {}
  local function follow_git(window, tab, rs, rec)
    if rec.view ~= 'git' or not rec.content then return rs end
    local pane = window:active_pane()
    local id = pane:pane_id()
    if id == rec.handle or id == rec.content or M.is_dock(id) or theme.is_web(pane) then return rs end
    local dir = dir_of(pane)
    local prev = rec.dir
    -- An explorer opened before dir was tracked has an unknown start directory.
    if not prev or prev == dir then
      rec.dir = dir
      return rs
    end
    local view = find(rs, rec.content)
    if view and runs(view.pane, 'lazygit') then return rs end
    local tab_id = tab:tab_id()
    local now = os.time()
    if last_follow[tab_id] and now - last_follow[tab_id] < 1 then return rs end
    last_follow[tab_id] = now
    rec.dir = dir
    local cli = make_cli(pane)
    if not (COLUMN and replace_view(cli, tab, rec, pane, 'git')) then
      local function skip(i) return i == rec.handle or i == rec.content or M.is_dock(i) end
      hide(cli, rec)
      rs = rows(tab:panes_with_info())
      if COLUMN then
        show_column(cli, tab, rec, pane, window)
      else
        show_inline(cli, rec, rs, pane, anchor_for(rs, rec, skip), window)
      end
    end
    cli('activate-pane', '--pane-id', id)
    return rows(tab:panes_with_info())
  end

  -- Reconciles state with the live tab, so call it from update-status. Creates the bar
  -- when the tab has none.
  api.sync = exclusive(function(window)
    local tab = window:active_tab()
    local rs = rows(tab:panes_with_info())
    local old = load(tab:tab_id())
    local rec = reconcile(old, rs)
    rec.nudge = gear_nudge(window)
    rec.web = M.web_button or nil
    -- The last grid pane is gone (its shell exited, it was closed): the bar, a view and the
    -- dock alone are no tab worth keeping, so the tab closes as WezTerm's last pane would.
    if rec.handle and find(rs, rec.handle) and not leftmost(rs, M.is_dock) then
      window:perform_action(wezterm.action.CloseCurrentTab { confirm = false }, find(rs, rec.handle).pane)
      return
    end
    if not rec.handle then
      ensure_handle(window, tab, rs, rec)
      if rec.handle then rs = rows(tab:panes_with_info()) end
    end
    for _, r in ipairs(rs) do
      if r.active and r.id ~= rec.handle and r.id ~= rec.content and not M.is_dock(r.id) then
        rec.last = r.id
      end
    end
    -- The bar never keeps the keyboard: a pane closed beside it, a pane move to the left
    -- or a click on it (stock WezTerm; the Hivegrid build leaves the focus alone, see
    -- hivegrid_no_focus) hands the focus back to the pane that last had it.
    local bar = rec.handle and find(rs, rec.handle)
    if bar and bar.active then
      local back = find(rs, rec.last) or leftmost(rs, M.is_dock) or find(rs, rec.content)
      if back then back.pane:activate() end
    end
    follow_git(window, tab, rs, rec)
    settle(window, tab, rec)
    if not same(old, rec) then
      save(tab:tab_id(), rec)
      write_state(rec)
    end
  end)

  -- A new grid pane for the explorer (`pane`, which must be the tab's view), started in
  -- `dir` with the command `argv` (nil: the default shell): beside the grid pane used last,
  -- on its right when it is wide, else below it, and focused.
  local RIGHT_MIN_COLS = 80
  local function open_in_grid(window, pane, dir, argv)
    local tab = pane:tab() or window:active_tab()
    local rs = rows(tab:panes_with_info())
    local rec = reconcile(load(tab:tab_id()), rs)
    if not rec.content or rec.content ~= pane:pane_id() then return end
    local function skip(id) return id == rec.handle or id == rec.content or M.is_dock(id) end
    local target = find(rs, rec.last) or leftmost(rs, skip)
    if not target then return end
    local cli = make_cli(pane)
    local args = { 'split-pane', target.width >= RIGHT_MIN_COLS and '--right' or '--bottom',
      '--cwd', dir, '--pane-id', target.id }
    if argv then
      args[#args + 1] = '--'
      for _, a in ipairs(argv) do args[#args + 1] = a end
    end
    local ok, out = cli(table.unpack(args))
    local id = ok and tonumber(out)
    if not id then return end
    cli('activate-pane', '--pane-id', id)
    rec.last = id
    save(tab:tab_id(), rec)
  end

  local function plain_path(path)
    return type(path) == 'string' and path:sub(1, 1) == '/' and not path:find('[%z\n]')
  end

  -- The explorer announces a double click (or T) as the user var hivegrid_open_dir (an
  -- absolute path): a terminal in that folder, or in a file's folder.
  local function open_terminal(window, pane, path)
    if not plain_path(path) then return end
    path = path:gsub('/+$', '')
    local dir = path == '' and '/' or path
    if not is_dir(dir) then
      local f = io.open(dir, 'r')
      if not f then return end
      f:close()
      dir = dir:match('^(.*)/[^/]*$')
      dir = dir == '' and '/' or dir
      if not is_dir(dir) then return end
    end
    open_in_grid(window, pane, dir, nil)
  end

  -- The editor command for `files`: $EDITOR as the user's shell sets it (bash and zsh read
  -- their rc files with -i, like any new terminal pane), else nano, else vi.
  local EDIT = 'ed=${EDITOR:-${VISUAL:-}}; [ -n "$ed" ] || { command -v nano >/dev/null 2>&1 && ed=nano || ed=vi; }; exec $ed "$@"'
  local function editor_argv(files)
    local shell = os.getenv('SHELL') or ''
    local argv = (shell:match('/bash$') or shell:match('/zsh$')) and { shell, '-ic', EDIT }
      or { '/bin/sh', '-c', EDIT }
    argv[#argv + 1] = 'hivegrid-edit'
    for _, f in ipairs(files) do argv[#argv + 1] = f end
    return argv
  end

  -- The explorer announces Enter / o on files as the user var hivegrid_open_file: absolute
  -- paths, one per line. They open in one editor in a new grid pane, in the first one's folder.
  local function open_files(window, pane, value)
    local files = {}
    for line in tostring(value):gmatch('[^\n]+') do
      if not plain_path(line) or is_dir(line) then return end
      local f = io.open(line, 'r')
      if not f then return end
      f:close()
      files[#files + 1] = line
    end
    if #files == 0 then return end
    local dir = files[1]:match('^(.*)/[^/]*$')
    open_in_grid(window, pane, dir == '' and '/' or dir, editor_argv(files))
  end

  wezterm.on('user-var-changed', function(window, pane, name, value)
    if name == 'sidebar_click' and api.is_sidebar(pane:pane_id()) then
      -- A click on a button of the bar. The overview, settings and browser buttons are handled in
      -- wezterm.lua.
      local view = tostring(value):match('^(%a+)')
      if view == 'files' or view == 'git' then apply(window, pane, view) end
    elseif name == 'hivegrid_open_dir' then
      open_terminal(window, pane, value)
    elseif name == 'hivegrid_open_file' then
      open_files(window, pane, value)
    end
  end)

  return api
end

-- config/web.lua says whether the bar shows its browser button: only the Hivegrid build can open a
-- web pane, and only with a browser installed. Set once per config load, before any tab syncs.
function M.set_web_button(on)
  M.web_button = on and true or nil
end

-- Standalone so other modules can skip sidebar panes without holding the api table:
-- require('sidebar').is_sidebar(pane_id)
function M.is_sidebar(pane_id)
  for _, r in pairs(require('wezterm').GLOBAL.sidebar or {}) do
    if r.handle == pane_id or r.content == pane_id then return true end
  end
  return false
end

-- The view of tab `tab_id` when it is a leaf split of the leftmost grid pane (stock
-- WezTerm), else nil: layouts.lua then measures that grid column with the view in. On the
-- Hivegrid build the view is a column of its own beside the grid.
function M.inline_view(tab_id)
  if require('theme').has_click then return nil end
  return ((require('wezterm').GLOBAL.sidebar or {})[tostring(tab_id)] or {}).content
end

-- True for any chrome pane that is not part of the agent grid: the sidebar's handle and
-- view, and the overview dock (overview.lua keeps its pane in wezterm.GLOBAL.overview,
-- keyed by tab id). Skip these wherever the grid's panes are counted or addressed.
function M.is_dock(pane_id)
  if M.is_sidebar(pane_id) then return true end
  for _, r in pairs(require('wezterm').GLOBAL.overview or {}) do
    if r.pane == pane_id then return true end
  end
  return false
end

return setmetatable(M, { __call = function(_, config, wezterm) return build(config, wezterm) end })
