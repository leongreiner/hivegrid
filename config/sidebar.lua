-- Left sidebar: file explorer (yazi) or git view (lazygit) docked beside a narrow
-- handle pane. Wire: local sb = require('sidebar')(config, wezterm)
--   LEADER+f explorer   LEADER+t git view   LEADER+Tab hide/show, remembering the view
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
    git = { bin = wezterm.home_dir .. '/.local/bin/lazygit' },
  }
  local COLORS = { accent = '#3b8eea', muted = '#8b949e' }

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
  local s=${1%% *} v=${1#* } r c g=▶ lab col='139;148;158' i out
  read -r r c < <(stty size </dev/tty)
  [[ $s == open ]] && { g=◀; col='204;204;204'; }
  [[ $v == git ]] && lab=GIT || lab=EXPLORER
  out=$'\e[0m\e[48;2;37;37;38m\e[2J\e[2;2H\e[1;38;2;59;142;234m'$g
  out+=$'\e[0;48;2;37;37;38;38;2;'$col'm'
  for ((i = 0; i < ${#lab} && i + 5 < r; i++)); do out+=$'\e['$((4 + i))';2H'${lab:i:1}; done
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

  local function dir_of(pane, view)
    local cwd = pane and pane:get_current_working_dir()
    local dir = cwd and cwd.file_path or wezterm.home_dir
    if view == 'git' then
      local ok, out = wezterm.run_child_process { 'git', '-C', dir, 'rev-parse', '--show-toplevel' }
      if ok and out ~= '' then return (out:gsub('%s+$', '')) end
    end
    return dir
  end

  -- Actions ---------------------------------------------------------------------------
  local function hide(cli, rec, rs)
    local c = find(rs, rec.content)
    cli('kill-pane', '--pane-id', rec.content)
    rec.content = nil
    if c then wezterm.GLOBAL.sidebar_width = c.width end
  end

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
    local dir = dir_of(src, rec.view)
    -- PWD is set explicitly because tools such as yazi prefer the inherited $PWD over the cwd.
    local ok, out = cli('split-pane', '--left', '--cells', w, '--cwd', dir,
      '--pane-id', anchor.id, '--', 'env', 'PWD=' .. dir, VIEWS[rec.view].bin)
    if ok then
      rec.content = tonumber(out)
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
      hide(cli, rec, rs)
      local back = find(rs, rec.last) or leftmost(rs, skip)
      if back then cli('activate-pane', '--pane-id', back.id) end
    else
      if rec.content then
        hide(cli, rec, rs)
        rs = rows(tab:panes_with_info())
      end
      rec.view = want or rec.view or 'files'
      show(cli, rec, rs, src, anchor_for(rs, rec, skip), window)
    end
    save(tab:tab_id(), rec)
    write_state(rec)
  end

  for _, k in ipairs { { 'f', 'files' }, { 't', 'git' }, { 'Tab', nil } } do
    table.insert(config.keys, {
      key = k[1], mods = 'LEADER',
      action = wezterm.action_callback(function(window, pane) apply(window, pane, k[2]) end),
    })
  end

  local api = {
    internals = {
      rows = rows, leftmost = leftmost, anchor_for = anchor_for,
      width_for = width_for, reconcile = reconcile,
    },
  }

  api.is_sidebar = M.is_sidebar

  function api.toggle(window, pane, view) apply(window, pane, view) end

  -- Reconciles state with the live tab, so call it from update-status. Returns
  -- wezterm.format items such as " [files] ": accent colour while open, muted when
  -- hidden, nothing before the sidebar was ever opened in this tab.
  function api.status(window)
    local tab = window:active_tab()
    local rs = rows(tab:panes_with_info())
    local old = load(tab:tab_id())
    local rec = reconcile(old, rs)
    for _, r in ipairs(rs) do
      if r.active and r.id ~= rec.handle and r.id ~= rec.content then rec.last = r.id end
    end
    local c = rec.content and find(rs, rec.content)
    if c and wezterm.GLOBAL.sidebar_width ~= c.width then wezterm.GLOBAL.sidebar_width = c.width end
    if not same(old, rec) then
      save(tab:tab_id(), rec)
      write_state(rec)
    end
    if not rec.handle then return {} end
    return {
      { Foreground = { Color = rec.content and COLORS.accent or COLORS.muted } },
      { Text = ' [' .. (rec.view == 'git' and 'git' or 'files') .. '] ' },
    }
  end

  wezterm.on('user-var-changed', function(window, pane, name)
    if name == 'sidebar_toggle' and api.is_sidebar(pane:pane_id()) then
      apply(window, pane, nil)
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
