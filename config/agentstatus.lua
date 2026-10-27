-- Per-pane agent state for grids of omp panes, in the hive panel's vocabulary
-- (bin/hivegrid_hive.py): working, busy, attention ("needs you"), done, idle, exited, plus
-- bell, shell (a shell prompt in a pane where no agent ran) and other (any other program).
-- Shown in the overview dock (overview.lua) and the pane picker (LEADER+g), used by Alt+n (next agent that needs you);
-- the header carries no agent state.
--
-- Wiring (parent): `local as = require('agentstatus')(config, wezterm)`.
-- `as` is { tab_parts, bell, pane_kind, pane_label, agent_number, overview, picker }.
-- WezTerm 20240203 runs ALL update-status handlers in order, but only the FIRST
-- format-tab-title handler executes, so the parent's own handlers must call
-- as.tab_parts(...) / as.bell(...) themselves.
-- This module registers only a cache-refreshing update-status handler (no
-- set_right_status, so it cannot clobber the parent's status).
--
-- State sources, exact first (see `step`):
--  shell    the foreground process is a shell (exact: procfs): `exited` when an agent ran
--           in the pane before, else `shell`.
--  ext      omp with the hivegrid-agent-state extension writes its exact state to
--           $XDG_RUNTIME_DIR/hivegrid-agents/<pane>.json, the file the hive reads; trusted
--           while its heartbeat is fresh. `done` reads as `idle` once acknowledged
--           (acks.json, shared with the hive); looking at the pane acknowledges it.
--  title    omp without the extension: the run state omp puts in the terminal title
--           (working / your turn / waiting on you, see `omp_title`); done = it worked
--           while you were elsewhere and stopped.
--  screen   other agents and programs:
--   attention the bottom screen lines hold a question/permission prompt or a y/n prompt
--           (text markers; a transcript quoting one stays so until it scrolls out of the
--           last LINES rows).
--   bell    the pane rang the bell since you last focused it (exact).
--   working omp loader markers once learned (see `calibrated`), else the screen changed
--           within QUIET_SECS (heuristic: reflow after a resize, or a ticking footer,
--           looks like work for 3 s).
--   done    was working while you were elsewhere and stopped; cleared on focus.
--   idle    an agent process is alive and quiet.
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

local QUIET_SECS = 3 -- no screen change for this long => not working (heuristic)
local RUN_SECS = 6 -- continuous change this long => working, marker or not
local MARKER_SECS = 10 -- a marker on a screen frozen this long is a quote, not the loader
local SLOW_SECS = 4 -- process/cwd lookups are costly; cache per pane, staggered
local DEDUPE_SECS = 0.4 -- one scan per window per tick however many callers
local LINES = 16 -- bottom screen rows inspected per pane
local SHELLS = { bash = true, zsh = true, fish = true, sh = true, dash = true }
local AGENTS = { omp = true, claude = true, codex = true, gemini = true, aider = true, opencode = true }

-- The one state table (theme.STATES, twin of bin/hivegrid_ui.py STATES): glyph, text, colour.
local STATES = theme.STATES

-- The omp extension's state files (ext/hivegrid-agent-state.ts; bin/hivegrid_agents.py reads
-- the same ones).
local STATE_DIR = (os.getenv('XDG_RUNTIME_DIR') or '/tmp') .. '/hivegrid-agents'
local STALE_SECS = 20 -- mirrors hivegrid_agents.STALE_SECS: older heartbeat => use the title
local EXT_STATES = { working = true, busy = true, done = true, idle = true, attention = true }

-- Keyed by pane_id; pruned per window on every scan.
local panes = {}
-- Keyed by window_id: { at, list }.
local wins = {}
-- Keyed by tab_id: { lead = id of the pane that names the tab }.
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

-- omp writes its run state into the terminal title (setting tui.titleState, on by
-- default): "π <sep> <session title>", <sep> being a spinner frame while the agent works,
-- ">" when it is your turn and "!" when it waits on you. Returns that state as 'working',
-- 'idle' or 'attention' (nil for any other separator, e.g. the "π: title" form used with
-- the setting off) and the session title.
local TITLE_WORKING = { [':'] = true, ['-'] = true, ['\\'] = true, ['|'] = true, ['/'] = true }
for g in pairs(SPIN) do TITLE_WORKING[g] = true end
for _, g in ipairs { '○', '◔', '◑', '◕', '●', '⠁', '⠂', '⠄', '⠠', '⠐', '⠈' } do TITLE_WORKING[g] = true end

local function omp_title(title)
  local sep, rest = title:match('^π (%S+)%s*(.*)$')
  if not sep then return nil, title:match('^π:%s*(.*)$') or title end
  if sep == '>' then return 'idle', rest end
  if sep == '!' then return 'attention', rest end
  if TITLE_WORKING[sep] then return 'working', rest end
  return nil, rest
end

-- omp starts its session titles with an emoji (📂, 🙋, ⏱️); the lists show the words only.
local function calm_title(s)
  s = s:gsub('^[\226\240-\244][\128-\191]+\239\184\143', ''):gsub('^[\240-\244][\128-\191]+', '')
  return (s:gsub('^%s+', ''))
end

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

-- One state transition. st is the pane's persistent record (bell, finished, was_working,
-- agent, changed_at, run_start); c = { now, focused, proc, hit, title_state, ext } with ext
-- the extension's state ('working', 'busy', 'attention', 'done', 'idle') when it is fresh.
-- Returns the pane's state key.
local function step(st, c)
  local quiet = c.now - st.changed_at >= QUIET_SECS
  local omp = c.proc == 'omp'
  local ts = (omp or c.proc == '') and c.title_state or nil
  if omp and c.hit.working and not quiet then
    wezterm.GLOBAL.agentstatus_markers_seen = true
  end
  -- Only a known agent process (or its exact state file) proves an agent ran here; a title
  -- seen before the process is known proves nothing (a fresh shell's first frames).
  if AGENTS[c.proc] or c.ext then st.agent = true end
  local working
  if c.ext then
    working = c.ext == 'working' or c.ext == 'busy'
  elseif ts then
    working = ts == 'working'
  elseif omp and calibrated() then
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

  if SHELLS[c.proc] then return st.agent and 'exited' or 'shell' end
  -- The extension is exact (done vs idle included): no bell or screen guesses on top.
  if c.ext then return c.ext end
  if ts == 'attention' or (not ts and c.hit.ask) then return 'attention' end
  if st.bell then return 'bell' end
  if working then return 'working' end
  if st.finished then return 'done' end
  if AGENTS[c.proc] or ts then return 'idle' end
  return 'other'
end

-- Fields kept in wezterm.GLOBAL so a config reload (a settings save, a theme switch) keeps
-- each pane's state, its age and its flags.
local KEEP = { 'wid', 'kind', 'since', 'agent', 'bell', 'finished', 'was_working' }

-- A fresh record (new pane, config reload) starts quiet: its first screen is a baseline, not
-- a change, so nothing reads as working (then done) that never ran.
local function new_state(now, id)
  local st = { changed_at = now - QUIET_SECS, run_start = now, since = now, born = now, slow_at = now - (id * 0.7) % SLOW_SECS }
  local kept = (wezterm.GLOBAL.agentstatus_panes or {})[tostring(id)]
  if kept then
    for _, k in ipairs(KEEP) do st[k] = kept[k] end
    st.last_kind = st.kind
  end
  return st
end

-- Publishes the records of window wid's panes; other windows' records stay until their scan.
local kept_sig
local function keep_states(wid)
  local out, sig = {}, {}
  for key, rec in pairs(wezterm.GLOBAL.agentstatus_panes or {}) do
    if rec.wid ~= wid then out[key] = rec end
  end
  for id, st in pairs(panes) do
    if st.wid == wid then
      local rec = {}
      for _, k in ipairs(KEEP) do rec[k] = st[k] end
      out[tostring(id)] = rec
      sig[#sig + 1] = string.format('%d:%s:%s:%s:%s:%s:%s', id, tostring(st.kind), tostring(st.since),
        tostring(st.agent), tostring(st.bell), tostring(st.finished), tostring(st.was_working))
    end
  end
  table.sort(sig)
  local s = wid .. '|' .. table.concat(sig, ',')
  if s ~= kept_sig then
    kept_sig = s
    wezterm.GLOBAL.agentstatus_panes = out
  end
end

local function read_json(path)
  local f = io.open(path, 'r')
  if not f then return nil end
  local text = f:read('*a')
  f:close()
  local ok, v = pcall(wezterm.json_parse, text or '')
  return ok and type(v) == 'table' and v or nil
end

-- The extension's record of pane id when its heartbeat is fresh, else nil.
local function ext_record(id, now)
  local doc = read_json(STATE_DIR .. '/' .. id .. '.json')
  if not doc or not EXT_STATES[doc.state] or type(doc.updated) ~= 'number' then return nil end
  if now - doc.updated > STALE_SECS then return nil end
  return doc
end

-- Marks a done as seen, the same record the hive's `a` writes (hivegrid_agents.ack).
local function write_ack(id, since)
  local path = STATE_DIR .. '/acks.json'
  local acks = read_json(path) or {}
  acks[tostring(id)] = since
  local tmp = path .. '.tmp.lua'
  local f = io.open(tmp, 'w')
  if not f then return end
  f:write(wezterm.json_encode(acks))
  f:close()
  os.rename(tmp, path)
end

-- The first lookup runs at once (a fresh pane needs its process to be classified); later
-- ones are staggered per pane.
local function refresh_slow(pane, st, now)
  if st.proc and now - st.slow_at < SLOW_SECS then return end
  if st.proc then st.slow_at = now end
  local ok, name = pcall(pane.get_foreground_process_name, pane)
  st.proc = ok and basename(name) or ''
  -- The shell reports its directory (OSC 7) only at a prompt, so for a program started
  -- after a `cd` on the same line (`cd x && omp`) its own directory is the truth.
  local path
  if st.proc ~= '' and not SHELLS[st.proc] then
    local okp, info = pcall(pane.get_foreground_process_info, pane)
    path = okp and info and info.cwd or nil
  end
  if not path or path == '' then
    local okc, cwd = pcall(pane.get_current_working_dir, pane)
    path = okc and url_path(cwd) or ''
  end
  st.cwd = short_path(path)
  st.label = basename(st.cwd) .. (st.proc ~= '' and (':' .. st.proc) or '')
end

-- A web pane (the Hivegrid fork's browser pane) has no process, screen text or title protocol
-- to read: it is a state of its own, never working, done or bell. Its row names the page's
-- host and carries the page title.
local function scan_web(pane, st, now)
  local okc, cwd = pcall(pane.get_current_working_dir, pane)
  local url = okc and theme.web_url(cwd, true) or nil
  local okt, title = pcall(pane.get_title, pane)
  st.proc = 'web'
  st.cwd = url and theme.web_label(url) or ''
  st.label = st.cwd .. ':web'
  st.heading = okt and type(title) == 'string' and title or ''
  st.kind, st.state, st.unseen, st.reason = 'web', nil, false, nil
  if st.kind ~= st.last_kind then
    st.last_kind, st.since = st.kind, now
  end
end

-- Refresh state for every pane in the window; returns the ordered tab list
-- ({ tab, id, entries = {{ pane, info, st, num }} }).
local function scan(window, force)
  local wid = window:window_id()
  local now = now_s()
  local cache = wins[wid]
  if cache and not force and now - cache.at < DEDUPE_SECS then return cache.list end

  local win_focused = window:is_focused()
  local active_tab_id = window:active_tab():tab_id()
  local seen, list = {}, {}
  local acks -- acks.json, read once per scan when a done needs it

  for _, mtab in ipairs(window:mux_window():tabs()) do
    local tid = mtab:tab_id()
    local infos = {}
    for _, info in ipairs(mtab:panes_with_info()) do
      if not require('sidebar').is_dock(info.pane:pane_id()) then infos[#infos + 1] = info end
    end
    local numbers = panemap.numbering(infos)
    local entries = {}
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
      if theme.is_web(pane) then
        scan_web(pane, st, now)
        entries[#entries + 1] = { pane = pane, info = info, st = st, num = numbers[i] }
        goto next_pane
      end

      local ok, text = pcall(pane.get_lines_as_text, pane, LINES)
      text = ok and text or ''
      if st.text == nil then
        st.text = text
      elseif text ~= st.text then
        if now - st.changed_at >= QUIET_SECS then st.run_start = now end
        st.text, st.changed_at = text, now
      end
      refresh_slow(pane, st, now)

      -- omp reports its run state in the terminal title; read it on every scan, the
      -- spinner it shows while working moves faster than the slow lookups refresh.
      local okt, title = pcall(pane.get_title, pane)
      title = okt and title or ''
      local title_state, heading
      -- The process name is looked up only every few seconds, so a fresh pane has none yet.
      if (st.proc or '') == 'omp' or (st.proc or '') == '' then title_state, heading = omp_title(title) end
      st.heading = calm_title(heading or title)

      local focused = win_focused and tid == active_tab_id and info.is_active
      local ext = ((st.proc or '') == 'omp' or (st.proc or '') == '') and ext_record(id, now) or nil
      -- omp marks its file `exited` when it quits: the shell left behind is an exited agent,
      -- even when the agent ran before this config was loaded.
      if not st.agent and SHELLS[st.proc or ''] then
        local doc = read_json(STATE_DIR .. '/' .. id .. '.json')
        -- a file older than this record is left over from an earlier mux that reused the id
        if doc and doc.state == 'exited' and type(doc.updated) == 'number' and doc.updated >= st.born - 5 then
          st.agent = true
        end
      end
      local ext_state = ext and ext.state
      if ext then
        if type(ext.title) == 'string' and ext.title ~= '' then st.heading = calm_title(ext.title) end
        if type(ext.cwd) == 'string' and ext.cwd ~= '' then st.cwd = short_path(ext.cwd) end
      end
      if ext_state == 'done' then
        acks = acks or read_json(STATE_DIR .. '/acks.json') or {}
        if acks[tostring(id)] == ext.since then
          ext_state = 'idle'
        elseif focused then
          write_ack(id, ext.since)
          acks[tostring(id)] = ext.since
          ext_state = 'idle'
        end
      end
      st.reason = ext and type(ext.reason) == 'string' and ext.reason or nil
      -- tokens and cost as omp reported them; the dock formats them (bin/hivegrid_agents.py parse_usage)
      st.usage = ext and type(ext.usage) == 'table' and ext.usage or nil
      st.unseen = pane:has_unseen_output()
      st.kind = step(st, {
        now = now,
        focused = focused,
        proc = st.proc or '',
        hit = markers(text),
        title_state = title_state,
        ext = ext_state,
      })
      if st.kind ~= st.last_kind then
        st.last_kind = st.kind
        -- The extension knows when its state began; elsewhere it is the first scan that saw it.
        st.since = ext and st.kind == ext_state and type(ext.since) == 'number' and ext.since or now
      end
      st.state = (not focused) and st.kind or nil

      entries[#entries + 1] = { pane = pane, info = info, st = st, num = numbers[i] }
      ::next_pane::
    end
    local lead
    for _, e in ipairs(entries) do
      if e.info.is_active then lead = e.pane:pane_id() end
    end
    tabs[tid] = { lead = lead or (entries[1] and entries[1].pane:pane_id()) }
    list[#list + 1] = { tab = mtab, id = tid, entries = entries }
  end

  for id, st in pairs(panes) do
    if st.wid == wid and not seen[id] then panes[id] = nil end
  end
  keep_states(wid)
  wins[wid] = { at = now, list = list }
  return list
end

local api = { internals = { markers = markers, step = step } }

-- Pieces of a tab title: the pane's directory and the agent/process running there (nil
-- for a plain shell).
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
      -- Not scanned yet (fresh pane, config reload): the shell's directory beats its title.
      local ok, cwd = pcall(function() return tab.active_pane.current_working_dir end)
      local path = short_path(ok and url_path(cwd) or '')
      name = path ~= '' and (basename(path) ~= '' and basename(path) or path) or tab.active_pane.title
    end
  end
  return { name = name, proc = proc }
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

-- State kind of a pane ('working', 'attention', 'idle', ... see STATES) as of the last scan,
-- nil if unknown.
function api.pane_kind(pane_id)
  return panes[pane_id] and panes[pane_id].kind or nil
end

function api.pane_label(pane_id)
  return panes[pane_id] and panes[pane_id].label or nil
end

-- Reading-order number of a pane within its tab (matches the minimap digits).
function api.agent_number(pane_id)
  return panes[pane_id] and panes[pane_id].num or nil
end

-- What a row says for state s of pane record st: a program's own name for 'other'.
local function state_text(s, st)
  if s == STATES.other and st.proc and st.proc ~= '' then return st.proc end
  return s.text
end

-- Rows for the overview dock, one per grid pane of tab `tab_id` in reading order:
-- { id, num, kind, glyph, text, color, name, cwd, title, reason, proc, since, active, zoomed }.
-- `kind` is the state key (see STATES; 'idle' when nothing is known), `since` the epoch
-- second it last changed, `name` the pane directory's basename and `cwd` the whole path (~ for
-- home), `title` omp's session title (or the pane title of another program), `reason` the
-- extension's detail ('bash: npm test (12s)', '1 background job', 'plan review', ...) or nil,
-- `usage` the extension's table of tokens and cost omp reported (or nil).
function api.overview(window, tab_id)
  for _, t in ipairs(scan(window)) do
    if t.id == tab_id then
      local rows = {}
      for _, e in ipairs(t.entries) do
        local st = e.st
        local kind = st.kind or 'idle'
        local s = STATES[kind]
        local name = basename(st.cwd or '')
        if name == '' then name = st.cwd or '' end
        rows[#rows + 1] = {
          id = e.pane:pane_id(), num = e.num, kind = kind, glyph = s.glyph, text = state_text(s, st),
          color = s.color, name = name, cwd = st.cwd or '', title = st.heading or '', reason = st.reason, usage = st.usage, proc = st.proc or '',
          since = st.since, active = e.info.is_active, zoomed = e.info.is_zoomed,
        }
      end
      table.sort(rows, function(a, b) return a.num < b.num end)
      return rows
    end
  end
  return {}
end

-- How long ago an epoch second was: 40s, 2m, 3h, 2d.
local function age(since)
  if type(since) ~= 'number' then return '' end
  local d = math.max(0, math.floor(now_s() - since))
  if d < 60 then return d .. 's' end
  if d < 3600 then return d // 60 .. 'm' end
  if d < 86400 then return d // 3600 .. 'h' end
  return d // 86400 .. 'd'
end

-- The Hivegrid picker over every pane in the window (picker.lua): glyph, number, directory,
-- state, omp's session title, age. In one tab the digits are the dock's; across tabs a row
-- names its tab (cyan, the secondary identifier) and the digits pick the n-th row shown.
-- The most urgent other pane (needs you, bell, done) starts selected, else this one.
local function pick(window, pane)
  local list = scan(window, true)
  local multi = #list > 1
  local rows, count, best, best_rank, here_id = {}, 0, nil, 4, nil
  for ti, t in ipairs(list) do
    local entries = {}
    for _, e in ipairs(t.entries) do entries[#entries + 1] = e end
    table.sort(entries, function(a, b) return a.num < b.num end)
    for _, e in ipairs(entries) do
      count = count + 1
      local kind = e.st.kind or 'idle'
      local s = STATES[kind]
      local id = tostring(e.pane:pane_id())
      local here = e.pane:pane_id() == pane:pane_id()
      local name = basename(e.st.cwd or '')
      if name == '' then name = e.st.cwd or e.st.label or '' end
      local cols = { { text = name } }
      if multi then cols[#cols + 1] = { text = 'tab ' .. ti, color = 'cyan' } end
      cols[#cols + 1] = { text = state_text(s, e.st), color = s.key or s.color }
      cols[#cols + 1] = { text = e.st.heading or '', color = 'muted' }
      rows[#rows + 1] = {
        id = id, key = (not multi) and tostring(e.num) or nil, glyph = s.glyph, color = s.key or s.color,
        cols = cols, meta = here and 'here' or age(e.st.since), meta_color = here and 'accent' or 'dim',
      }
      if here then here_id = id end
      if not here and (s.rank or 9) < best_rank then best, best_rank = id, s.rank end
    end
  end
  require('picker').open(window, pane, {
    title = 'Go to agent',
    crumb = (multi and (#list .. ' tabs · ') or '') .. count .. (count == 1 and ' pane' or ' panes'),
    verb = 'go', selected = best or here_id, rows = rows, empty = 'No panes in this window',
  }, function(_, _, id)
    local target = wezterm.mux.get_pane(tonumber(id))
    if target then target:activate() end
  end)
end
api.picker = wezterm.action_callback(pick)

-- Next pane that needs you (`needs you`, or a bell), after the active one in tab and reading
-- order, wrapping across the tabs of the window; repeated presses cycle through them.
-- Pane:activate() also brings its tab to the front. No such pane: a toast, nothing else.
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
  for i = 1, #flat do
    local e = flat[(start + i - 1) % #flat + 1]
    if e.pane:pane_id() ~= cur and (e.st.state == 'attention' or e.st.state == 'bell') then
      e.pane:activate()
      return
    end
  end
  window:toast_notification('Hivegrid', 'No agent needs you', nil, 3000)
end

-- Next / previous grid pane of the tab in reading order (the dock's digits).
local function cycle(delta)
  return function(window, pane)
    local tab = window:active_tab()
    local infos = {}
    for _, info in ipairs(tab:panes_with_info()) do
      if not require('sidebar').is_dock(info.pane:pane_id()) then infos[#infos + 1] = info end
    end
    if #infos < 2 then return end
    local numbers = panemap.numbering(infos)
    local ordered = {}
    for i, info in ipairs(infos) do ordered[numbers[i]] = info end
    local cur
    for n, info in ipairs(ordered) do
      if info.is_active then cur = n end
    end
    local nxt = ((cur or 1) - 1 + delta) % #ordered
    window:perform_action(wezterm.action.ActivatePaneByIndex(ordered[nxt + 1].index), pane)
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
