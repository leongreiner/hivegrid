-- Overview dock: a pane at the right edge of the active tab that lists every grid pane with
-- its number, state glyph, directory, time in that state and omp's session title.
-- Wire (parent): require('overview')(config, wezterm, as, sb, layouts)
--   LEADER+o shows or hides the dock in the active tab (settings key overview_toggle).
-- The dock is bin/hivegrid-overview. This module writes the rows (agentstatus.overview) to
-- $XDG_RUNTIME_DIR/hivegrid-overview-<dock pane id>.json whenever they change and the dock
-- only draws them. A click on a row reaches us as the user var hivegrid_overview_goto and
-- focuses that pane; the ▦ button as hivegrid_overview_equalize (layouts.equalize).
-- The dock pane is registered in wezterm.GLOBAL.overview[tab id] = { pane, last } (last =
-- the grid pane that was active most recently), which sidebar.is_dock reads so the rest of
-- the config leaves it out of the grid. It closes itself when no grid pane is left.
-- Placement: with the Hivegrid WezTerm build the split is `--top-level`, so the dock takes
-- the full window height beside the whole grid. Stock WezTerm 20240203 mis-sizes a
-- top-level split of a multi-pane tab (see sidebar.lua), so there the dock splits the
-- top-right grid pane and is only as tall as that pane.
local theme = require 'theme'
local P = theme.P

return function(config, wezterm, as, sb, layouts)
  local RUNTIME = os.getenv('XDG_RUNTIME_DIR') or '/tmp'
  local BIN = wezterm.home_dir .. '/.local/bin/hivegrid-overview'
  local MIN_COLS, FRACTION, MIN_TOTAL = 28, 0.18, 70
  local TOP_LEVEL = theme.has_click -- true exactly on the Hivegrid build

  local function records() return wezterm.GLOBAL.overview or {} end
  local function load(tab_id) return records()[tostring(tab_id)] end
  local function save(tab_id, rec)
    local all = records()
    all[tostring(tab_id)] = rec
    wezterm.GLOBAL.overview = all
  end
  local function state_file(pane_id)
    return ('%s/hivegrid-overview-%d.json'):format(RUNTIME, pane_id)
  end

  -- While a dock is open the bar highlights its button: it watches for the file
  -- "<bar state file>.ov", which this keeps in step with the dock.
  local flagged = {} -- tab id -> bar pane id that carries the flag
  local function set_flag(tab_id, on)
    local handle = ((wezterm.GLOBAL.sidebar or {})[tostring(tab_id)] or {}).handle
    local old = flagged[tab_id]
    if old and (not on or old ~= handle) then
      os.remove(('%s/wezterm-sidebar-%d.ov'):format(RUNTIME, old))
      flagged[tab_id] = nil
    end
    if on and handle and flagged[tab_id] ~= handle then
      local f = io.open(('%s/wezterm-sidebar-%d.ov'):format(RUNTIME, handle), 'w')
      if f then
        f:write('1')
        f:close()
        flagged[tab_id] = handle
      end
    end
  end

  -- Forgets the dock of a tab and removes its state file and bar flag.
  local function drop(tab_id, rec)
    os.remove(state_file(rec.pane))
    set_flag(tab_id, false)
    local all = records()
    all[tostring(tab_id)] = nil
    wezterm.GLOBAL.overview = all
  end

  local function exists(infos, pane_id)
    for _, i in ipairs(infos) do
      if i.pane:pane_id() == pane_id then return i end
    end
  end

  local function grid_panes(infos)
    local out = {}
    for _, i in ipairs(infos) do
      if not sb.is_dock(i.pane:pane_id()) then out[#out + 1] = i end
    end
    return out
  end

  local PAL = { 'deep', 'base', 'surface', 'text', 'bright', 'muted', 'dim', 'accent', 'raised', 'border' }
  local written = {} -- dock pane id -> last JSON written

  -- Writes the rows when they changed (atomically, the dock may read at any moment).
  local function publish(window, mtab, rec)
    set_flag(mtab:tab_id(), true)
    local rows = as.overview(window, mtab:tab_id())
    local last = rec.last
    for _, r in ipairs(rows) do
      if r.active then last = r.id end
    end
    for _, r in ipairs(rows) do r.active = (r.id == last) end
    if last ~= rec.last then
      rec.last = last
      save(mtab:tab_id(), rec)
    end
    local pal = {}
    for _, k in ipairs(PAL) do pal[k] = P[k] end
    local json = wezterm.json_encode { pal = pal, panes = rows }
    if written[rec.pane] == json then return end
    local path = state_file(rec.pane)
    local f = io.open(path .. '.tmp', 'w')
    if not f then return end
    f:write(json)
    f:close()
    if os.rename(path .. '.tmp', path) then written[rec.pane] = json end
  end

  local function toast(window, text)
    window:toast_notification('Hivegrid overview', text, nil, 3000)
  end

  -- Tabs whose dock the user closed: wezterm.GLOBAL.overview_off[tab id] = true.
  local function off(tab_id) return (wezterm.GLOBAL.overview_off or {})[tostring(tab_id)] end
  local function set_off(tab_id, v)
    local all = wezterm.GLOBAL.overview_off or {}
    all[tostring(tab_id)] = v
    wezterm.GLOBAL.overview_off = all
  end

  -- Opening or closing a top-level split squeezes the grid columns unevenly (the dock takes
  -- its columns from the first columns, and gives them all back to the last one). A grid
  -- whose panes were all about equally wide is equalized again afterwards.
  local function even(grid)
    local lo, hi = math.huge, 0
    for _, i in ipairs(grid) do
      lo, hi = math.min(lo, i.width), math.max(hi, i.width)
    end
    return hi - lo <= 3
  end

  local function hide(window, cli, tab_id, rec, infos, from)
    local dock = exists(infos, rec.pane)
    if dock then wezterm.GLOBAL.overview_width = dock.width end
    local was_even = TOP_LEVEL and even(grid_panes(infos))
    cli('kill-pane', '--pane-id', rec.pane)
    local back = from:pane_id() == rec.pane and rec.last and wezterm.mux.get_pane(rec.last)
    if back then back:activate() end -- the Lua API: a cli call from here can wait on the GUI
    written[rec.pane] = nil
    drop(tab_id, rec)
    sb.restore(window, window:active_tab())
    if was_even then
      local target = from:pane_id() == rec.pane and rec.last and wezterm.mux.get_pane(rec.last) or from
      layouts.equalize(window, target)
    end
  end

  local function show(window, mtab, infos, cli, from, quiet)
    local grid = grid_panes(infos)
    if #grid == 0 then return end
    local total, anchor = 0, nil
    for _, i in ipairs(infos) do total = math.max(total, i.left + i.width) end
    if total < MIN_TOTAL then
      if not quiet then toast(window, 'The window is too narrow for the overview') end
      return
    end
    -- The pane to split: any pane for a top-level split, else the top-right grid pane.
    local active
    for _, i in ipairs(grid) do
      if i.is_active then active = i end
      local right, a_right = i.left + i.width, anchor and anchor.left + anchor.width
      if not anchor or right > a_right or (right == a_right and i.top < anchor.top) then anchor = i end
    end
    if TOP_LEVEL then anchor = active or grid[1] end
    -- A remembered width can be tiny (the window was narrow when the dock closed): never
    -- narrower than MIN_COLS, and no more than a third of the window unless that is less.
    local width = math.min(math.max(MIN_COLS, wezterm.GLOBAL.overview_width or math.floor(total * FRACTION)),
      math.max(MIN_COLS, math.floor(total / 3)))
    local was_even = TOP_LEVEL and even(grid)
    local args = { 'split-pane', '--right', '--cells', width, '--pane-id', anchor.pane:pane_id() }
    if TOP_LEVEL then args[#args + 1] = '--top-level' end
    args[#args + 1] = '--'
    args[#args + 1] = BIN
    local ok, out = cli(table.unpack(args))
    local id = ok and tonumber(out)
    if not id then
      if not quiet then toast(window, 'Could not open the overview dock (is hivegrid-overview in ~/.local/bin?)') end
      return
    end
    local rec = { pane = id, last = (active or anchor).pane:pane_id() }
    save(mtab:tab_id(), rec)
    publish(window, mtab, rec)
    -- The split focuses the new pane; the keyboard stays where it was.
    from:activate()
    sb.restore(window, mtab) -- the top-level split squeezed the bar; no waiting for the tick
    if was_even then layouts.equalize(window, from) end
  end

  -- A tab with two or more grid panes gets the dock by itself, once its layout has held for
  -- two status ticks (omp-grid is still splitting before that). A tab where the user closed
  -- the dock keeps it closed until LEADER+o opens it again.
  local pending = {} -- tab id -> { n = grid pane count, ticks = ticks at that count }
  local function auto_open(window, mtab)
    local tab_id = mtab:tab_id()
    local infos = mtab:panes_with_info()
    local n = #grid_panes(infos)
    local from = window:active_pane()
    if off(tab_id) or n < 2 or not from or not exists(infos, from:pane_id()) then
      pending[tab_id] = nil
      return
    end
    local p = pending[tab_id]
    if not p or p.n ~= n then
      pending[tab_id] = { n = n, ticks = 1 }
      return
    end
    p.ticks = p.ticks + 1
    if p.ticks < 3 or (p.tried and os.time() - p.tried < 10) then return end
    p.tried = os.time()
    show(window, mtab, infos, sb.make_cli(from), from, true)
  end

  local function toggle(window, pane)
    local mtab = window:active_tab()
    local tab_id = mtab:tab_id()
    local infos = mtab:panes_with_info()
    local rec = load(tab_id)
    -- A click on the bar's button arrives from the bar pane: act as the grid pane used last,
    -- so the keyboard goes back to the grid.
    if sb.is_sidebar(pane:pane_id()) then
      local last = ((wezterm.GLOBAL.sidebar or {})[tostring(tab_id)] or {}).last or (rec and rec.last)
      local target = last and wezterm.mux.get_pane(last)
      if not target then
        local g = grid_panes(infos)[1]
        target = g and g.pane
      end
      pane = target or pane
    end
    local cli = sb.make_cli(pane)
    if rec and exists(infos, rec.pane) then
      set_off(tab_id, true)
      hide(window, cli, tab_id, rec, infos, pane)
    else
      if rec then drop(tab_id, rec) end
      set_off(tab_id, nil)
      show(window, mtab, infos, cli, pane)
    end
  end

  -- Keeps every dock of the window current, and cleans up after a closed dock or a tab
  -- without grid panes. Runs after agentstatus's own update-status (registered first), so
  -- its scan is fresh.
  wezterm.on('update-status', function(window)
    for _, mtab in ipairs(window:mux_window():tabs()) do
      local tab_id = mtab:tab_id()
      local rec = load(tab_id)
      if rec then
        local infos = mtab:panes_with_info()
        if not exists(infos, rec.pane) then
          -- Closed from inside the dock (q) or by hand: it stays closed in this tab.
          set_off(tab_id, true)
          written[rec.pane] = nil
          drop(tab_id, rec)
          if rec.even and mtab:tab_id() == window:active_tab():tab_id() then
            local from = window:active_pane()
            if from then layouts.equalize(window, from) end
          end
        elseif #grid_panes(infos) == 0 then
          local dock = wezterm.mux.get_pane(rec.pane)
          if dock then sb.make_cli(dock)('kill-pane', '--pane-id', rec.pane) end
          written[rec.pane] = nil
          drop(tab_id, rec)
        else
          local ev = TOP_LEVEL and even(grid_panes(infos))
          if ev ~= rec.even then
            rec.even = ev
            save(tab_id, rec)
          end
          publish(window, mtab, rec)
        end
      end
    end
    local active = window:active_tab()
    if active and not load(active:tab_id()) then auto_open(window, active) end
  end)

  -- Requests from the dock, which must come from the dock pane of its own tab: a click on a
  -- row (or its digit) focuses that grid pane, the grid buttons equalize or arrange the panes.
  wezterm.on('user-var-changed', function(window, pane, name, value)
    if name ~= 'hivegrid_overview_goto' and name ~= 'hivegrid_overview_equalize'
        and name ~= 'hivegrid_overview_arrange' then
      return
    end
    local tab = pane:tab()
    local rec = tab and load(tab:tab_id())
    if not rec or rec.pane ~= pane:pane_id() then return end
    if name == 'hivegrid_overview_equalize' then
      layouts.equalize(window, pane)
      return
    end
    if name == 'hivegrid_overview_arrange' then
      layouts.arrange(window, pane)
      return
    end
    local id = tonumber(tostring(value):match('^(%d+)'))
    local target = id and wezterm.mux.get_pane(id)
    local ttab = target and target:tab()
    if ttab and ttab:tab_id() == tab:tab_id() and not sb.is_dock(id) then target:activate() end
  end)

  local S = require('settings')(config, wezterm)
  local bind = S.key('overview_toggle', 'LEADER+o')
  table.insert(config.keys, {
    key = bind.key, mods = bind.mods, action = wezterm.action_callback(toggle),
  })

  return { toggle = toggle }
end
