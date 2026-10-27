local wezterm = require 'wezterm'
local act = wezterm.action
local config = wezterm.config_builder()
local theme = require 'theme'
local P = theme.P
local T = theme.T
local S = require('settings')(config, wezterm)
local picker = require 'picker'

-- A binding from settings.json (see settings.lua), `default` when the file has none.
local function bound(name, default, action)
  local k = S.key(name, default)
  return { key = k.key, mods = k.mods, action = action }
end

-- Ctrl+C copies when text is selected, otherwise sends SIGINT.
-- Ctrl+V pastes from the clipboard.
config.keys = {
  {
    key = 'c',
    mods = 'CTRL',
    action = wezterm.action_callback(function(window, pane)
      local sel = window:get_selection_text_for_pane(pane)
      if sel and sel ~= '' then
        window:perform_action(act.CopyTo 'ClipboardAndPrimarySelection', pane)
        window:perform_action(act.ClearSelection, pane)
      else
        window:perform_action(act.SendKey { key = 'c', mods = 'CTRL' }, pane)
      end
    end),
  },
  { key = 'v', mods = 'CTRL', action = act.PasteFrom 'Clipboard' },

  -- Pane grid for running many agents.
  bound('split_horizontal', 'CTRL+SHIFT+|', act.SplitHorizontal { domain = 'CurrentPaneDomain' }),
  bound('split_vertical', 'CTRL+SHIFT+_', act.SplitVertical { domain = 'CurrentPaneDomain' }),
  { key = 'h', mods = 'ALT', action = act.ActivatePaneDirection 'Left' },
  { key = 'l', mods = 'ALT', action = act.ActivatePaneDirection 'Right' },
  { key = 'k', mods = 'ALT', action = act.ActivatePaneDirection 'Up' },
  { key = 'j', mods = 'ALT', action = act.ActivatePaneDirection 'Down' },
  { key = 'z', mods = 'ALT', action = act.TogglePaneZoomState },
  { key = 'RightArrow', mods = 'SHIFT', action = act.SplitPane { direction = 'Right' } },
  { key = 'LeftArrow', mods = 'SHIFT', action = act.SplitPane { direction = 'Left' } },
  { key = 'DownArrow', mods = 'SHIFT', action = act.SplitPane { direction = 'Down' } },
  { key = 'UpArrow', mods = 'SHIFT', action = act.SplitPane { direction = 'Up' } },
  -- Ctrl+Backspace deletes the word before the cursor (WezTerm would send ^H).
  { key = 'Backspace', mods = 'CTRL', action = act.SendKey { key = 'w', mods = 'CTRL' } },
  { key = 'RightArrow', mods = 'ALT', action = act.ActivatePaneDirection 'Right' },
  { key = 'LeftArrow', mods = 'ALT', action = act.ActivatePaneDirection 'Left' },
  { key = 'DownArrow', mods = 'ALT', action = act.ActivatePaneDirection 'Down' },
  { key = 'UpArrow', mods = 'ALT', action = act.ActivatePaneDirection 'Up' },
}

config.scrollback_lines = 20000
config.enable_wayland = true

-- On Wayland WezTerm looks cursors up in XCURSOR_THEME, which GNOME does not export, so
-- the resize cursor over pane dividers falls back to the arrow. Use the session's theme.
local function gnome_setting(key)
  local ok, out = wezterm.run_child_process { 'gsettings', 'get', 'org.gnome.desktop.interface', key }
  if not ok then return nil end
  return (out:gsub('%s+$', ''):gsub("^'(.*)'$", '%1'))
end
config.xcursor_theme = gnome_setting('cursor-theme') or 'Adwaita'
config.xcursor_size = tonumber(gnome_setting('cursor-size')) or 24

-- Hivegrid look: solid navy base. The window stays fully opaque.
config.window_background_opacity = 1.0
config.text_background_opacity = 1.0

-- Dimmed panes sink into the navy instead of going grey.
config.inactive_pane_hsb = { hue = 1.0, saturation = 0.75, brightness = 0.55 }

-- Header: integrated window buttons in a fancy tab bar at the top.
config.window_decorations = 'INTEGRATED_BUTTONS|RESIZE'
config.use_fancy_tab_bar = true
config.tab_bar_at_bottom = false
config.hide_tab_bar_if_only_one_tab = false
config.tab_max_width = 60

-- DejaVuSansM Nerd Font Mono when it is installed; WezTerm turns a missing family into a
-- Configuration Error window, and its built-in Symbols Nerd Font Mono fallback already
-- draws the nerd glyphs over plain DejaVu Sans Mono.
local NERD = 'DejaVuSansM Nerd Font Mono'
local has_nerd = (function()
  local ok, success, out = pcall(wezterm.run_child_process, { 'fc-list', NERD, 'family' })
  return ok and success and out ~= ''
end)()
local function families(list)
  if has_nerd then table.insert(list, 1, { family = NERD, weight = 'Regular' }) end
  return list
end
config.font = wezterm.font_with_fallback(families { 'DejaVu Sans Mono', 'Noto Sans Mono' })
config.font_size = 10.5
config.colors = {
  foreground = P.text,
  background = P.base,
  cursor_bg = P.accent,
  cursor_fg = P.base,
  cursor_border = P.accent,
  selection_bg = T.selection_bg,
  selection_fg = P.bright,
  split = T.split,
  scrollbar_thumb = T.scrollbar_thumb,
  visual_bell = P.bright,
  ansi = T.ansi,
  brights = T.brights,
}
-- The tab bar font is monospaced so block-glyph bars in the status line line up.
config.window_frame = {
  border_left_width = '1px', border_right_width = '1px', border_bottom_height = '1px',
  border_top_height = '2px',
  border_left_color = P.border, border_right_color = P.border, border_bottom_color = P.border,
  border_top_color = P.edge_hi,
  font = wezterm.font_with_fallback(families {
    { family = 'DejaVu Sans Mono', weight = 'Regular' },
  }),
  font_size = 9.5,
  active_titlebar_bg = P.deep,
  inactive_titlebar_bg = P.deep,
  button_bg = P.deep,
  button_fg = P.muted,
  button_hover_bg = T.button_hover,
  button_hover_fg = P.bright,
}
config.colors.tab_bar = {
  background = P.deep,
  active_tab = { bg_color = P.raised, fg_color = P.bright },
  inactive_tab = { bg_color = P.surface, fg_color = P.muted },
  inactive_tab_hover = { bg_color = T.tab_hover, fg_color = P.text },
  inactive_tab_edge = T.tab_edge,
  new_tab = { bg_color = P.deep, fg_color = P.muted },
  new_tab_hover = { bg_color = T.button_hover, fg_color = P.bright },
}

-- Popups (selectors, palette, pane select) share the palette.
config.command_palette_bg_color = P.surface
config.command_palette_fg_color = P.text
config.command_palette_font_size = 11
config.pane_select_bg_color = P.raised
config.pane_select_fg_color = P.bright

-- Space between the header and the first line of the grid. Nothing at the sides: the sidebar
-- bar and the overview dock sit against the window edges and paint their whole width.
config.window_padding = { left = 0, right = 0, top = 22, bottom = 16 }

-- Header proportions: a taller bar, status groups centred in it with air above and
-- below, and a hairline under it. These options exist only in the Hivegrid WezTerm
-- fork; stock WezTerm rejects them, so each is tried on its own.
for key, value in pairs {
  tab_bar_height_scale = 2.6,
  tab_bar_status_scale = 1.75,
  tab_bar_separator_height = 1,
  tab_bar_separator_color = P.border,
  tab_bar_floating_tabs = true,
} do
  pcall(function() config[key] = value end)
end

-- The Hivegrid mark is drawn by the fork (seven hexagons, like the app icon); stock
-- WezTerm rejects the option and the header falls back to the glyph row in theme.brand.
local BRAND_DRAWN = pcall(function() config.tab_bar_brand = theme.BRAND_MARK end)

-- Agent state tracking registers its own update-status first, so the state is fresh below.
local as = require('agentstatus')(config, wezterm)
local ss = require('sysstats')(config, wezterm)
local cq = require('claudequota')(config, wezterm)
local hd = require('header')(config, wezterm, as)
cq.set_pane_kind(as.pane_kind)
local sb = require('sidebar')(config, wezterm)
config.status_update_interval = 1000

-- Space the header spends outside the tab titles, measured on the fancy bar at 96 dpi
-- (window_frame.font_size 9.5 gives an 8 px cell): the margin before the brand mark,
-- the "+" button and the window buttons, all in pixels at 96 dpi and scaled with the
-- window's dpi. The fancy bar caps every tab at window width / (tabs + 1) - 1.5 cells
-- and never shrinks tabs for the right status, so titles are fitted to that cap here
-- and the status is fitted to what the tabs leave over.
local LEFT_PX, NEW_TAB_PX, BUTTONS_PX = 14, 23, 146
local BRAND_CELLS = BRAND_DRAWN and 0 or theme.width(theme.brand())
if BRAND_DRAWN then
  local m = theme.BRAND_MARK
  LEFT_PX = m.margin_left + m.size + m.margin_right + 4 -- 4: the tab row's half-cell padding
end
-- Cells a tab spends besides its text: the floating chip's half-cell paddings and the
-- close button with its margin (measured 3 to 3.25 cells; the larger is kept).
local TAB_PAD_CELLS = 3.25
local TAB_CELLS = 34 -- widest title text

-- The bar font's cell is its 0.602 em advance rounded up to whole pixels.
local function cell_px(dpi)
  return math.ceil(9.5 * (dpi or 96) / 72 * 0.6021)
end

local function tab_cap(pixel_width, ntabs, cell)
  return math.floor(pixel_width / (ntabs + 1) / cell - 1.5)
end

-- Fits a tab's pieces into `cap` cells (the cap is read from wezterm.GLOBAL by the
-- format-tab-title handler, which has no window). Priority: name, agent; a cut name
-- ends in an ellipsis. Returns name, proc, width in cells (padding included).
local function tab_layout(parts, tab_index, cap)
  cap = math.min(cap or TAB_CELLS + TAB_PAD_CELLS, TAB_CELLS + TAB_PAD_CELLS)
  local fixed = #tostring(tab_index + 1) + 3 -- ' N ' before the name, ' ' after it
  local room = math.floor(cap - TAB_PAD_CELLS - fixed)
  local nlen = utf8.len(parts.name) or #parts.name
  local pneed = parts.proc and (#parts.proc + 1) or 0
  local name, proc = parts.name, parts.proc
  if nlen + pneed > room then proc, pneed = nil, 0 end
  -- A cut keeps at least one character plus the ellipsis, and only when that is shorter.
  local keep = math.max(1, room - 1)
  if nlen > room and keep + 1 < nlen then
    name = wezterm.truncate_right(parts.name, keep) .. '…'
  end
  return name, proc, fixed + (utf8.len(name) or #name) + pneed + TAB_PAD_CELLS
end

-- How the right status gives up width (PLAN, the tiers of its groups): config/header.lua.

-- The pill shown while a key table is active, so a modal key never acts unseen, with the
-- table's keys beside it while there is room. One mode colour (amber); broadcast types into
-- every pane and is the one in red. Hints read like a footer: key, then what it does.
local MODES = {
  broadcast = { 'BROADCAST', P.red, { { 'LEADER+b', 'stop' } } },
  resize = { 'RESIZE', P.amber, { { 'hjkl', 'resize' }, { 'Esc', 'done' } } },
  pane = { 'PANE', P.amber, {
    { 'g', 'go' }, { 's', 'swap' }, { 'r', 'rotate' }, { 'x', 'close' },
    { 'Esc', 'done' },
  } },
  omp_account = { 'ACCOUNT', P.amber, { { '1-9', 'start omp' }, { 'Esc', 'cancel' } } },
}

-- The web key table exists only on the Hivegrid build (config/web.lua).
if theme.has_web then
  MODES.web = { 'WEB', P.amber, {
    { 'l', 'location' }, { 'b', 'back' }, { 'f', 'fwd' }, { 'r', 'reload' }, { 'Esc', 'done' },
  } }
end

-- Footer-style key hints on the header band: key in text, what it does in muted.
local function hint_items(pairs_)
  local items = {}
  for i, h in ipairs(pairs_) do
    items[#items + 1] = { Foreground = { Color = P.text } }
    items[#items + 1] = { Text = (i > 1 and '  ' or '') .. h[1] }
    items[#items + 1] = { Foreground = { Color = P.muted } }
    items[#items + 1] = { Text = ' ' .. h[2] }
  end
  return items
end

-- The lead pills: the active key table's mode, then ZOOM while the tab has a zoomed pane
-- (any pane: on the Hivegrid build the focus may sit on a chrome pane beside it). Returns
-- the full version (with key hints) and the compact one.
local function lead_pills(window)
  local full, compact = {}, {}
  local mode = MODES[window:active_key_table() or '']
  if mode then
    local pill = theme.pill({
      { Foreground = { Color = P.deep } }, { Attribute = { Intensity = 'Bold' } }, { Text = mode[1] },
    }, mode[2])
    compact[#compact + 1] = pill
    full[#full + 1] = theme.extend(theme.extend({}, pill), theme.extend({ { Text = ' ' } }, hint_items(mode[3])))
  end
  local zoomed = false
  local tab = window:active_tab()
  for _, info in ipairs(tab and tab:panes_with_info() or {}) do
    zoomed = zoomed or info.is_zoomed
  end
  if zoomed then
    local pill = theme.click('zoom', theme.pill({
      { Foreground = { Color = P.bright } }, { Attribute = { Intensity = 'Bold' } }, { Text = 'ZOOM' },
    }, P.raised))
    compact[#compact + 1] = pill
    full[#full + 1] = theme.extend(theme.extend({}, pill), theme.extend({ { Text = ' ' } }, hint_items { { 'Alt+z', 'unzoom' } }))
  end
  return full, compact
end

-- Right side of the header, in the cells the tabs leave over: [mode] A (project, branch and
-- agents), B (Claude quota), C (system stats). Every group has tiers; header.PLAN says which
-- one narrows first.
wezterm.on('update-status', function(window)
  local dims = window:get_dimensions()
  local pixel_width = dims.pixel_width
  local cell = cell_px(dims.dpi)
  local mtabs = window:mux_window():tabs()
  local cap = tab_cap(pixel_width, #mtabs, cell)
  wezterm.GLOBAL.hivegrid_tab_cap = cap
  local fixed_px = (LEFT_PX + NEW_TAB_PX + BUTTONS_PX) * (dims.dpi or 96) / 96
  local total = (pixel_width - fixed_px) / cell
  local used = BRAND_CELLS
  for i, t in ipairs(mtabs) do
    local ap = t:active_pane()
    local parts = as.tab_parts {
      tab_title = t:get_title(),
      tab_id = t:tab_id(),
      active_pane = {
        pane_id = ap:pane_id(), title = ap:get_title(),
        current_working_dir = ap:get_current_working_dir(),
      },
    }
    local _, _, w = tab_layout(parts, i - 1, cap)
    used = used + w
  end
  local avail = total - used - 1

  cq.refresh_if_due(window)
  ss.record() -- sample and log the system stats even when the header hides them

  -- Up to four visual groups: brand (left status), then right to left of the tabs
  --   [mode] [zoom]   A: project, branch, agents   B: quota   C: system
  -- A is the one pill and the strongest text; B and C are muted text that takes a colour
  -- only past a threshold. Each group has tiers, richest first; the tiers shrink in header.PLAN
  -- order until the groups fit the free cells.
  local groups = {
    A = { tiers = hd.tiers(hd.context(window)) },
    B = { tiers = cq.visible() and cq.tiers(cq.active(window)) or {} },
    C = { tiers = ss.visible() and ss.tiers() or {} },
  }
  sb.sync(window)

  local function width_of(list)
    local w = 0
    for _, it in ipairs(list) do w = w + theme.width(it) + 1 end
    return w
  end
  local full, compact = lead_pills(window)
  local lead = full
  if not hd.fit(groups, width_of(full), avail) and #compact > 0 then
    lead = compact
    hd.fit(groups, width_of(compact), avail)
  end

  local items = {}
  for _, it in ipairs(lead) do
    theme.extend(items, it)
    items[#items + 1] = { Text = ' ' }
  end
  for _, name in ipairs { 'A', 'B', 'C' } do
    local t = groups[name].tiers[groups[name].tier]
    if t then
      theme.extend(items, t.items)
      items[#items + 1] = { Text = string.rep(' ', hd.GAP) }
    end
  end
  window:set_left_status(BRAND_DRAWN and '' or wezterm.format(theme.brand()))
  window:set_right_status(wezterm.format(items))
end)

-- The panels (bin/hivegrid-panel settings|usage|agents|monitor|hive): an overlay over the tab
-- where this WezTerm has ShowOverlayCommand (the Hivegrid fork), a new tab on stock WezTerm.
-- Every panel gets the pane it covers (--origin): inside the overlay WezTerm reports the
-- overlay's own pane, and the hive (also reached from another panel's tab strip) needs the
-- tab it was opened over.
local panel_bin = os.getenv('HOME') .. '/.local/bin/hivegrid-panel'
local function panel_action(name, extra)
  return wezterm.action_callback(function(window, pane)
    local args = { panel_bin, name, '--origin', tostring(pane:pane_id()) }
    for _, a in ipairs(extra or {}) do args[#args + 1] = a end
    local ok, action = pcall(function()
      return act.ShowOverlayCommand { args = args, cwd = os.getenv('HOME') }
    end)
    window:perform_action(ok and action or act.SpawnCommandInNewTab { args = args }, pane)
  end)
end
local function settings_action() return panel_action('settings') end
local function open_hive(window, pane)
  window:perform_action(panel_action('hive'), pane)
end
local function hive_action() return panel_action('hive') end

-- Clicks on the header's clickable segments (theme.click; needs the Hivegrid WezTerm
-- fork, stock WezTerm never emits the event). Unknown ids are ignored.
-- Left click: start/switch omp (quota pill). Right click on a quota pill or a click on the
-- system group opens the matching panel; a click on the agent counts opens the pane picker; the
-- logo (id 'brand') opens the agent hive; the ZOOM pill unzooms.
wezterm.on('status-click', function(window, pane, id, button)
  if button ~= 'Left' and button ~= 'Right' then return end
  local account = id:match('^quota:(.+)$')
  if account then
    if button == 'Left' then
      cq.start_omp(window, pane, account)
    else
      window:perform_action(panel_action('usage', { '--account', account }), pane)
    end
  elseif id == 'system' then
    window:perform_action(panel_action('monitor'), pane)
  elseif id == 'agents' then
    window:perform_action(as.picker, pane)
  elseif id == 'brand' then
    open_hive(window, pane)
  elseif id == 'zoom' then
    window:perform_action(act.TogglePaneZoomState, pane)
  end
end)

-- New tabs with agents: LEADER (Ctrl+Space) then 1/2/3, and the first rows of the "+" menu.
local grid = os.getenv('HOME') .. '/.local/bin/omp-grid'
local function grid_args(rows, cols)
  return { 'bash', '-ic', string.format('%s %d %d "$PWD"; exec bash', grid, rows, cols) }
end
local new_tabs = {
  { label = 'New tab with omp', args = { 'bash', '-ic', 'omp; exec bash' } },
  { label = 'New tab with an omp grid 2x2', args = grid_args(2, 2) },
  { label = 'New tab with an omp grid 3x3', args = grid_args(3, 3) },
}
local leader = S.key('leader', 'CTRL+Space')
config.leader = { key = leader.key, mods = leader.mods, timeout_milliseconds = 1000 }
for i, entry in ipairs(new_tabs) do
  table.insert(config.keys, {
    key = tostring(i),
    mods = 'LEADER',
    action = act.SpawnCommandInNewTab { args = entry.args },
  })
end

local web -- set at the end, once the layouts exist (config/web.lua)

-- How a binding reads in a menu: "LEADER+,", "CTRL+SHIFT+E".
local function key_hint(name, default)
  local k = S.key(name, default)
  return (k.mods and (k.mods:gsub('|', '+') .. '+') or '') .. k.key
end

-- Right-click on "+": the Hivegrid menu instead of WezTerm's launcher, which lists every
-- domain and key assignment. Panels open as overlays here too, as from their keys.
local function plus_menu(window, pane)
  local rows = {}
  for i, entry in ipairs(new_tabs) do
    rows[#rows + 1] = { entry.label, 'LEADER+' .. i, function(w, p)
      w:perform_action(act.SpawnCommandInNewTab { args = entry.args }, p)
    end }
  end
  if web and web.enabled then
    rows[#rows + 1] = { 'New web tab…', '', function(w, p) web.prompt_new(w, p, { where = 'tab' }) end }
    rows[#rows + 1] = { 'Web pane here…', key_hint('web_leader', 'LEADER+w') .. ' n', function(w, p) web.prompt_new(w, p) end }
  end
  for _, entry in ipairs {
    { 'Agent hive', 'hive', 'hive_panel', 'LEADER+a' },
    { 'Hivegrid settings', 'settings', 'settings', 'LEADER+,' },
    { 'Usage history', 'usage', 'usage_panel', 'LEADER+h' },
    { 'Agent overview', 'agents' },
    { 'System monitor', 'monitor' },
  } do
    rows[#rows + 1] = { entry[1], entry[3] and key_hint(entry[3], entry[4]) or '', function(w, p)
      if entry[2] == 'hive' then return open_hive(w, p) end
      w:perform_action(panel_action(entry[2]), p)
    end }
  end
  local items = {}
  for i, row in ipairs(rows) do
    items[i] = { id = tostring(i), cols = { { text = row[1] } }, meta = row[2] }
  end
  picker.open(window, pane, {
    title = 'Hivegrid', crumb = 'new tab or panel', verb = 'open', rows = items,
  }, function(w, p, id)
    local row = rows[tonumber(id)]
    if row then row[3](w, p) end
  end)
end

wezterm.on('new-tab-button-click', function(window, pane, button)
  if button ~= 'Right' then return end
  plus_menu(window, pane)
  return false
end)

-- Tab shape: the fancy bar draws a tab as a rounded-top box in the background colour of
-- the title's first cell, so every cell of a title shares one background. The active
-- tab is a raised box, hover a faint one, and inactive tabs are bare text on the bar.
wezterm.on('format-tab-title', function(tab, tabs, panes, cfg, hover, max_width)
  local parts = as.tab_parts(tab)
  local name, proc = tab_layout(parts, tab.tab_index, wezterm.GLOBAL.hivegrid_tab_cap)
  local bg, text, num
  if tab.is_active then
    bg, text, num = P.raised, P.bright, P.cyan
  elseif hover then
    bg, text, num = T.tab_hover, P.text, P.accent
  else
    bg, text, num = P.deep, P.muted, P.dim
  end
  local pieces = {
    { fg = text, text = ' ' },
    { fg = num, text = (tab.tab_index + 1) .. ' ', bold = true },
    { fg = text, text = name, bold = tab.is_active },
  }
  if proc then pieces[#pieces + 1] = { fg = P.dim, text = ' ' .. proc } end
  pieces[#pieces + 1] = { fg = text, text = ' ' }
  return theme.gradient(pieces, bg, bg)
end)

wezterm.on('format-window-title', function(tab, pane, tabs, panes, cfg)
  local parts = as.tab_parts(tab)
  return 'Hivegrid · ' .. parts.name
end)

-- Silent bell, with a desktop toast when a background pane rings it.
config.audible_bell = 'Disabled'
config.visual_bell = {
  fade_in_duration_ms = 75,
  fade_out_duration_ms = 150,
  target = 'CursorColor',
}

wezterm.on('bell', function(window, pane)
  as.bell(window, pane)
  if not window:is_focused() or pane:pane_id() ~= window:active_pane():pane_id() then
    window:toast_notification('Hivegrid', 'Pane ' .. pane:pane_id() .. ': ' .. pane:get_title(), nil, 4000)
  end
end)

local layouts = require('layouts')(config, wezterm)

-- While the settings panel records a new key binding it sets the user var
-- hivegrid_capture: the config's bindings are suspended meanwhile, so a chord such as
-- CTRL+f reaches the panel instead of triggering what it is bound to.
local overview -- set at the end, once the dock module is loaded
wezterm.on('user-var-changed', function(window, pane, name, value)
  if name == 'sidebar_click' then
    -- The settings, overview and browser buttons of the sidebar bar; its other buttons are
    -- handled in sidebar.lua.
    if sb.is_sidebar(pane:pane_id()) then
      local button = tostring(value):match('^(%a+)')
      if button == 'settings' then
        window:perform_action(settings_action(), pane)
      elseif button == 'overview' and overview then
        overview.toggle(window, pane)
      elseif button == 'browser' and web then
        web.browser_button(window, pane)
      end
    end
    return
  end
  if name ~= 'hivegrid_capture' then return end
  if value == '1' then
    window:set_config_overrides {
      keys = {},
      disable_default_key_bindings = true,
      leader = { key = 'F24', mods = 'SHIFT|CTRL|ALT|SUPER' },
    }
  else
    window:set_config_overrides {}
  end
end)

-- Settings panel: LEADER+, by default. Added last so the callback numbering of the
-- bindings above stays as it was.
table.insert(config.keys, bound('settings', 'LEADER+,', panel_action('settings')))
table.insert(config.keys, bound('usage_panel', 'LEADER+h', panel_action('usage')))
table.insert(config.keys, bound('hive_panel', 'LEADER+a', hive_action()))

-- Overview dock: LEADER+o by default (settings key overview_toggle).
overview = require('overview')(config, wezterm, as, sb, layouts)

-- Web pane: LEADER+w table, links into a web pane, "+" menu rows (Hivegrid build only).
web = require('web')(config, wezterm, sb, layouts)

-- Session snapshot and restore (config/session.lua): the layout and the omp chats survive a
-- crash. Last, so it sees every module it reads from.
require('session')(config, wezterm, { as = as, sb = sb, web = web })

return config
