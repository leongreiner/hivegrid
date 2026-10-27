-- Session snapshot and restore: Hivegrid keeps its layout on disk and builds it again when it
-- starts, so a crash, a power loss or a closed window loses no tab, no split and no omp chat.
-- Wire (parent, last): local session = require('session')(config, wezterm, { as = as, sb = sb, web = web })
--
-- Saving: a status tick (every SAVE_SECS) takes a snapshot of every window: its size in cells,
-- its tabs (custom title, the sidebar view and whether the dock was closed), and the grid panes
-- of each tab as a tree of splits with the share of each cut. A pane records what it runs: a
-- shell (directory), omp (directory, profile, and the session file omp reports through
-- ext/hivegrid-agent-state.ts) or a web page (URL). Chrome panes (bar, view, dock) are not
-- saved: the sidebar and the dock build them again. The file is written when the snapshot
-- changed, atomically ($XDG_STATE_HOME/hivegrid/session.json, the previous one kept as
-- session.json.bak, then fsync'ed in the background). A snapshot that has less in it than the
-- last one (a pane closed, an omp gone) waits SHRINK_SECS before it replaces that one: when
-- the system shuts down, panes die one after the other before the GUI does, and that must not
-- become the saved state.
--
-- Restoring (gui-startup, GUI process only): when the file holds a session of an instance that
-- is gone, every window, tab and split is built again at its saved size. An omp pane gets
-- `omp [--profile=P] --restore <session file>` typed into its shell: omp resumes the chat and
-- goes on with the turn a crash cut off (nothing when the turn had finished). A pane whose
-- session file does not exist (a chat without a message) starts a plain omp. Other programs
-- (an editor, another agent) come back as a shell in their directory. A web pane opens its
-- URL again in the default web profile. Never when the GUI was started with a command of its
-- own (`wezterm start -- cmd`) or while another running Hivegrid owns the file.
-- The snapshot that was found is copied to session.prev.json first, so a restore that went
-- wrong loses nothing. settings.json: "session": { "enabled": false } turns saving and
-- restoring off.
local theme = require 'theme'

local VERSION = 1
local SAVE_SECS = 2 -- a snapshot is taken at most this often
local SHRINK_SECS = 8 -- a snapshot with less in it must hold this long to be saved
local SEED_SECS = 120 -- a restored omp pane keeps its chat in the snapshot this long while it starts
local MAX_WINDOWS, MAX_TABS, MAX_PANES = 8, 64, 64
local SHELLS = { bash = true, zsh = true, fish = true, sh = true, dash = true }

-- Pure helpers ----------------------------------------------------------------------------

local function nonempty(s)
  return type(s) == 'string' and s ~= '' and s or nil
end

local function shquote(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function clean_text(s, max)
  return type(s) == 'string' and #s <= max and not s:find('%c') and s or nil
end

-- A tree of the panes of one tab from their rectangles (cells; `left`, `top`, `width`,
-- `height`, and `leaf`, whatever the caller keeps per pane). Neighbours are one divider cell
-- apart. Returns a leaf (the item's `leaf`) or a node { dir = 'right' | 'bottom', frac, a, b }:
-- `a` is the left or upper part, `b` the other; `frac` is the share of the cells (dividers
-- apart) that `b` takes. A cut is a divider column or row that no pane crosses; the left or
-- upper one is taken first, so the first leaf of a node is its top-left pane.
local function tree_of(items)
  if #items == 1 then return items[1].leaf end
  for _, axis in ipairs { 'right', 'bottom' } do
    local pos, size = 'left', 'width'
    if axis == 'bottom' then pos, size = 'top', 'height' end
    local cuts, order = {}, {}
    for _, it in ipairs(items) do
      local c = it[pos] + it[size]
      if not cuts[c] then
        cuts[c] = true
        order[#order + 1] = c
      end
    end
    table.sort(order)
    for _, c in ipairs(order) do
      local a, b = {}, {}
      for _, it in ipairs(items) do
        if it[pos] + it[size] <= c then
          a[#a + 1] = it
        elseif it[pos] >= c + 1 then
          b[#b + 1] = it
        else
          a, b = nil, nil
          break
        end
      end
      if a and #a > 0 and #b > 0 then
        local function extent(list)
          local lo, hi = math.huge, 0
          for _, it in ipairs(list) do
            lo, hi = math.min(lo, it[pos]), math.max(hi, it[pos] + it[size])
          end
          return hi - lo
        end
        local ea, eb = extent(a), extent(b)
        return {
          dir = axis, frac = math.floor(eb / (ea + eb) * 10000 + 0.5) / 10000,
          a = tree_of(a), b = tree_of(b),
        }
      end
    end
  end
  -- Not a tiling WezTerm makes: stack the panes in reading order, equally.
  table.sort(items, function(p, q)
    if p.top ~= q.top then return p.top < q.top end
    return p.left < q.left
  end)
  local rest = {}
  for i = 2, #items do rest[#rest + 1] = items[i] end
  return { dir = 'bottom', frac = math.floor((#items - 1) / #items * 10000 + 0.5) / 10000, a = items[1].leaf, b = tree_of(rest) }
end

local function first_leaf(node)
  while node.dir do node = node.a end
  return node
end

-- Calls fn(leaf) for every leaf, in order.
local function each_leaf(node, fn)
  if node.dir then
    each_leaf(node.a, fn)
    each_leaf(node.b, fn)
  else
    fn(node)
  end
end

-- Key-sorted text of a value: the same snapshot gives the same text whatever the table order.
local function stable(v)
  if type(v) ~= 'table' then return type(v) == 'string' and string.format('%q', v) or tostring(v) end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = k end
  table.sort(keys, function(p, q) return tostring(p) < tostring(q) end)
  local out = {}
  for _, k in ipairs(keys) do out[#out + 1] = tostring(k) .. '=' .. stable(v[k]) end
  return '{' .. table.concat(out, ',') .. '}'
end

-- What a snapshot holds that would be lost with it: counts of tabs, panes, omp chats and web pages.
local function score(doc)
  local s = { tabs = 0, panes = 0, omps = 0, chats = 0, webs = 0 }
  for _, w in ipairs(doc and doc.windows or {}) do
    for _, t in ipairs(w.tabs) do
      s.tabs = s.tabs + 1
      each_leaf(t.layout, function(leaf)
        s.panes = s.panes + 1
        if leaf.kind == 'omp' then
          s.omps = s.omps + 1
          if leaf.session then s.chats = s.chats + 1 end
        elseif leaf.kind == 'web' then
          s.webs = s.webs + 1
        end
      end)
    end
  end
  return s
end

local function lost(old, new)
  for k, v in pairs(old) do
    if (new[k] or 0) < v then return true end
  end
  return false
end

-- The command typed into the shell of a restored omp pane. `have` says whether a session file
-- exists (injected so the logic is testable).
local function omp_command(leaf, have)
  local parts = { 'omp' }
  if leaf.profile then parts[#parts + 1] = '--profile=' .. leaf.profile end
  if leaf.session and have(leaf.session) then
    parts[#parts + 1] = '--restore'
    parts[#parts + 1] = shquote(leaf.session)
  end
  return table.concat(parts, ' ')
end

-- Validation of a snapshot read from disk. Returns a fresh document with only the fields and
-- values this module writes, or nil when it is unusable. Strings that reach a shell or a
-- spawn call (profile, session file) are restricted here; nothing else is trusted.
local function clean_doc(doc)
  if type(doc) ~= 'table' or doc.version ~= VERSION or type(doc.windows) ~= 'table' then return nil end
  local function int(v, lo, hi)
    if type(v) ~= 'number' or v ~= v or v < lo or v > hi then return nil end
    return math.floor(v)
  end
  local function leaf_of(n)
    local leaf = { kind = (n.kind == 'omp' or n.kind == 'web') and n.kind or 'shell' }
    local cwd = clean_text(n.cwd, 4096)
    leaf.cwd = cwd and cwd:sub(1, 1) == '/' and cwd or nil
    leaf.proc = clean_text(n.proc, 64)
    leaf.active = n.active == true or nil
    if leaf.kind == 'omp' then
      local p = clean_text(n.profile, 64)
      leaf.profile = p and p:match('^%w[%w._-]*$') and p or nil
      local f = clean_text(n.session, 4096)
      leaf.session = f and f:sub(1, 1) == '/' and f:match('%.jsonl$') and f or nil
    elseif leaf.kind == 'web' then
      leaf.url = clean_text(n.url, 2048)
    end
    return leaf
  end
  local budget = MAX_PANES
  local function node_of(n, depth)
    if type(n) ~= 'table' or depth > 40 or budget <= 0 then return nil end
    if n.dir == nil then
      budget = budget - 1
      return leaf_of(n)
    end
    if (n.dir ~= 'right' and n.dir ~= 'bottom') or type(n.frac) ~= 'number' or n.frac ~= n.frac then return nil end
    local a, b = node_of(n.a, depth + 1), node_of(n.b, depth + 1)
    if not a or not b then return nil end
    return { dir = n.dir, frac = math.min(0.98, math.max(0.02, n.frac)), a = a, b = b }
  end
  local out = { version = VERSION, windows = {}, chrome = {} }
  for _, w in ipairs(doc.windows) do
    if #out.windows >= MAX_WINDOWS then break end
    if type(w) == 'table' and type(w.tabs) == 'table' then
      local wd = {
        cols = int(w.cols, 20, 1000), rows = int(w.rows, 5, 500),
        fullscreen = w.fullscreen == true or nil, tabs = {},
      }
      for _, t in ipairs(w.tabs) do
        if #wd.tabs >= MAX_TABS then break end
        budget = MAX_PANES
        local layout = type(t) == 'table' and node_of(t.layout, 0) or nil
        if layout then
          wd.tabs[#wd.tabs + 1] = {
            layout = layout, title = clean_text(t.title, 200), active = t.active == true or nil,
            sidebar = (t.sidebar == 'files' or t.sidebar == 'git') and t.sidebar or nil,
            dock_off = t.dock_off == true or nil,
          }
        end
      end
      if #wd.tabs > 0 then out.windows[#out.windows + 1] = wd end
    end
  end
  if #out.windows == 0 then return nil end
  if type(doc.chrome) == 'table' then
    for _, k in ipairs { 'sidebar_width', 'sidebar_width_of', 'overview_width', 'overview_width_of' } do
      out.chrome[k] = int(doc.chrome[k], 1, 2000)
    end
  end
  if type(doc.owner) == 'table' then
    out.owner = { pid = int(doc.owner.pid, 1, 2 ^ 31), boot = clean_text(doc.owner.boot, 64) }
  end
  return out
end

-- The module --------------------------------------------------------------------------------

return function(config, wezterm, deps)
  deps = deps or {}
  local as, sb, web = deps.as, deps.sb, deps.web
  local S = require('settings')(config, wezterm)
  local api = {
    enabled = S.session.enabled,
    internals = {
      tree_of = tree_of, first_leaf = first_leaf, score = score, lost = lost, stable = stable,
      omp_command = omp_command, clean_doc = clean_doc,
    },
  }
  if not api.enabled then return api end

  local DIR = (os.getenv('XDG_STATE_HOME') and os.getenv('XDG_STATE_HOME'):sub(1, 1) == '/'
    and os.getenv('XDG_STATE_HOME') or (wezterm.home_dir .. '/.local/state')) .. '/hivegrid'
  local FILE = DIR .. '/session.json'
  local BAK, PREV = FILE .. '.bak', DIR .. '/session.prev.json'
  api.file = FILE

  local function read_file(path)
    local f = io.open(path, 'rb')
    if not f then return nil end
    local text = f:read('a')
    f:close()
    return text
  end

  local function exists(path)
    local f = io.open(path, 'r')
    if f then f:close() end
    return f ~= nil
  end

  local function load_doc(path)
    local text = read_file(path)
    if not text or text == '' then return nil end
    local ok, doc = pcall(wezterm.json_parse, text)
    return ok and clean_doc(doc) or nil
  end

  -- The GUI process of this config (its pid) and the boot of this machine, to tell the owner of
  -- a snapshot: a pid from before a reboot means nothing.
  local function boot_id()
    return (read_file('/proc/sys/kernel/random/boot_id') or ''):gsub('%s+$', '')
  end
  local function my_pid()
    return tonumber((read_file('/proc/self/stat') or ''):match('^(%d+)'))
  end
  local function owner_alive(owner)
    if not owner or not owner.pid or owner.pid == my_pid() then return false end
    if owner.boot and owner.boot ~= boot_id() then return false end
    local cmd = read_file('/proc/' .. owner.pid .. '/cmdline')
    return cmd ~= nil and cmd:find('wezterm', 1, true) ~= nil
  end
  api.internals.owner_alive = owner_alive

  -- Saving ------------------------------------------------------------------------------

  local known = {} -- pane id -> { leaf, at, seed }: what the pane last showed, and restored omp panes
  local last_sig, committed, pending_shrink, last_tick

  local function path_of(pane)
    local ok, cwd = pcall(pane.get_current_working_dir, pane)
    if not ok or not cwd then return nil end
    local okp, p = pcall(function() return cwd.file_path end)
    return okp and nonempty(p) or nil
  end

  -- What one pane runs, from the agent scan (agentstatus.facts) and the pane itself.
  local function leaf_of(pane, now)
    local id = pane:pane_id()
    if theme.is_web(pane) then
      local ok, cwd = pcall(pane.get_current_working_dir, pane)
      local url = ok and theme.web_url(cwd, true) or nil
      return { kind = 'web', url = url and url.href or nil }
    end
    local f = as and as.facts(id) or nil
    local proc = f and f.proc or ''
    local leaf = { kind = 'shell', cwd = nonempty(f and f.path) or path_of(pane) }
    local k = known[id]
    if proc == 'omp' then
      leaf.kind = 'omp'
      local s = f.session
      leaf.profile = (s and s.profile) or f.profile
      leaf.session = s and s.file
      -- The extension is silent while omp starts (a restored pane) and between heartbeats:
      -- the chat it had stays the chat.
      if not leaf.session and k and k.leaf.kind == 'omp' then
        leaf.session = k.leaf.session
        leaf.profile = leaf.profile or k.leaf.profile
      end
    else
      if proc ~= '' and not SHELLS[proc] then leaf.proc = proc end
      -- A restored omp pane is a shell until the command typed into it has started omp.
      if k and k.seed and now - k.at < SEED_SECS then
        known[id] = k
        return k.leaf
      end
    end
    known[id] = { leaf = leaf, at = now }
    return leaf
  end

  local function sidebar_rec(tab_id)
    return (wezterm.GLOBAL.sidebar or {})[tostring(tab_id)] or {}
  end

  local function collect_window(mw, now, seen)
    local tabs, active_tab = {}, mw:active_tab()
    local active_id = active_tab and active_tab:tab_id()
    local cols, rows
    for _, mtab in ipairs(mw:tabs()) do
      local infos = mtab:panes_with_info()
      local tid = mtab:tab_id()
      local rec = sidebar_rec(tid)
      local items, any_active, view = {}, false, false
      local ext, ext_rows = 0, 0
      for _, info in ipairs(infos) do
        local id = info.pane:pane_id()
        ext = math.max(ext, info.left + info.width)
        ext_rows = math.max(ext_rows, info.top + info.height)
        if rec.content == id then view = true end
        if not sb.is_dock(id) and #items < MAX_PANES then
          seen[id] = true
          local leaf = leaf_of(info.pane, now)
          local copy = {}
          for k, v in pairs(leaf) do copy[k] = v end
          leaf = copy
          leaf.active = info.is_active or nil
          any_active = any_active or info.is_active
          items[#items + 1] = { left = info.left, top = info.top, width = info.width, height = info.height, leaf = leaf, id = id }
        end
      end
      if #items > 0 and #tabs < MAX_TABS then
        -- The keyboard may sit on a chrome pane (Hivegrid build): the grid pane used last has the focus.
        if not any_active then
          local pick = items[1]
          for _, it in ipairs(items) do
            if it.id == rec.last then pick = it end
          end
          pick.leaf.active = true
        end
        local off = (wezterm.GLOBAL.overview_off or {})[tostring(tid)]
        local ok, title = pcall(mtab.get_title, mtab)
        tabs[#tabs + 1] = {
          title = ok and nonempty(title) or nil,
          active = tid == active_id or nil,
          layout = tree_of(items),
          sidebar = view and (rec.view == 'files' or rec.view == 'git') and rec.view or nil,
          dock_off = off and true or nil,
        }
        if tid == active_id or not cols then cols, rows = ext, ext_rows end
      end
    end
    if #tabs == 0 then return nil end
    local full = false
    local okg, gw = pcall(mw.gui_window, mw)
    if okg and gw then
      local okd, dims = pcall(gw.get_dimensions, gw)
      full = okd and dims and dims.is_full_screen == true or false
    end
    return { cols = cols, rows = rows, fullscreen = full or nil, tabs = tabs }
  end

  -- The snapshot of everything now open, or nil when nothing is.
  local function snapshot(now)
    local windows, seen = {}, {}
    for _, mw in ipairs(wezterm.mux.all_windows()) do
      if #windows >= MAX_WINDOWS then break end
      local ok, w = pcall(collect_window, mw, now, seen)
      if ok and w then
        windows[#windows + 1] = w
      elseif not ok then
        wezterm.log_error('session: ' .. tostring(w))
      end
    end
    for id in pairs(known) do
      if not seen[id] then known[id] = nil end
    end
    if #windows == 0 then return nil end
    local g, chrome = wezterm.GLOBAL, {}
    for _, k in ipairs { 'sidebar_width', 'sidebar_width_of', 'overview_width', 'overview_width_of' } do
      chrome[k] = type(g[k]) == 'number' and g[k] or nil
    end
    return { version = VERSION, windows = windows, chrome = chrome }
  end
  api.snapshot = snapshot

  local function ensure_dir()
    pcall(wezterm.run_child_process, { 'mkdir', '-p', '-m', '700', DIR })
  end

  -- Writes the document; true when it is in place.
  local function write(doc)
    local text = wezterm.json_encode(doc)
    local tmp = FILE .. '.tmp'
    local f = io.open(tmp, 'wb')
    if not f then
      ensure_dir()
      f = io.open(tmp, 'wb')
    end
    if not f then return false end
    local ok = f:write(text)
    local closed = f:close()
    if not ok or not closed then
      os.remove(tmp)
      return false
    end
    os.rename(FILE, BAK)
    if not os.rename(tmp, FILE) then return false end
    -- The data reaches the disk even when the machine dies right after: a power loss is what
    -- this file is for. Background: the GUI never waits for it.
    pcall(wezterm.background_child_process, { 'sync', '-f', FILE })
    return true
  end

  -- Takes a snapshot (at most every SAVE_SECS) and keeps the file current.
  local function save(now)
    if last_tick and now - last_tick < SAVE_SECS then return end
    last_tick = now
    if wezterm.GLOBAL.layout_busy then return end
    local body = snapshot(now)
    if not body then return end
    local sig = stable(body)
    if sig == last_sig then
      pending_shrink = nil
      return
    end
    local sc = score(body)
    if not committed then committed = score(load_doc(FILE)) end
    if lost(committed, sc) then
      if not pending_shrink or pending_shrink.sig ~= sig then
        pending_shrink = { sig = sig, since = now }
        return
      end
      if now - pending_shrink.since < SHRINK_SECS then return end
    end
    pending_shrink = nil
    local current = load_doc(FILE)
    if current and owner_alive(current.owner) then return end -- another Hivegrid keeps this file
    body.saved = now
    body.owner = { pid = my_pid(), boot = boot_id() }
    if write(body) then
      last_sig, committed = sig, sc
    end
  end
  api.save = save

  -- Restoring -----------------------------------------------------------------------------

  local function usable_cwd(path)
    return nonempty(path) and exists(path) and path or nil
  end

  -- A web page needs the fork, a browser and a location the allowlist accepts; else a shell.
  local function resolve(leaf)
    if leaf.kind ~= 'web' then return leaf end
    local url = web and web.enabled and web.has_chrome and web.normalize_url(leaf.url or 'about:blank')
    if not url then return { kind = 'shell', active = leaf.active } end
    return { kind = 'web', url = url, active = leaf.active }
  end

  -- The arguments of the spawn that starts a pane for `leaf`.
  local function spawn_opts(leaf)
    if leaf.kind == 'web' then
      return { args = { 'hivegrid:web', leaf.url }, set_environment_variables = { HIVEGRID_WEB_PROFILE = S.web.profile } }
    end
    return { cwd = usable_cwd(leaf.cwd) }
  end

  -- Starts what `leaf` ran in its pane and remembers it as the pane's content.
  local function start(pane, leaf, now)
    if leaf.kind == 'omp' then
      pane:send_text(omp_command(leaf, exists) .. '\r')
    end
    known[pane:pane_id()] = { leaf = leaf, at = now, seed = leaf.kind == 'omp' or nil }
  end

  -- Splits `pane` for node `node`: the part b takes `frac` of the cells (dividers apart).
  local function split_for(pane, node)
    local b = resolve(first_leaf(node.b))
    local opts = spawn_opts(b)
    opts.direction = node.dir == 'right' and 'Right' or 'Bottom'
    -- A size of 1.0 or more is cells (below it WezTerm takes whole percents): cells are exact.
    local ok, dims = pcall(pane.get_dimensions, pane)
    local total = ok and type(dims) == 'table' and (node.dir == 'right' and dims.cols or dims.viewport_rows)
    if type(total) == 'number' and total >= 4 then
      opts.size = math.max(1, math.min(total - 2, math.floor(node.frac * (total - 1) + 0.5)))
    else
      opts.size = node.frac
    end
    return pane:split(opts)
  end

  -- Builds the tree below `pane`; returns the pane of the leaf that had the focus.
  local function build(node, pane, now)
    if not node.dir then
      local leaf = resolve(node)
      start(pane, leaf, now)
      return node.active and pane or nil
    end
    local new = split_for(pane, node)
    local a = build(node.a, pane, now)
    local b = build(node.b, new, now)
    return a or b
  end

  local function pending()
    return wezterm.GLOBAL.session_pending or {}
  end

  -- What a restored tab leaves for the status tick: its sidebar view, once the tab has a bar.
  local function remember_chrome(mtab, tdoc, now)
    local tid = tostring(mtab:tab_id())
    if tdoc.dock_off then
      local off = wezterm.GLOBAL.overview_off or {}
      off[tid] = true
      wezterm.GLOBAL.overview_off = off
    end
    if tdoc.sidebar then
      local p = pending()
      p[tid] = { view = tdoc.sidebar }
      wezterm.GLOBAL.session_pending = p
    end
  end

  local function restore_tab(mtab, pane, tdoc, now)
    local focus = build(tdoc.layout, pane, now)
    if tdoc.title then mtab:set_title(tdoc.title) end
    remember_chrome(mtab, tdoc, now)
    return focus
  end

  local function restore_window(wdoc, now)
    local lead = resolve(first_leaf(wdoc.tabs[1].layout))
    local opts = spawn_opts(lead)
    opts.width, opts.height = wdoc.cols, wdoc.rows
    local mtab, pane, mwin = wezterm.mux.spawn_window(opts)
    local tabs, chosen = {}, nil
    local function add(t, p, tdoc)
      local ok, focus = pcall(restore_tab, t, p, tdoc, now)
      if not ok then
        wezterm.log_error('session: a tab could not be restored: ' .. tostring(focus))
        return
      end
      tabs[#tabs + 1] = { tab = t, focus = focus or p }
      if tdoc.active then chosen = #tabs end
    end
    add(mtab, pane, wdoc.tabs[1])
    for i = 2, #wdoc.tabs do
      local tdoc = wdoc.tabs[i]
      local topts = spawn_opts(resolve(first_leaf(tdoc.layout)))
      local ok, t, p = pcall(mwin.spawn_tab, mwin, topts)
      if ok and t then
        add(t, p, tdoc)
      else
        wezterm.log_error('session: a tab could not be spawned: ' .. tostring(t))
      end
    end
    -- Focus: every tab's own pane first, then the tab that was in front.
    chosen = chosen or 1
    for i, t in ipairs(tabs) do
      if i ~= chosen then t.focus:activate() end
    end
    if tabs[chosen] then tabs[chosen].focus:activate() end
    if wdoc.fullscreen then
      local fs = wezterm.GLOBAL.session_fullscreen or {}
      fs[tostring(mwin:window_id())] = true
      wezterm.GLOBAL.session_fullscreen = fs
    end
    return #tabs
  end

  -- Builds the saved session. True when a window was made.
  local function restore(cmd, now)
    if cmd and cmd.args and #cmd.args > 0 then return false end -- a command of the user's own
    local doc = load_doc(FILE) or load_doc(BAK)
    if not doc or owner_alive(doc.owner) then return false end
    local text = read_file(exists(FILE) and FILE or BAK)
    if text then
      local f = io.open(PREV, 'wb')
      if not f then
        ensure_dir()
        f = io.open(PREV, 'wb')
      end
      if f then
        f:write(text)
        f:close()
      end
    end
    for k, v in pairs(doc.chrome) do wezterm.GLOBAL[k] = v end
    local made = 0
    for _, wdoc in ipairs(doc.windows) do
      local ok, n = pcall(restore_window, wdoc, now)
      if ok and n > 0 then
        made = made + 1
      elseif not ok then
        wezterm.log_error('session: a window could not be restored: ' .. tostring(n))
      end
    end
    return made > 0
  end
  api.restore = restore

  wezterm.on('gui-startup', function(cmd)
    local ok, done = pcall(restore, cmd, os.time())
    if not ok then wezterm.log_error('session: restore failed: ' .. tostring(done)) end
    if not (ok and done) then wezterm.mux.spawn_window(cmd or {}) end
  end)

  -- Status tick: what a restore left for later (the sidebar view of a tab, full screen), then
  -- the snapshot.
  wezterm.on('update-status', function(window)
    local now = os.time()
    local fs = wezterm.GLOBAL.session_fullscreen
    local wid = tostring(window:window_id())
    if fs and fs[wid] then
      fs[wid] = nil
      wezterm.GLOBAL.session_fullscreen = fs
      pcall(window.toggle_fullscreen, window)
    end
    -- A restored tab shows its sidebar view once the tab is in front and has its bar (the bar is
    -- made by the sidebar's own status tick, only for the active tab).
    local tab = window:active_tab()
    local p = pending()
    local tid = tab and tostring(tab:tab_id())
    local want = tid and p[tid]
    if want then
      local rec = sidebar_rec(tab:tab_id())
      if rec.content then
        p[tid] = nil
        wezterm.GLOBAL.session_pending = p
      elseif rec.handle and window:active_pane() then
        p[tid] = nil
        wezterm.GLOBAL.session_pending = p
        sb.toggle(window, window:active_pane(), want.view)
      end
    end
    local ok, err = pcall(save, now)
    if not ok then wezterm.log_error('session: ' .. tostring(err)) end
  end)

  return api
end
