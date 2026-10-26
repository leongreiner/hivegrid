-- Left sidebar: file explorer (yazi) or git view (hivegrid-git: every repository of the
-- directory, lazygit in the one you pick) docked beside a narrow
-- handle pane. Wire: local sb = require('sidebar')(config, wezterm)
--   Ctrl+F explorer   Ctrl+G git view   LEADER+Tab hide/show, remembering the view
-- Layout is [handle][sidebar][leftmost pane...]: both are ordinary leaf splits of the
-- leftmost grid pane, so a killed sidebar's columns return to that pane. `--top-level`
-- is avoided: in WezTerm 20240203 it on a tab with 2+ panes leaves the pane tree
-- (cells + 1) columns wider than the window.
-- The handle script turns on mouse reporting, so WezTerm forwards clicks to it and
-- no mouse_bindings are needed; every default selection/link/bypass binding stays.
-- The script announces a left press as the user var `sidebar_toggle`.
local M = {}

local function build(config, wezterm)
  config.keys = config.keys or {}
  config.mouse_bindings = config.mouse_bindings or {}

  local HANDLE_COLS, MIN_COLS, FRACTION, MIN_GRID = 3, 28, 0.22, 20
  local RUNTIME = os.getenv('XDG_RUNTIME_DIR') or '/tmp'
  local VIEWS = {
    files = { bin = wezterm.home_dir .. '/.local/bin/yazi' },
    git = { bin = wezterm.home_dir .. '/.local/bin/hivegrid-git' },
  }
  local theme = require 'theme'
  local COLORS = { accent = theme.P.accent, muted = theme.P.muted }

  -- Runs inside the handle pane. State file: "<open|closed> <files|git>", written by
  -- Lua, polled here so a resize (WINCH) or a state change redraws.
  local HANDLE_SCRIPT = [==[
st="${XDG_RUNTIME_DIR:-/tmp}/wezterm-sidebar-$WEZTERM_PANE"
trap 'rm -f "$st"' EXIT
trap '' INT
trap 'dirty=1' WINCH
stty -echo
printf '\e[?1049h\e[?25l\e[?7l\e[?1000h\e[?1006h'
draw() {
  local s=${1%% *} v=${1#* } r c g=▶ lab col='116;135;173' i out
  read -r r c < <(stty size </dev/tty)
  [[ $s == open ]] && { g=◀; col='201;214;238'; }
  [[ $v == git ]] && lab=GIT || lab=EXPLORER
  out=$'\e[0m\e[48;2;15;26;48m\e[2J\e[2;2H\e[1;38;2;79;157;255m'$g
  out+=$'\e[0;48;2;15;26;48;38;2;'$col'm'
  for ((i = 0; i < ${#lab} && i + 5 < r; i++)); do out+=$'\e['$((4 + i))';2H'${lab:i:1}; done
  out+=$'\e[0;48;2;7;13;25m'
  for ((i = 1; i <= r; i++)); do out+=$'\e['$i';'$c'H '; done
  printf %s "$out"
}
dirty=1 prev=
while :; do
  cur='closed files'; [[ -r $st ]] && read -r cur <"$st"
  if [[ $dirty == 1 || $cur != "$prev" ]]; then dirty=0 prev=$cur; draw "$cur"; fi
  IFS= read -rsn1 -t 0.3 k; rc=$?
  ((rc == 0)) || { ((rc > 128)) && continue; break; }
  [[ $k == $'\e' ]] || continue
  seq=; while IFS= read -rsn1 -t 0.05 ch; do seq+=$ch; [[ $ch == [Mm] ]] && break; done
  [[ $seq == '[<0;'*M ]] && printf '\e]1337;SetUserVar=sidebar_toggle=%s\a' "$(printf %s "$RANDOM$SECONDS" | base64)"
done
]==]

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

  -- Columns for the sidebar, or nil when the anchor pane cannot spare them. `saved`
  -- is the last width; the cap keeps MIN_GRID columns for the anchor itself.
  local function width_for(rs, anchor, needs_handle, saved)
    local total = 0
    for _, r in ipairs(rs) do total = math.max(total, r.left + r.width) end
    local room = anchor.width - (needs_handle and HANDLE_COLS + 1 or 0) - 1 - MIN_GRID
    local w = math.min(saved or math.max(MIN_COLS, math.floor(total * FRACTION)), room)
    if w >= 8 then return w end
  end

  local function total_width(rs)
    local total = 0
    for _, r in ipairs(rs) do total = math.max(total, r.left + r.width) end
    return total
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

  -- The next resize that brings the sidebar panes to their intended widths, or nil. The
  -- handle goes first: AdjustPaneSize on it only trades columns with the subtree beside
  -- it, so the content width is untouched by that step.
  local function next_step(rs, rec, saved)
    local h = rec.handle and find(rs, rec.handle)
    if not h then return nil end
    if h.width ~= HANDLE_COLS then return { id = h.id, delta = HANDLE_COLS - h.width } end
    local c = rec.content and find(rs, rec.content)
    local w = c and content_target(rs, c, saved)
    if w and w ~= c.width then return { id = c.id, delta = w - c.width } end
  end

  -- Drops ids of panes that no longer exist (killed, or quit with `q`).
  local function reconcile(rec, rs)
    local r = { view = rec.view, handle = rec.handle, content = rec.content, last = rec.last }
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
    return a.view == b.view and a.handle == b.handle and a.content == b.content and a.last == b.last
  end

  local function write_state(rec)
    local f = rec.handle and io.open(('%s/wezterm-sidebar-%d'):format(RUNTIME, rec.handle), 'w')
    if f then
      f:write((rec.content and 'open ' or 'closed ') .. (rec.view or 'files') .. '\n')
      f:close()
    end
  end

  -- `wezterm cli`, aimed at the mux this pane belongs to so several GUI instances
  -- cannot mix up pane ids.
  local cli_bin = wezterm.executable_dir .. '/wezterm'
  do
    local f = io.open(cli_bin, 'rb')
    if f then f:close() else cli_bin = 'wezterm' end
  end
  local function make_cli(pane)
    local sock
    local ok, info = pcall(function() return pane:get_foreground_process_info() end)
    if ok and info and info.pid then
      local f = io.open('/proc/' .. info.pid .. '/environ', 'rb')
      if f then
        sock = f:read('a'):match('WEZTERM_UNIX_SOCKET=([^\0]+)')
        f:close()
      end
    end
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

  -- Directory the sidebar view starts in: the pane's directory, else home. The git view
  -- scans it for repositories itself (the one around it and those below it).
  local function dir_of(pane)
    return pane and pane_dir(pane) or wezterm.home_dir
  end

  -- Window rescales ---------------------------------------------------------------------
  -- WezTerm redistributes columns across the pane tree when the window changes size (a
  -- move to another monitor), which rescales the handle and the content. A rescale is
  -- told from a divider drag by the tab's total width: it changes only with the window.
  -- `seen` is the state after the last settle; a width saved from a rescale would stick.
  local function seen_all() return wezterm.GLOBAL.sidebar_seen or {} end
  local function rescale_pending(tab_id, rs)
    local s = seen_all()[tostring(tab_id)]
    return s ~= nil and s.total ~= total_width(rs)
  end

  local settling = false

  -- Brings handle and content back to their intended widths and records what was seen.
  -- Idempotent: runs `wezterm cli` only when a width is off. AdjustPaneSize acts on the
  -- tab's active pane, so the pane to resize is activated first and the user's restored.
  local function settle(window, tab, rs, rec)
    if settling or not rec.handle or not find(rs, rec.handle) then return end
    local key = tostring(tab:tab_id())
    local seen = seen_all()[key]
    local total = total_width(rs)
    local rescaled = seen ~= nil and seen.total ~= total
    local c = rec.content and find(rs, rec.content)
    local saved = wezterm.GLOBAL.sidebar_width
    if c and not rescaled
        and (saved == nil or (seen and seen.content_id == c.id and seen.content_w ~= c.width)) then
      saved = c.width
      wezterm.GLOBAL.sidebar_width = saved
    end

    local step = next_step(rs, rec, saved)
    if step then
      settling = true
      local ok = pcall(function()
        local pane = window:active_pane()
        local cli = make_cli(pane)
        local prev
        for _ = 1, 3 do
          if not step or (prev and prev.id == step.id and prev.delta == step.delta) then break end
          cli('activate-pane', '--pane-id', step.id)
          cli('adjust-pane-size', '--pane-id', step.id, '--amount', math.abs(step.delta),
            step.delta > 0 and 'Right' or 'Left')
          prev = step
          rs = rows(tab:panes_with_info())
          step = next_step(rs, rec, saved)
        end
        cli('activate-pane', '--pane-id', pane:pane_id())
      end)
      settling = false
      if not ok then return end
      rs = rows(tab:panes_with_info())
      c = rec.content and find(rs, rec.content)
    end

    local all = seen_all()
    all[key] = {
      total = total_width(rs), content_id = c and c.id or nil, content_w = c and c.width or nil,
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
    settle(window, tab, rs, reconcile(load(tab:tab_id()), rs))
  end)

  -- Actions ---------------------------------------------------------------------------
  local function hide(cli, rec, rs, tab_id)
    local c = find(rs, rec.content)
    cli('kill-pane', '--pane-id', rec.content)
    rec.content = nil
    if c and not rescale_pending(tab_id, rs) then wezterm.GLOBAL.sidebar_width = c.width end
  end

  -- The explorer's own config home keeps the sidebar's keys and click handling out of a
  -- global ~/.config/yazi.
  local YAZI_CONFIG_HOME = wezterm.home_dir .. '/.config/hivegrid/yazi'

  local function show(cli, rec, rs, src, anchor, window)
    local w = anchor and width_for(rs, anchor, not rec.handle, wezterm.GLOBAL.sidebar_width)
    if not w then
      window:toast_notification('WezTerm sidebar', 'The leftmost pane is too narrow', nil, 3000)
      return
    end
    if not rec.handle then
      local ok, out = cli('split-pane', '--left', '--cells', HANDLE_COLS,
        '--pane-id', anchor.id, '--', 'bash', '-c', HANDLE_SCRIPT)
      if not ok then return end
      rec.handle = tonumber(out)
    end
    local dir = dir_of(src)
    -- PWD is set explicitly because tools such as yazi prefer the inherited $PWD over the cwd.
    local cmd = { 'env', 'PWD=' .. dir }
    if rec.view == 'files' then cmd[#cmd + 1] = 'YAZI_CONFIG_HOME=' .. YAZI_CONFIG_HOME end
    cmd[#cmd + 1] = VIEWS[rec.view].bin
    local ok, out = cli('split-pane', '--left', '--cells', w, '--cwd', dir,
      '--pane-id', anchor.id, '--', table.unpack(cmd))
    if ok then
      rec.content = tonumber(out)
      wezterm.GLOBAL.sidebar_width = wezterm.GLOBAL.sidebar_width or w
      cli('activate-pane', '--pane-id', rec.content)
    end
  end

  -- want = 'files' | 'git' to show that view (or hide it if already shown); nil to
  -- toggle whatever view was last used.
  local function apply(window, pane, want)
    local tab = window:active_tab()
    local rs = rows(tab:panes_with_info())
    local rec = reconcile(load(tab:tab_id()), rs)
    local function skip(id) return id == rec.handle or id == rec.content end
    local cli = make_cli(pane)

    local src = not skip(pane:pane_id()) and pane
      or (find(rs, rec.last) or leftmost(rs, skip) or { pane = pane }).pane
    if not skip(src:pane_id()) then rec.last = src:pane_id() end

    if rec.content and (want == nil or want == rec.view) then
      hide(cli, rec, rs, tab:tab_id())
      local back = find(rs, rec.last) or leftmost(rs, skip)
      if back then cli('activate-pane', '--pane-id', back.id) end
    else
      if rec.content then
        hide(cli, rec, rs, tab:tab_id())
        rs = rows(tab:panes_with_info())
      end
      rec.view = want or rec.view or 'files'
      show(cli, rec, rs, src, anchor_for(rs, rec, skip), window)
    end
    save(tab:tab_id(), rec)
    write_state(rec)
  end

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
      content_target = content_target, next_step = next_step,
    },
  }

  api.is_sidebar = M.is_sidebar

  function api.toggle(window, pane, view) apply(window, pane, view) end

  -- Reconciles state with the live tab, so call it from update-status. Returns
  -- wezterm.format items for a chip such as "◀ files": accent colour while open, muted when
  -- hidden, nothing before the sidebar was ever opened in this tab.
  function api.status(window)
    local tab = window:active_tab()
    local rs = rows(tab:panes_with_info())
    local old = load(tab:tab_id())
    local rec = reconcile(old, rs)
    for _, r in ipairs(rs) do
      if r.active and r.id ~= rec.handle and r.id ~= rec.content then rec.last = r.id end
    end
    settle(window, tab, rs, rec)
    if not same(old, rec) then
      save(tab:tab_id(), rec)
      write_state(rec)
    end
    if not rec.handle then return {} end
    return theme.pill({
      { Foreground = { Color = rec.content and COLORS.accent or COLORS.muted } },
      { Text = (rec.content and '◀ ' or '▶ ') .. (rec.view == 'git' and 'git' or 'files') },
    }, rec.content and theme.P.raised or theme.P.pill)
  end

  -- The explorer announces a double click as the user var hivegrid_open_dir (an absolute
  -- path). A wide target pane gets the terminal on its right, a narrow one below it.
  local RIGHT_MIN_COLS = 80
  local function open_terminal(window, pane, path)
    local tab = pane:tab() or window:active_tab()
    local rs = rows(tab:panes_with_info())
    local rec = reconcile(load(tab:tab_id()), rs)
    if not rec.content or rec.content ~= pane:pane_id() then return end
    if type(path) ~= 'string' or path:sub(1, 1) ~= '/' or path:find('%z') then return end

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

    local function skip(id) return id == rec.handle or id == rec.content end
    local target = find(rs, rec.last) or leftmost(rs, skip)
    if not target then return end
    local cli = make_cli(pane)
    local ok, out = cli('split-pane', target.width >= RIGHT_MIN_COLS and '--right' or '--bottom',
      '--cwd', dir, '--pane-id', target.id)
    local id = ok and tonumber(out)
    if not id then return end
    cli('activate-pane', '--pane-id', id)
    rec.last = id
    save(tab:tab_id(), rec)
  end

  wezterm.on('user-var-changed', function(window, pane, name, value)
    if name == 'sidebar_toggle' and api.is_sidebar(pane:pane_id()) then
      apply(window, pane, nil)
    elseif name == 'hivegrid_open_dir' then
      open_terminal(window, pane, value)
    end
  end)

  return api
end

-- Standalone so other modules can skip sidebar panes without holding the api table:
-- require('sidebar').is_sidebar(pane_id)
function M.is_sidebar(pane_id)
  for _, r in pairs(require('wezterm').GLOBAL.sidebar or {}) do
    if r.handle == pane_id or r.content == pane_id then return true end
  end
  return false
end

return setmetatable(M, { __call = function(_, config, wezterm) return build(config, wezterm) end })
