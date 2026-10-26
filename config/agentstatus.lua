-- Per-pane agent state for grids of omp panes: asking, bell, working,
-- finished, waiting, exited. Summarised in the tab title and right status, as a
-- minimap of the active tab's layout, and in a pane picker (LEADER+g).
--
-- Wiring (parent): `local as = require('agentstatus')(config, wezterm)`.
-- `as` is { status, tab_parts, bell, pane_label, agent_number, minimap, picker }.
-- WezTerm 20240203 runs ALL update-status handlers in order, but only the FIRST
-- format-tab-title handler executes, so the parent's own handlers must call
-- as.tab_parts(...) / as.status(...) / as.minimap(...) / as.bell(...) themselves.
-- This module registers only a cache-refreshing update-status handler (no
-- set_right_status, so it cannot clobber the parent's status).
--
-- State detection, strongest signal first (see `step`):
--  exited   foreground process is a shell (exact: procfs).
--  ask      the bottom screen lines hold an omp question/permission prompt or a
--           y/n prompt (text markers; a transcript quoting one stays "ask" until
--           it scrolls out of the last LINES rows).
--  bell     the pane rang the bell since you last focused it (exact).
--  working  omp loader markers once learned (see `calibrated`), else the screen
--           changed within QUIET_SECS (heuristic: reflow after a resize, or a
--           ticking footer, looks like work for 3 s).
--  finished was working while you were elsewhere and stopped; cleared on focus.
--           Works for any agent, since it only needs the working->quiet edge.
--  waiting  an agent process is alive and idle at its prompt.
-- Why markers are "learned": the omp loader text could not be observed here, so
-- negative authority (no marker => not working) is granted to omp panes only
-- after a marker was seen on a pane whose screen was changing. If the markers
-- never match, behaviour stays the timing heuristic. A pane whose screen changes
-- for RUN_SECS straight is working even without a marker.
-- Rejected: tinting panes with pane:inject_output(OSC 11/111). The sequence is
-- parsed by a separate parser, so it cannot split the app's own escape
-- sequences, but it rewrites the pane's palette: omp's terminal-theme detection
-- (OSC 11 query) would read the tint, a stale tint outlives the agent unless
-- every exit path resets it, and none of it can be checked without the GUI.

local wezterm = require 'wezterm'
local panemap = require 'panemap'
local theme = require 'theme'
local P = theme.P

local QUIET_SECS = 3 -- no screen change for this long => not working (heuristic)
local RUN_SECS = 6 -- continuous change this long => working, marker or not
local MARKER_SECS = 10 -- a marker on a screen frozen this long is a quote, not the loader
local SLOW_SECS = 4 -- process/cwd lookups are costly; cache per pane, staggered
local DEDUPE_SECS = 0.4 -- one scan per window per tick however many callers
local LINES = 16 -- bottom screen rows inspected per pane
local SHELLS = { bash = true, zsh = true, fish = true, sh = true, dash = true }
local AGENTS = { omp = true, claude = true, codex = true, gemini = true, aider = true, opencode = true }

local STATES = {
  ask = { color = P.violet, glyph = '?', text = 'asking', rank = 1 },
  bell = { color = P.red, glyph = '!', text = 'bell', rank = 2 },
  finished = { color = P.green, glyph = '✓', text = 'finished', rank = 3 },
  waiting = { color = P.amber, glyph = '●', text = 'waiting', rank = 4 },
  working = { color = P.accent, glyph = '↻', text = 'working', rank = 5 },
  exited = { color = P.grey, glyph = '○', text = 'exited', rank = 6 },
  idle = { color = P.dim, glyph = '▫', text = 'idle', rank = 7 },
}
local ORDER = { 'ask', 'bell', 'finished', 'waiting', 'working', 'exited' }

-- Keyed by pane_id; pruned per window on every scan.
local panes = {}
-- Keyed by window_id: { at, list, totals }.
local wins = {}
-- Keyed by tab_id: { counts = {state=n} }.
local tabs = {}

local function now_s()
  local ok, t = pcall(function() return tonumber(wezterm.time.now():format('%s%.3f')) end)
  return ok and t or os.time()
end

local function basename(s)
  return (s or ''):match('([^/\\]+)[/\\]*$') or ''
end

local function url_path(cwd)
  if not cwd then return '' end
  local ok, p = pcall(function() return cwd.file_path end)
  if ok and type(p) == 'string' then return p end
  return (tostring(cwd):gsub('^file://[^/]*', ''))
end

local function short_path(path)
  local home = wezterm.home_dir
  if home and home ~= '' and path:sub(1, #home) == home then return '~' .. path:sub(#home + 1) end
  return path
end

-- omp's own spinner uses these braille frames.
local SPIN = {}
for _, g in ipairs { '⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏' } do SPIN[g] = true end
local WORKING_TEXT = { 'Working…', 'Working...', 'to interrupt', 'to cancel)' }
local ASK_TEXT = { 'Waiting for your answer', 'Allow once', '(y/n)', '[y/N]', '[Y/n]', 'Do you want to proceed' }

local function contains_any(line, needles)
  for _, n in ipairs(needles) do
    if line:find(n, 1, true) then return true end
  end
  return false
end

-- Scan the bottom lines for loader / prompt markers.
local function markers(text)
  local hit = { working = false, ask = false }
  for line in text:gmatch('[^\n]+') do
    if not hit.working then
      local g = line:match '^%s*(\226[\160\161][\128-\191])%s'
      hit.working = (g ~= nil and SPIN[g] == true) or contains_any(line, WORKING_TEXT)
    end
    if not hit.ask then hit.ask = contains_any(line, ASK_TEXT) end
  end
  return hit
end

local function calibrated()
  return wezterm.GLOBAL.agentstatus_markers_seen == true
end

-- One state transition. st is the pane's persistent record (bell, finished,
-- was_working, changed_at, run_start); c = { now, focused, proc, hit, unseen }.
-- Returns the pane's state key, or nil for "nothing to report".
local function step(st, c)
  local quiet = c.now - st.changed_at >= QUIET_SECS
  local omp = c.proc == 'omp'
  if omp and c.hit.working and not quiet then
    wezterm.GLOBAL.agentstatus_markers_seen = true
  end
  local working
  if omp and calibrated() then
    working = (c.hit.working and c.now - st.changed_at < MARKER_SECS)
      or (not quiet and c.now - st.run_start >= RUN_SECS)
  else
    working = not quiet
  end

  if c.focused then
    st.bell, st.finished, st.was_working = false, false, false
  elseif working then
    st.was_working = true
  elseif st.was_working then
    st.finished, st.was_working = true, false
  end

  if SHELLS[c.proc] then return 'exited' end
  if c.hit.ask then return 'ask' end
  if st.bell then return 'bell' end
  if working then return 'working' end
  if st.finished then return 'finished' end
  if AGENTS[c.proc] then return 'waiting' end
  if c.unseen and quiet then return 'waiting' end
  return nil
end

local function new_state(now, id)
  return { changed_at = now, run_start = now, text = '', slow_at = now - (id * 0.7) % SLOW_SECS }
end

local function refresh_slow(pane, st, now)
  if now - st.slow_at < SLOW_SECS then return end
  st.slow_at = now
  local ok, name = pcall(pane.get_foreground_process_name, pane)
  st.proc = ok and basename(name) or ''
  local okc, cwd = pcall(pane.get_current_working_dir, pane)
  st.cwd = short_path(okc and url_path(cwd) or '')
  st.label = basename(st.cwd) .. (st.proc ~= '' and (':' .. st.proc) or '')
end

-- Refresh state for every pane in the window; returns the ordered tab list
-- ({ tab, id, entries = {{ pane, info, st, num }} }) and the state totals.
local function scan(window, force)
  local wid = window:window_id()
  local now = now_s()
  local cache = wins[wid]
  if cache and not force and now - cache.at < DEDUPE_SECS then return cache.list, cache.totals end

  local win_focused = window:is_focused()
  local active_tab_id = window:active_tab():tab_id()
  local seen, list, totals = {}, {}, {}

  for _, mtab in ipairs(window:mux_window():tabs()) do
    local tid = mtab:tab_id()
    local infos = {}
    for _, info in ipairs(mtab:panes_with_info()) do
      if not require('sidebar').is_sidebar(info.pane:pane_id()) then infos[#infos + 1] = info end
    end
    local numbers = panemap.numbering(infos)
    local counts, entries = {}, {}
    for i, info in ipairs(infos) do
      local pane = info.pane
      local id = pane:pane_id()
      seen[id] = true
      local st = panes[id]
      if not st then
        st = new_state(now, id)
        panes[id] = st
      end
      st.wid, st.num = wid, numbers[i]

      local ok, text = pcall(pane.get_lines_as_text, pane, LINES)
      text = ok and text or ''
      if text ~= st.text then
        if now - st.changed_at >= QUIET_SECS then st.run_start = now end
        st.text, st.changed_at = text, now
      end
      refresh_slow(pane, st, now)

      local focused = win_focused and tid == active_tab_id and info.is_active
      st.unseen = pane:has_unseen_output()
      st.kind = step(st, {
        now = now,
        focused = focused,
        proc = st.proc or '',
        hit = markers(text),
        unseen = st.unseen,
      })
      st.state = (not focused) and st.kind or nil

      if st.state then
        counts[st.state] = (counts[st.state] or 0) + 1
        totals[st.state] = (totals[st.state] or 0) + 1
      end
      entries[#entries + 1] = { pane = pane, info = info, st = st, num = numbers[i] }
    end
    local lead
    for _, e in ipairs(entries) do
      if e.info.is_active then lead = e.pane:pane_id() end
    end
    tabs[tid] = { counts = counts, lead = lead or (entries[1] and entries[1].pane:pane_id()) }
    list[#list + 1] = { tab = mtab, id = tid, entries = entries }
  end

  for id, st in pairs(panes) do
    if st.wid == wid and not seen[id] then panes[id] = nil end
  end
  wins[wid] = { at = now, list = list, totals = totals }
  return list, totals
end

-- State counts such as "✓8 ●1", each in its state colour, one space apart.
local function marks(counts)
  local items = {}
  for _, key in ipairs(ORDER) do
    local n = counts[key]
    if n then
      if #items > 0 then items[#items + 1] = { Text = ' ' } end
      items[#items + 1] = { Foreground = { Color = STATES[key].color } }
      items[#items + 1] = { Text = STATES[key].glyph .. n }
    end
  end
  return items
end

local api = { internals = { markers = markers, step = step } }

-- Right-status items; refreshes state as a side effect.
function api.status(window, _)
  local _, totals = scan(window)
  local items = marks(totals)
  if #items == 0 then return {} end
  return theme.pill(items, P.pill)
end

-- Minimap of the active tab: one glyph per layout cell, rows split by a bar,
-- the active pane highlighted, digit = agent number. Empty for a single pane
-- or a layout wider than opts.max_width (default 24).
function api.minimap(window, opts)
  local list = scan(window)
  local active = window:active_tab():tab_id()
  for _, t in ipairs(list) do
    if t.id == active then
      if #t.entries < 2 then return {} end
      local geoms = {}
      for i, e in ipairs(t.entries) do geoms[i] = e.info end
      local g = panemap.grid(geoms, opts)
      if not g then return {} end
      local items = panemap.render(g, function(i)
        local e = t.entries[i]
        local s = STATES[e.st.kind or 'idle']
        return { glyph = s.glyph, color = s.color, active = e.info.is_active, zoomed = e.info.is_zoomed }
      end)
      return theme.pill(items, P.pill)
    end
  end
  return {}
end

-- Pieces of a tab title: the pane's directory, the agent/process running there (nil
-- for a plain shell), and the state marks of the tab's background panes.
function api.tab_parts(tab)
  -- A sidebar pane is not tracked: name the tab after the grid pane it sits beside.
  local st = panes[tab.active_pane.pane_id] or panes[(tabs[tab.tab_id] or {}).lead]
  local name, proc = tab.tab_title, nil
  if not name or name == '' then
    if st and st.cwd and st.cwd ~= '' then
      name = basename(st.cwd)
      if name == '' then name = st.cwd end
      if st.proc and st.proc ~= '' and not SHELLS[st.proc] then proc = st.proc end
    else
      name = tab.active_pane.title
    end
  end
  local counts = (tabs[tab.tab_id] or {}).counts or {}
  local tail = marks(counts)
  return { name = name, proc = proc, marks = tail, marks_width = theme.width(tail) }
end

-- Called from the parent's bell handler; never returns false so the toast runs.
function api.bell(window, pane)
  local id = pane:pane_id()
  local st = panes[id]
  if not st then
    st = new_state(now_s(), id)
    panes[id] = st
  end
  local active = window:active_pane()
  if not (window:is_focused() and active and active:pane_id() == id) then
    st.bell = true
  end
end

function api.pane_label(pane_id)
  return panes[pane_id] and panes[pane_id].label or nil
end

-- Reading-order number of a pane within its tab (matches the minimap digits).
function api.agent_number(pane_id)
  return panes[pane_id] and panes[pane_id].num or nil
end

-- InputSelector over every pane in the window: "agent N [state] cwd".
local function pick(window, pane)
  local list = scan(window, true)
  local multi = #list > 1
  local choices = {}
  for ti, t in ipairs(list) do
    local entries = {}
    for _, e in ipairs(t.entries) do entries[#entries + 1] = e end
    table.sort(entries, function(a, b) return a.num < b.num end)
    for _, e in ipairs(entries) do
      local s = STATES[e.st.kind or 'idle']
      local here = e.pane:pane_id() == pane:pane_id()
      choices[#choices + 1] = {
        id = tostring(e.pane:pane_id()),
        label = wezterm.format {
          { Foreground = { Color = P.muted } },
          { Text = (multi and ('tab ' .. ti .. ' ') or '') .. 'agent ' .. e.num .. ' ' },
          { Foreground = { Color = s.color } },
          { Text = string.format('[%s]', s.text) .. string.rep(' ', 9 - #s.text) },
          { Foreground = { Color = P.text } },
          { Text = (e.st.cwd ~= '' and e.st.cwd or e.st.label or '') .. (here and '  (here)' or '') },
        },
      }
    end
  end
  window:perform_action(
    wezterm.action.InputSelector {
      title = 'Agents',
      description = 'Type a number or / to filter, Enter = go, Esc = cancel',
      fuzzy_description = 'Agent: ',
      alphabet = '123456789',
      choices = choices,
      action = wezterm.action_callback(function(_, _, id)
        if not id then return end
        local target = wezterm.mux.get_pane(tonumber(id))
        if target then target:activate() end
      end),
    },
    pane
  )
end
api.picker = wezterm.action_callback(pick)

-- Next pane (after the active one, wrapping across tabs) needing attention:
-- ask/bell/finished first, then idle agents, else any unseen output.
local function jump_next(window, pane)
  local list = scan(window, true)
  local flat, start = {}, 1
  local cur = pane:pane_id()
  for _, t in ipairs(list) do
    for _, e in ipairs(t.entries) do
      flat[#flat + 1] = e
      if e.pane:pane_id() == cur then start = #flat end
    end
  end
  local function pickfirst(match)
    for i = 1, #flat do
      local e = flat[(start + i - 1) % #flat + 1]
      if e.pane:pane_id() ~= cur and match(e.st) then return e.pane end
    end
  end
  local target = pickfirst(function(st)
    return st.state == 'ask' or st.state == 'bell' or st.state == 'finished'
  end) or pickfirst(function(st)
    return st.state == 'waiting'
  end) or pickfirst(function(st)
    return st.unseen
  end)
  if target then target:activate() end
end

local function cycle(delta)
  return function(window, pane)
    local tab = window:active_tab()
    local infos = {}
    for _, info in ipairs(tab:panes_with_info()) do
      if not require('sidebar').is_sidebar(info.pane:pane_id()) then infos[#infos + 1] = info end
    end
    if #infos < 2 then return end
    local cur
    for i, info in ipairs(infos) do
      if info.is_active then cur = i end
    end
    local nxt = ((cur or 1) - 1 + delta) % #infos
    window:perform_action(wezterm.action.ActivatePaneByIndex(infos[nxt + 1].index), pane)
  end
end

return function(config)
  config.keys = config.keys or {}
  local act = wezterm.action_callback
  local keys = {
    { key = 'n', mods = 'ALT', action = act(jump_next) },
    { key = 'n', mods = 'LEADER', action = act(jump_next) },
    { key = ']', mods = 'ALT', action = act(cycle(1)) },
    { key = '[', mods = 'ALT', action = act(cycle(-1)) },
    { key = 'g', mods = 'LEADER', action = api.picker },
  }
  for _, k in ipairs(keys) do table.insert(config.keys, k) end

  wezterm.on('update-status', function(window, _)
    scan(window)
  end)

  return api
end
