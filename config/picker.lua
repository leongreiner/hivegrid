-- The Hivegrid picker: one themed chooser for every list Hivegrid asks the user to pick
-- from (agents LEADER+g, accounts Alt+q, the + menu, pane mode swap/select) and for yes/no
-- questions. On the Hivegrid WezTerm fork it runs `hivegrid-panel picker` (bin/hivegrid_picker.py)
-- in a ShowOverlayCommand overlay over the tab; stock WezTerm gets an InputSelector built from
-- the same spec.
--
--   local picker = require 'picker'
--   picker.open(window, pane, spec, function(window, pane, id) ... end)
--
-- spec: title, crumb (muted context after the title), description, verb (what Enter does),
-- empty (text when there are no rows), selected (id selected at start), and either
--   rows = { { id, key (digit), glyph, color, cols = { { text, color, bold }, ... }, meta,
--              meta_color, desc }, ... }
-- or confirm = { text, yes, no, danger } for a Yes / No question with No focused (id 'yes'/'no').
-- Colours are palette keys of theme.P ('violet', 'muted', ...) or '#rrggbb'. The callback gets
-- the pane the picker was opened over (not the overlay) and the chosen id; it is not called
-- when the picker is closed without a choice.
--
-- Protocol: the spec travels as JSON in the overlay's environment (HIVEGRID_PICKER_SPEC) with
-- a request id; the picker answers with the user var hivegrid_pick = JSON { request, id } and
-- exits. The callback runs once the overlay is gone, so it acts on the tab, not the overlay.
local wezterm = require 'wezterm'
local theme = require 'theme'
local act = wezterm.action
local P = theme.P

local M = {}

local BIN = (os.getenv('HOME') or '') .. '/.local/bin/hivegrid-panel'
local pending = {} -- request id -> { fn, origin pane id }

-- True on a WezTerm with ShowOverlayCommand (the Hivegrid fork); stock WezTerm rejects it.
M.has_overlay = pcall(function() return act.ShowOverlayCommand { args = { 'true' } } end)

local function color(c, default)
  if type(c) == 'string' and c:match('^#%x%x%x%x%x%x$') then return c end
  return P[c or ''] or default
end

local function next_request()
  local n = (wezterm.GLOBAL.hivegrid_pick_seq or 0) + 1
  wezterm.GLOBAL.hivegrid_pick_seq = n
  return tostring(os.time()) .. '-' .. n
end

-- The stock fallback: the same rows as InputSelector lines.
local function selector(window, pane, spec, fn)
  local choices = {}
  if spec.confirm then
    choices = {
      { id = 'no', label = spec.confirm.no or 'No' },
      { id = 'yes', label = spec.confirm.yes or 'Yes' },
    }
  else
    for _, r in ipairs(spec.rows or {}) do
      local items = {}
      if r.glyph then
        items[#items + 1] = { Foreground = { Color = color(r.color, P.muted) } }
        items[#items + 1] = { Text = r.glyph .. ' ' }
      end
      for i, c in ipairs(r.cols or {}) do
        if i > 1 then items[#items + 1] = { Text = '  ' } end
        for _, s in ipairs(c.segs or { c }) do
          items[#items + 1] = { Foreground = { Color = color(s.color or c.color, P.text) } }
          items[#items + 1] = { Text = s.text or '' }
        end
      end
      if r.meta and r.meta ~= '' then
        items[#items + 1] = { Foreground = { Color = color(r.meta_color, P.dim) } }
        items[#items + 1] = { Text = '  ' .. r.meta }
      end
      choices[#choices + 1] = { id = tostring(r.id), label = wezterm.format(items) }
    end
  end
  if #choices == 0 then
    choices[1] = { id = '\0none', label = spec.empty or 'Nothing to choose' }
  end
  local desc = spec.confirm and spec.confirm.text or spec.description
  window:perform_action(act.InputSelector {
    title = spec.title or 'Hivegrid',
    description = desc or 'Enter or click chooses, / filters, Esc closes.',
    fuzzy_description = (spec.title or 'Filter') .. ': ',
    choices = choices,
    action = wezterm.action_callback(function(w, _, id)
      if id and id ~= '\0none' then fn(w, pane, id) end
    end),
  }, pane)
end

function M.open(window, pane, spec, fn)
  if not M.has_overlay then return selector(window, pane, spec, fn) end
  local request = next_request()
  pending[request] = { fn = fn, origin = pane:pane_id() }
  local palette = {}
  for k, v in pairs(P) do palette[k] = v end
  local body = {
    request = request, title = spec.title, crumb = spec.crumb, description = spec.description,
    verb = spec.verb, empty = spec.empty, selected = spec.selected, rows = spec.rows,
    confirm = spec.confirm, palette = palette,
  }
  local ok, json = pcall(wezterm.json_encode, body)
  if not ok then
    pending[request] = nil
    wezterm.log_error('hivegrid picker: ' .. tostring(json))
    return selector(window, pane, spec, fn)
  end
  window:perform_action(act.ShowOverlayCommand {
    args = { BIN, 'picker', '--origin', tostring(pane:pane_id()) },
    cwd = os.getenv('HOME'),
    set_environment_variables = { HIVEGRID_PICKER_SPEC = json },
  }, pane)
end

-- Runs fn once the overlay pane `overlay_id` no longer covers the window's tab (at most ~1 s).
local function after_overlay(window, overlay_id, fn)
  local tries = 0
  local function poll()
    local ok, ap = pcall(function() return window:active_pane() end)
    tries = tries + 1
    if ok and ap and ap:pane_id() == overlay_id and tries < 40 then
      return wezterm.time.call_after(0.025, poll)
    end
    fn()
  end
  poll()
end

-- The picker waits for this acknowledgement (typed into its input) before it exits: a user
-- var set by a program that exits at once can be lost with its pane.
M.ACK = '\x1b_hivegrid-pick-ack\x1b\\'

wezterm.on('user-var-changed', function(window, pane, name, value)
  if name ~= 'hivegrid_pick' then return end
  pcall(function() pane:send_text(M.ACK) end)
  local ok, msg = pcall(wezterm.json_parse, value or '')
  if not ok or type(msg) ~= 'table' or not msg.request then return end
  local req = pending[msg.request]
  pending[msg.request] = nil
  if not req or msg.id == nil then return end
  local overlay_id = pane:pane_id()
  after_overlay(window, overlay_id, function()
    local origin = wezterm.mux.get_pane(req.origin)
    if not origin then return end
    req.fn(window, origin, tostring(msg.id))
  end)
end)

return M
