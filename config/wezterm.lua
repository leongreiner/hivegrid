local wezterm = require 'wezterm'
local act = wezterm.action
local config = wezterm.config_builder()
local theme = require 'theme'
local P = theme.P

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
  { key = '|', mods = 'CTRL|SHIFT', action = act.SplitHorizontal { domain = 'CurrentPaneDomain' } },
  { key = '_', mods = 'CTRL|SHIFT', action = act.SplitVertical { domain = 'CurrentPaneDomain' } },
  { key = 'h', mods = 'ALT', action = act.ActivatePaneDirection 'Left' },
  { key = 'l', mods = 'ALT', action = act.ActivatePaneDirection 'Right' },
  { key = 'k', mods = 'ALT', action = act.ActivatePaneDirection 'Up' },
  { key = 'j', mods = 'ALT', action = act.ActivatePaneDirection 'Down' },
  { key = 'z', mods = 'ALT', action = act.TogglePaneZoomState },
  { key = 'RightArrow', mods = 'CTRL', action = act.SplitPane { direction = 'Right' } },
  { key = 'LeftArrow', mods = 'CTRL', action = act.SplitPane { direction = 'Left' } },
  { key = 'DownArrow', mods = 'CTRL', action = act.SplitPane { direction = 'Down' } },
  { key = 'UpArrow', mods = 'CTRL', action = act.SplitPane { direction = 'Up' } },
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

config.font = wezterm.font_with_fallback { 'DejaVu Sans Mono', 'Noto Sans Mono' }
config.font_size = 10.5
config.colors = {
  foreground = P.text,
  background = P.base,
  cursor_bg = P.accent,
  cursor_fg = P.base,
  cursor_border = P.accent,
  selection_bg = '#1f3f73',
  selection_fg = P.bright,
  split = '#1a2b4d',
  scrollbar_thumb = '#22324f',
  visual_bell = P.bright,
  ansi = {
    '#243252', '#f87171', '#34d399', '#fbbf24', '#4f9dff', '#c084fc', '#38bdf8', '#c9d6ee',
  },
  brights = {
    '#5b6b8f', '#fca5a5', '#6ee7b7', '#fcd34d', '#7dbaff', '#d8b4fe', '#7dd3fc', '#eaf1ff',
  },
}
-- The tab bar font is monospaced so block-glyph bars in the status line line up.
config.window_frame = {
  border_left_width = '1px', border_right_width = '1px', border_bottom_height = '1px',
  border_top_height = '2px',
  border_left_color = P.border, border_right_color = P.border, border_bottom_color = P.border,
  border_top_color = P.edge_hi,
  font = wezterm.font { family = 'DejaVu Sans Mono', weight = 'Regular' },
  font_size = 9.5,
  active_titlebar_bg = P.deep,
  inactive_titlebar_bg = P.deep,
  button_bg = P.deep,
  button_fg = P.muted,
  button_hover_bg = '#1b2f55',
  button_hover_fg = P.bright,
}
config.colors.tab_bar = {
  background = P.deep,
  active_tab = { bg_color = P.raised, fg_color = P.bright },
  inactive_tab = { bg_color = P.surface, fg_color = P.muted },
  inactive_tab_hover = { bg_color = '#18274a', fg_color = P.text },
  inactive_tab_edge = '#04080f',
  new_tab = { bg_color = P.surface, fg_color = P.muted },
  new_tab_hover = { bg_color = '#1b2f55', fg_color = P.bright },
}

-- Popups (selectors, palette, pane select) share the palette.
config.command_palette_bg_color = P.surface
config.command_palette_fg_color = P.text
config.command_palette_font_size = 11
config.pane_select_bg_color = P.raised
config.pane_select_fg_color = P.bright

-- Space between the header and the first line of the grid.
config.window_padding = { left = 20, right = 20, top = 22, bottom = 16 }

-- Header proportions: a taller bar, status groups centred in it with air above and
-- below, and a hairline under it. These options exist only in the Hivegrid WezTerm
-- fork; stock WezTerm rejects them, so each is tried on its own.
for key, value in pairs {
  tab_bar_height_scale = 2.6,
  tab_bar_status_scale = 1.75,
  tab_bar_separator_height = 1,
  tab_bar_separator_color = P.border,
} do
  pcall(function() config[key] = value end)
end

-- Agent state tracking registers its own update-status first, so the state is fresh below.
local as = require('agentstatus')(config, wezterm)
local ss = require('sysstats')(config, wezterm)
local cq = require('claudequota')(config, wezterm)
local sb = require('sidebar')(config, wezterm)
config.status_update_interval = 1000

-- Space the header spends outside the tab titles, measured on the fancy bar at 96 dpi
-- (window_frame.font_size 9.5 gives an 8 px cell): the margin before the brand mark,
-- the "+" button and the window buttons, all in pixels at 96 dpi and scaled with the
-- window's dpi. The fancy bar caps every tab at window width / (tabs + 1) - 1.5 cells
-- and never shrinks tabs for the right status, so titles are fitted to that cap here
-- and the status is fitted to what the tabs leave over.
local LEFT_PX, NEW_TAB_PX, BUTTONS_PX = 14, 21, 146
local BRAND_CELLS = theme.width(theme.brand())
local TAB_PAD_CELLS = 4 -- element padding, border and the close button
local TAB_CELLS = 34 -- widest title text

-- The bar font's cell is its 0.602 em advance rounded up to whole pixels.
local function cell_px(dpi)
  return math.ceil(9.5 * (dpi or 96) / 72 * 0.6021)
end

local function tab_cap(pixel_width, ntabs, cell)
  return math.floor(pixel_width / (ntabs + 1) / cell - 1.5)
end

-- Fits a tab's pieces into `cap` cells (the cap is read from wezterm.GLOBAL by the
-- format-tab-title handler, which has no window). Priority: marks, name, agent.
-- Returns name, proc, show_marks, width in cells (padding included).
local function tab_layout(parts, tab_index, cap)
  cap = math.min(cap or TAB_CELLS + TAB_PAD_CELLS, TAB_CELLS + TAB_PAD_CELLS)
  local fixed = 2 + #tostring(tab_index + 1) + 2 -- end cells, " N "
  local room = cap - TAB_PAD_CELLS - fixed
  local nlen = utf8.len(parts.name) or #parts.name
  local mneed = parts.marks_width > 0 and parts.marks_width + 1 or 0
  local pneed = parts.proc and (#parts.proc + 1) or 0
  local name, proc, marks = parts.name, parts.proc, mneed > 0
  if nlen + mneed + pneed > room then proc, pneed = nil, 0 end
  if nlen + mneed + pneed > room then
    name = wezterm.truncate_right(parts.name, math.max(3, room - mneed))
    if room - mneed < 4 then marks, mneed = false, 0 end
  end
  return name, proc, marks, fixed + (utf8.len(name) or #name) + mneed + pneed + TAB_PAD_CELLS
end

-- How the right status gives up width: { 'tier', group, tier } takes a group to a
-- narrower tier, { 'drop', group } removes it (S = the sidebar toggle).
local PLAN = {
  { 'tier', 'C', 2 }, { 'tier', 'B', 2 }, { 'tier', 'C', 3 }, { 'tier', 'B', 3 }, { 'drop', 'S' },
  { 'tier', 'A', 2 }, { 'tier', 'C', 4 }, { 'tier', 'B', 4 }, { 'tier', 'A', 3 }, { 'tier', 'B', 5 },
  { 'tier', 'A', 4 }, { 'drop', 'C' }, { 'drop', 'B' }, { 'drop', 'A' },
}

-- Right side of the header, in the cells the tabs leave over: [BROADCAST] [sidebar
-- toggle] A (path, panes, minimap or agent marks), B (Claude quota), C (system stats).
-- Every group has tiers; PLAN says which one narrows first.
wezterm.on('update-status', function(window, pane)
  local cwd = pane:get_current_working_dir()
  local path = cwd and cwd.file_path or ''
  path = path:gsub('^' .. (os.getenv('HOME') or ''), '~')
  local n = 0
  for _, p in ipairs(window:active_tab():panes()) do
    if not sb.is_sidebar(p:pane_id()) then n = n + 1 end
  end

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
      active_pane = { pane_id = ap:pane_id(), title = ap:get_title() },
    }
    local _, _, _, w = tab_layout(parts, i - 1, cap)
    used = used + w
  end
  local avail = total - used - 1

  cq.refresh_if_due(window)

  -- Up to four visual groups: brand (left status), then right to left of the tabs
  --   [broadcast] [sidebar] A: path, panes, minimap, agent marks   B: quota   C: system
  -- Each group has tiers, richest first; the tiers shrink in PLAN order until the
  -- groups fit the free cells.
  local function short_path(p)
    if p == '~' then return '~/' end
    if #p <= 22 then return p end
    local tail = p:match('([^/]*/[^/]*)/?$') or p:sub(-20)
    return '…/' .. tail
  end
  local a_tiers = {}
  do
    local minimap = as.minimap(window, { max_width = 28, bare = true })
    local marks = as.status(window, { bare = true })
    local function tier(with_path, with_minimap, with_marks)
      local head = {}
      if with_path and path ~= '' then
        head = { { Foreground = { Color = P.muted } }, { Text = short_path(path) .. ' · ' } }
      end
      theme.extend(head, { { Foreground = { Color = P.bright } }, { Text = tostring(n) } })
      local parts = { head }
      if with_minimap and #minimap > 0 then parts[#parts + 1] = minimap end
      if with_marks and #marks > 0 then parts[#parts + 1] = marks end
      local flat = {}
      for i, part in ipairs(parts) do
        if i > 1 then
          theme.extend(flat, { { Foreground = { Color = P.dim } }, { Text = ' │ ' } })
        end
        theme.extend(flat, part)
      end
      local items = theme.click('agents', theme.pill(flat, P.pill))
      return { items = items, w = theme.width(items) }
    end
    a_tiers = { tier(true, true, false), tier(true, false, true), tier(false, false, true), tier(false, false, false) }
  end
  local groups = {
    A = { tiers = a_tiers },
    B = { tiers = cq.visible() and cq.tiers() or {} },
    C = { tiers = ss.visible() and ss.tiers() or {} },
  }
  local lead = {}
  if window:active_key_table() == 'broadcast' then
    lead[#lead + 1] = theme.pill({
      { Foreground = { Color = P.bright } }, { Attribute = { Intensity = 'Bold' } },
      { Text = 'BROADCAST' },
    }, '#c23b4a')
  end
  local sidebar_items = sb.status(window)
  if #sidebar_items > 0 then lead[#lead + 1] = sidebar_items end
  local lead_w = 0
  for _, it in ipairs(lead) do lead_w = lead_w + theme.width(it) + 1 end

  for _, g in pairs(groups) do g.tier = 1 end
  local function total()
    local w = lead_w
    for _, g in pairs(groups) do
      local t = g.tiers[g.tier]
      if t then w = w + t.w + 1 end
    end
    return w
  end
  for _, step in ipairs(PLAN) do
    if total() <= avail then break end
    local g = groups[step[2]]
    if step[1] == 'tier' then
      if g.tiers[step[3]] then g.tier = math.max(g.tier, step[3]) end
    elseif step[1] == 'drop' then
      if step[2] == 'S' then lead_w, lead = 0, {} else g.tier = math.huge end
    end
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
      items[#items + 1] = { Text = ' ' }
    end
  end
  window:set_left_status(wezterm.format(theme.click('brand', theme.brand())))
  window:set_right_status(wezterm.format(items))
end)

-- Clicks on the header's clickable segments (theme.click; needs the Hivegrid WezTerm
-- fork, stock WezTerm never emits the event). Unknown ids are ignored.
wezterm.on('status-click', function(window, pane, id, button)
  if button ~= 'Left' then return end
  local account = id:match('^quota:(.+)$')
  if account then
    cq.start_omp(window, pane, account)
  elseif id == 'brand' then
    window:toast_notification('Hivegrid', 'settings panel coming soon', nil, 3000)
  elseif id == 'agents' then
    window:perform_action(as.picker, pane)
  end
end)

-- Right-click on the "+" button opens this menu.
local grid = os.getenv('HOME') .. '/.local/bin/omp-grid'
local function grid_entry(label, rows, cols)
  return {
    label = label,
    args = { 'bash', '-ic', string.format('%s %d %d "$PWD"; exec bash', grid, rows, cols) },
  }
end
config.launch_menu = {
  { label = 'omp (single)', args = { 'bash', '-ic', 'omp; exec bash' } },
  grid_entry('omp grid 2x2', 2, 2),
  grid_entry('omp grid 3x3', 3, 3),
}

-- Leader (Ctrl+Space) then 1/2/3 opens a new tab with the matching menu entry.
config.leader = { key = 'Space', mods = 'CTRL', timeout_milliseconds = 1000 }
for i, entry in ipairs(config.launch_menu) do
  table.insert(config.keys, {
    key = tostring(i),
    mods = 'LEADER',
    action = act.SpawnCommandInNewTab { args = entry.args },
  })
end

-- Tab shape: the fancy bar draws a tab as a rounded-top box in the background colour of
-- the title's first cell, so every cell of a title shares one background. The active
-- tab is a raised box, hover a faint one, and inactive tabs are bare text on the bar.
wezterm.on('format-tab-title', function(tab, tabs, panes, cfg, hover, max_width)
  local parts = as.tab_parts(tab)
  local name, proc, marks = tab_layout(parts, tab.tab_index, wezterm.GLOBAL.hivegrid_tab_cap)
  local bg, text, num
  if tab.is_active then
    bg, text, num = P.raised, P.bright, P.cyan
  elseif hover then
    bg, text, num = '#18274a', P.text, P.accent
  else
    bg, text, num = P.deep, P.muted, P.dim
  end
  local pieces = {
    { fg = text, text = ' ' },
    { fg = num, text = (tab.tab_index + 1) .. ' ', bold = true },
    { fg = text, text = name, bold = tab.is_active },
  }
  if proc then pieces[#pieces + 1] = { fg = P.dim, text = ' ' .. proc } end
  if marks then
    pieces[#pieces + 1] = { fg = P.dim, text = ' ' }
    for _, it in ipairs(parts.marks) do
      if it.Foreground then pieces[#pieces + 1] = { fg = it.Foreground.Color, text = '' } end
      if it.Text then pieces[#pieces].text = pieces[#pieces].text .. it.Text end
    end
  end
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
    window:toast_notification('WezTerm', 'Pane ' .. pane:pane_id() .. ': ' .. pane:get_title(), nil, 4000)
  end
end)

require('layouts')(config, wezterm)

return config
