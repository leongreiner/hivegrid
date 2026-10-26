local wezterm = require 'wezterm'
local act = wezterm.action
local config = wezterm.config_builder()

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

-- Make the focused agent obvious in a grid.
config.inactive_pane_hsb = { saturation = 0.8, brightness = 0.6 }

-- Header: integrated window buttons in a fancy tab bar at the top.
config.window_decorations = 'INTEGRATED_BUTTONS|RESIZE'
config.use_fancy_tab_bar = true
config.tab_bar_at_bottom = false
config.hide_tab_bar_if_only_one_tab = false
config.tab_max_width = 40

-- VS Code "Dark Modern" terminal look.
config.font = wezterm.font_with_fallback { 'DejaVu Sans Mono', 'Noto Sans Mono' }
config.font_size = 10.5
config.colors = {
  foreground = '#cccccc',
  background = '#181818',
  cursor_bg = '#cccccc',
  cursor_fg = '#181818',
  cursor_border = '#cccccc',
  selection_bg = '#264f78',
  selection_fg = '#ffffff',
  split = '#2b2b2b',
  ansi = { '#000000', '#cd3131', '#0dbc79', '#e5e510', '#2472c8', '#bc3fbc', '#11a8cd', '#e5e5e5' },
  brights = { '#666666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#e5e5e5' },
}
config.window_frame = {
  font = wezterm.font { family = 'DejaVu Sans', weight = 'Regular' },
  font_size = 9.5,
  active_titlebar_bg = '#252526',
  inactive_titlebar_bg = '#252526',
  button_bg = '#252526',
  button_fg = '#cccccc',
  button_hover_bg = '#3a3d3e',
  button_hover_fg = '#ffffff',
}
config.colors.tab_bar = {
  background = '#252526',
  -- Active tab matches the terminal so it reads as attached to the pane below.
  active_tab = { bg_color = '#181818', fg_color = '#ffffff' },
  inactive_tab = { bg_color = '#2d2d2d', fg_color = '#8b949e' },
  inactive_tab_hover = { bg_color = '#37373d', fg_color = '#cccccc' },
  new_tab = { bg_color = '#252526', fg_color = '#8b949e' },
  new_tab_hover = { bg_color = '#37373d', fg_color = '#ffffff' },
}

config.window_padding = { left = 6, right = 6, top = 4, bottom = 4 }

-- Agent state tracking registers its own update-status first, so the state is fresh below.
local as = require('agentstatus')(config, wezterm)
local ss = require('sysstats')(config, wezterm)
local cq = require('claudequota')(config, wezterm)
local sb = require('sidebar')(config, wezterm)
config.status_update_interval = 1000

-- Right side of the header: [BROADCAST] system stats, pane minimap, agent counts, pane count, cwd.
wezterm.on('update-status', function(window, pane)
  local cwd = pane:get_current_working_dir()
  local path = cwd and cwd.file_path or ''
  path = path:gsub('^' .. (os.getenv('HOME') or ''), '~')
  local n = 0
  for _, p in ipairs(window:active_tab():panes()) do
    if not sb.is_sidebar(p:pane_id()) then n = n + 1 end
  end
  local items = {}
  if window:active_key_table() == 'broadcast' then
    table.insert(items, { Foreground = { Color = '#f14c4c' } })
    table.insert(items, { Text = ' BROADCAST ' })
  end
  if ss.visible() then
    local wide = window:get_dimensions().pixel_width >= 1500
    for _, it in ipairs(ss.segments { compact = not wide }) do table.insert(items, it) end
  end
  cq.refresh_if_due(window)
  if cq.visible() then
    local wide = window:get_dimensions().pixel_width >= 2000
    for _, it in ipairs(cq.segments { compact = not wide }) do table.insert(items, it) end
  end
  for _, it in ipairs(as.minimap(window)) do table.insert(items, it) end
  for _, it in ipairs(as.status(window, pane)) do table.insert(items, it) end
  for _, it in ipairs(sb.status(window)) do table.insert(items, it) end
  table.insert(items, { Foreground = { Color = '#8b949e' } })
  table.insert(items, { Text = string.format('%d pane%s  %s  ', n, n == 1 and '' or 's', path) })
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

-- Leader (Ctrl+Shift+Space) then 1/2/3 opens a new tab with the matching menu entry.
config.leader = { key = 'Space', mods = 'CTRL|SHIFT', timeout_milliseconds = 1000 }
for i, entry in ipairs(config.launch_menu) do
  table.insert(config.keys, {
    key = tostring(i),
    mods = 'LEADER',
    action = act.SpawnCommandInNewTab { args = entry.args },
  })
end

wezterm.on('format-tab-title', function(tab, tabs, panes, cfg, hover, max_width)
  local items = as.tab_title(tab, tabs, panes, cfg, hover, max_width)
  local n = #tab.panes
  if n > 1 then
    table.insert(items, { Text = '[' .. n .. '] ' })
  end
  return items
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
