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

-- Hivegrid look: navy base, slightly translucent. WezTerm 20240203 has no blur API
-- on Wayland, so translucency is plain alpha over whatever is behind the window.
-- text_background_opacity stays at 1 so cells with their own background (selection,
-- ls colours, TUIs) remain solid and readable.
config.window_background_opacity = 0.94
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
config.window_padding = { left = 20, right = 20, top = 24, bottom = 16 }

-- Agent state tracking registers its own update-status first, so the state is fresh below.
local as = require('agentstatus')(config, wezterm)
local ss = require('sysstats')(config, wezterm)
local cq = require('claudequota')(config, wezterm)
local sb = require('sidebar')(config, wezterm)
config.status_update_interval = 1000

-- Cells the header spends outside the tab titles: window buttons, the "+" button and
-- the brand. Measured on the fancy bar at window_frame.font_size 9.5 (cell 7.6 px).
-- The fancy bar caps every tab at window width / (tabs + 1) - 1.5 cells and never
-- shrinks tabs for the right status, so titles are fitted to that cap here and the
-- status is fitted to what the tabs leave over.
local CELL_PX, BUTTONS_CELLS, NEW_TAB_CELLS, BRAND_CELLS = 7.6, 21, 8, 15
local TAB_PAD_CELLS = 4 -- element padding, border and the close button
local TAB_CELLS = 34 -- widest title text

local function tab_cap(pixel_width, ntabs)
  return math.floor(pixel_width / (ntabs + 1) / CELL_PX - 1.5)
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

-- Powerline cap glyphs are drawn by WezTerm itself (custom_block_glyphs), so they
-- fill the whole cell without a patched font.
local CAP = { round_l = '\u{e0b6}', round_r = '\u{e0b4}' }

local function brand_items()
  local body = '#16294d'
  local items = { { Foreground = { Color = body } }, { Text = CAP.round_l } }
  theme.extend(items, theme.gradient({
    { fg = P.accent, text = ' ⬢ ', bold = true },
    { fg = P.bright, text = 'HIVE', bold = true },
    { fg = P.accent, text = 'GRID ', bold = true },
  }, body, body))
  theme.extend(items, {
    { Background = { Color = P.deep } }, { Foreground = { Color = body } },
    { Attribute = { Intensity = 'Normal' } }, { Text = CAP.round_r },
    'ResetAttributes',
  })
  return items
end

-- Right side of the header, left to right: [BROADCAST] cwd, sidebar, pane minimap,
-- agent counts, Claude quota chip, system stats. The chips share one cell budget:
-- what the tabs leave over; the system stats pick a tier that fits and the cwd takes
-- the remainder.
wezterm.on('update-status', function(window, pane)
  local cwd = pane:get_current_working_dir()
  local path = cwd and cwd.file_path or ''
  path = path:gsub('^' .. (os.getenv('HOME') or ''), '~')
  local n = 0
  for _, p in ipairs(window:active_tab():panes()) do
    if not sb.is_sidebar(p:pane_id()) then n = n + 1 end
  end

  local pixel_width = window:get_dimensions().pixel_width
  local total = pixel_width / CELL_PX
  local mtabs = window:mux_window():tabs()
  local cap = tab_cap(pixel_width, #mtabs)
  wezterm.GLOBAL.hivegrid_tab_cap = cap
  local used = BUTTONS_CELLS + NEW_TAB_CELLS + BRAND_CELLS
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
  local blocks = {}
  local function add(name, items, w)
    if #items > 0 then blocks[#blocks + 1] = { name = name, items = items, w = w or theme.width(items) } end
  end
  if window:active_key_table() == 'broadcast' then
    add('broadcast', {
      { Background = { Color = P.red } }, { Foreground = { Color = P.deep } },
      { Attribute = { Intensity = 'Bold' } }, { Text = ' BROADCAST ' }, 'ResetAttributes',
    })
  end
  add('sidebar', sb.status(window))
  add('minimap', as.minimap(window, { max_width = 28 }))
  add('agents', as.status(window, pane))
  if cq.visible() then
    local items, w = cq.segments()
    add('quota', items, w)
  end

  local function fixed_width()
    local w = 0
    for _, b in ipairs(blocks) do w = w + b.w + 1 end
    return w
  end
  for _, drop in ipairs { 'sidebar', 'minimap', 'agents', 'quota' } do
    if fixed_width() <= avail then break end
    for i, b in ipairs(blocks) do
      if b.name == drop then table.remove(blocks, i) break end
    end
  end

  local left = avail - fixed_width()
  local sys_items, sys_w = {}, 0
  if ss.visible() then sys_items, sys_w = ss.segments { budget = left } end
  left = left - sys_w - (sys_w > 0 and 1 or 0)

  local items = {}
  local label = string.format('%d pane%s', n, n == 1 and '' or 's')
  local text = path ~= '' and (path .. '  ' .. label) or label
  if #text + 2 > left then text = label end
  if #text + 2 <= left then
    items[#items + 1] = { Foreground = { Color = P.dim } }
    items[#items + 1] = { Text = text .. ' ' }
  end
  local first = true
  for _, b in ipairs(blocks) do
    if not first then theme.extend(items, theme.sep()) end
    first = false
    theme.extend(items, b.items)
  end
  if sys_w > 0 then
    if not first then theme.extend(items, theme.sep()) end
    theme.extend(items, sys_items)
  end
  items[#items + 1] = { Text = ' ' }
  window:set_left_status(wezterm.format(brand_items()))
  window:set_right_status(wezterm.format(items))
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

-- Tab shape. The fancy bar draws a tab as a rounded-top box whose padding and border
-- take the background of the title's FIRST cell, and paints the other cells over it.
-- The first cell is therefore a full block in the body colour on a lighter rim colour:
-- the rim reads as a lit bezel around a body that blends light to dark left to right.
-- The bar draws no top/bottom edge of its own, so this rim is all the depth available.
wezterm.on('format-tab-title', function(tab, tabs, panes, cfg, hover, max_width)
  local parts = as.tab_parts(tab)
  local name, proc, marks = tab_layout(parts, tab.tab_index, wezterm.GLOBAL.hivegrid_tab_cap)
  local rim, c1, c2, text, num
  if tab.is_active then
    rim, c1, c2, text, num = '#4a78c4', '#2b5199', '#1a3563', P.bright, P.cyan
  elseif hover then
    rim, c1, c2, text, num = '#2f4a82', '#1f3563', '#142348', P.text, P.accent
  else
    rim, c1, c2, text, num = '#1d2d50', '#16233f', '#0e1830', P.muted, P.dim
  end
  local pieces = {
    { fg = c1, text = '█' },
    { fg = num, text = ' ' .. (tab.tab_index + 1) .. ' ', bold = true },
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
  -- Cell 0 keeps `rim` as its background (what the bar reads) while showing body colour.
  return theme.gradient(pieces, c1, c2, rim)
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
