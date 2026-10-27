-- Fake `wezterm` module for tests/lua: loaded by `require 'wezterm'` (run.lua preloads it)
-- and recreated by every T.fresh(). Real API names behave like WezTerm's; everything a
-- test inspects or scripts lives under wezterm.stub (recorders, hooks) and wezterm.fake
-- (window/tab/pane builders). Helpers: stub.on_child (script child processes),
-- stub.call/emit (events), stub.perform (run an action), stub.key (binding lookup, last
-- wins like WezTerm), stub.run_timers. tests/lua/fakemux.lua models `wezterm cli`.
local T = require 'tests.lua.t'

local wezterm = {}
local stub = {
  children = {},     -- argv of every run_child_process call
  background = {},   -- argv of every background_child_process call
  handlers = {},     -- event -> { fn, ... } from wezterm.on
  emitted = {},      -- { name, args } of every wezterm.emit
  timers = {},       -- pending time.call_after: { at, fn, seq }
  watch = {},        -- add_to_config_reload_watch_list paths
  logs = {},         -- { level, message }
  reloads = 0,       -- reload_configuration calls
  procinfo = {},     -- pid -> info for procinfo.get_info_for_pid
  panes = {}, tabs = {}, windows = {}, -- mux registry (filled by the fake builders)
  fork = false,      -- true: format accepts the Hivegrid fork's Click/ClickEnd items
  activations = {},  -- pane ids in activation order (pane:activate())
  opened = {},       -- urls handed to wezterm.open_with
  probes = 0,        -- web-pane capability probes run (see run_child_process)
  web = false,       -- true: the wezterm cli has the web pane (`split-pane --url`)
}
wezterm.stub = stub

-- Scripted child processes: replace to answer run_child_process(argv) -> ok, stdout, stderr.
function stub.on_child(_argv) return false, '', '' end

local function copy(t)
  local out = {}
  for k, v in pairs(t) do out[k] = v end
  return out
end

-- The probe theme.has_web runs (`<exe> cli split-pane --help`) goes through io.popen, because a
-- module body cannot call the yielding run_child_process. It is answered here without a child
-- process: counted in stub.probes and answered by stub.web (true: the help lists --url, as the
-- Hivegrid fork with the web pane does). Other popen commands run for real.
T._probe_stub = stub
if not T._popen_wrapped then
  T._popen_wrapped = true
  local real_popen = io.popen
  io.popen = function(cmd, mode)
    if type(cmd) == 'string' and cmd:find(' cli split-pane --help', 1, true) then
      local st = T._probe_stub
      st.probes = st.probes + 1
      local text = st.web and 'OPTIONS: --url <URL>\n' or 'OPTIONS: --cwd <CWD>\n'
      return {
        read = function() return text end,
        close = function() return true end,
      }
    end
    return real_popen(cmd, mode)
  end
end

function wezterm.run_child_process(argv)
  stub.children[#stub.children + 1] = copy(argv)
  local ok, out, err = stub.on_child(argv)
  return ok, out or '', err or ''
end

function wezterm.background_child_process(argv)
  stub.background[#stub.background + 1] = copy(argv)
end

-- State and events ----------------------------------------------------------------
wezterm.GLOBAL = {}

function wezterm.on(event, fn)
  local list = stub.handlers[event] or {}
  list[#list + 1] = fn
  stub.handlers[event] = list
end

-- Calls every handler of `event`; like WezTerm, returns false when a handler did.
function wezterm.emit(event, ...)
  stub.emitted[#stub.emitted + 1] = { name = event, args = table.pack(...) }
  local result = true
  for _, fn in ipairs(stub.handlers[event] or {}) do
    if fn(...) == false then result = false end
  end
  return result
end

-- The return values of the only handler of `event` (errors when there is not exactly one).
function stub.call(event, ...)
  local list = stub.handlers[event] or {}
  assert(#list == 1, string.format('%d handlers for %s', #list, event))
  return list[1](...)
end

-- Actions ---------------------------------------------------------------------------
-- act.Name is { kind = 'Name' } (usable bare, e.g. act.PopKeyTable) and callable:
-- act.Name(args) -> { kind = 'Name', args = args }.
local action_cache = {}
wezterm.action = setmetatable({}, {
  __index = function(_, kind)
    local a = action_cache[kind]
    if not a then
      a = setmetatable({ kind = kind }, {
        __call = function(_, args) return { kind = kind, args = args } end,
      })
      action_cache[kind] = a
    end
    return a
  end,
})

function wezterm.action_callback(fn) return { kind = 'callback', fn = fn } end

-- Runs an action the way a key press would: callbacks get (window, pane, ...); a
-- Multiple runs its members; other actions go to window:perform_action.
function stub.perform(action, window, pane, ...)
  if action.kind == 'callback' then return action.fn(window, pane, ...) end
  if action.kind == 'Multiple' then
    for _, a in ipairs(action.args) do stub.perform(a, window, pane, ...) end
    return
  end
  return window:perform_action(action, pane)
end

-- The action WezTerm would run for key/mods in a list of bindings (config.keys or a key
-- table): like WezTerm's map, a later entry for the same combination wins.
function stub.key(list, key, mods)
  local found
  for _, b in ipairs(list or {}) do
    if b.key == key and (b.mods or 'NONE') == (mods or 'NONE') then found = b.action end
  end
  return found
end

-- JSON --------------------------------------------------------------------------------
local json = {}

local function json_error(s, i, what) error(string.format('json_parse: %s at byte %d', what, i), 0) end

local function skip_ws(s, i)
  return s:find('[^ \t\r\n]', i) or #s + 1
end

local escapes = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f', n = '\n', r = '\r', t = '\t' }

local parse_value

local function parse_string(s, i)
  local out, j = {}, i + 1
  while true do
    local c = s:sub(j, j)
    if c == '' then json_error(s, j, 'unterminated string') end
    if c == '"' then return table.concat(out), j + 1 end
    if c == '\\' then
      local e = s:sub(j + 1, j + 1)
      if e == 'u' then
        local hex = s:sub(j + 2, j + 5)
        if not hex:match('^%x%x%x%x$') then json_error(s, j, 'bad \\u escape') end
        local cp = tonumber(hex, 16)
        j = j + 6
        if cp >= 0xD800 and cp <= 0xDBFF and s:sub(j, j + 1) == '\\u' then
          local lo = tonumber(s:sub(j + 2, j + 5), 16)
          if lo and lo >= 0xDC00 and lo <= 0xDFFF then
            cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
            j = j + 6
          end
        end
        out[#out + 1] = utf8.char(cp)
      elseif escapes[e] then
        out[#out + 1] = escapes[e]
        j = j + 2
      else
        json_error(s, j, 'bad escape')
      end
    elseif c:byte() < 0x20 then
      json_error(s, j, 'control character in string')
    else
      local k = s:find('["\\%c]', j) or #s + 1
      out[#out + 1] = s:sub(j, k - 1)
      j = k
    end
  end
end

parse_value = function(s, i)
  i = skip_ws(s, i)
  local c = s:sub(i, i)
  if c == '{' then
    local obj = {}
    i = skip_ws(s, i + 1)
    if s:sub(i, i) == '}' then return obj, i + 1 end
    while true do
      i = skip_ws(s, i)
      if s:sub(i, i) ~= '"' then json_error(s, i, 'expected object key') end
      local k
      k, i = parse_string(s, i)
      i = skip_ws(s, i)
      if s:sub(i, i) ~= ':' then json_error(s, i, "expected ':'") end
      local v
      v, i = parse_value(s, i + 1)
      obj[k] = v
      i = skip_ws(s, i)
      local d = s:sub(i, i)
      if d == '}' then return obj, i + 1 end
      if d ~= ',' then json_error(s, i, "expected ',' or '}'") end
      i = i + 1
    end
  elseif c == '[' then
    local arr, n = {}, 0
    i = skip_ws(s, i + 1)
    if s:sub(i, i) == ']' then return arr, i + 1 end
    while true do
      local v
      v, i = parse_value(s, i)
      n = n + 1
      arr[n] = v
      i = skip_ws(s, i)
      local d = s:sub(i, i)
      if d == ']' then return arr, i + 1 end
      if d ~= ',' then json_error(s, i, "expected ',' or ']'") end
      i = i + 1
    end
  elseif c == '"' then
    return parse_string(s, i)
  elseif s:sub(i, i + 3) == 'true' then
    return true, i + 4
  elseif s:sub(i, i + 4) == 'false' then
    return false, i + 5
  elseif s:sub(i, i + 3) == 'null' then
    return nil, i + 4
  else
    local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', i)
    if not num or num == '' or num == '-' then json_error(s, i, 'unexpected character') end
    local v = math.tointeger(tonumber(num)) or tonumber(num)
    if v == nil then json_error(s, i, 'bad number') end
    return v, i + #num
  end
end

function json.parse(s)
  if type(s) ~= 'string' then error('json_parse: expected a string', 2) end
  local v, i = parse_value(s, 1)
  i = skip_ws(s, i)
  if i <= #s then json_error(s, i, 'trailing characters') end
  return v
end

local function encode(v, out)
  local t = type(v)
  if v == nil then
    out[#out + 1] = 'null'
  elseif t == 'boolean' then
    out[#out + 1] = tostring(v)
  elseif t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then error('json_encode: non-finite number') end
    out[#out + 1] = math.type(v) == 'integer' and tostring(v) or string.format('%.14g', v)
  elseif t == 'string' then
    out[#out + 1] = '"' .. v:gsub('[%c"\\]', function(c)
      local m = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
      return m[c] or string.format('\\u%04x', c:byte())
    end) .. '"'
  elseif t == 'table' then
    local n, count = #v, 0
    for _ in pairs(v) do count = count + 1 end
    if count == n and n > 0 or count == 0 then
      out[#out + 1] = '['
      for i = 1, n do
        if i > 1 then out[#out + 1] = ',' end
        encode(v[i], out)
      end
      out[#out + 1] = ']'
    else
      local keys = {}
      for k in pairs(v) do keys[#keys + 1] = tostring(k) end
      table.sort(keys)
      out[#out + 1] = '{'
      for i, k in ipairs(keys) do
        if i > 1 then out[#out + 1] = ',' end
        encode(k, out)
        out[#out + 1] = ':'
        local val = v[k]
        if val == nil then val = v[tonumber(k)] end
        encode(val, out)
      end
      out[#out + 1] = '}'
    end
  else
    error('json_encode: cannot encode ' .. t)
  end
end

function json.encode(v)
  local out = {}
  encode(v, out)
  return table.concat(out)
end

wezterm.json_parse, wezterm.json_encode = json.parse, json.encode
wezterm.serde = { json_decode = json.parse, json_encode = json.encode }

-- Files and paths ---------------------------------------------------------------------
-- Full paths of every entry (dotfiles too), sorted; errors when `path` is not a directory.
function wezterm.read_dir(path)
  local q = T.sh_quote(path)
  local p = io.popen('test -d ' .. q .. ' && ls -A ' .. q .. ' 2>/dev/null')
  local text = p:read('a')
  local ok = p:close()
  if not ok then error('read_dir: cannot read ' .. tostring(path), 2) end
  local out = {}
  for name in text:gmatch('[^\n]+') do out[#out + 1] = path .. '/' .. name end
  table.sort(out)
  return out
end

wezterm.home_dir = os.getenv('HOME') or T.sandbox
wezterm.executable_dir = T.sandbox .. '/no-wezterm-bin' -- holds no wezterm: cli falls back to 'wezterm'
wezterm.config_dir = T.root .. '/config'
wezterm.config_file = T.root .. '/config/wezterm.lua'
wezterm.version = '20240203-stub'
wezterm.target_triple = 'x86_64-unknown-linux-gnu'

function wezterm.hostname() return 'testhost' end
function wezterm.add_to_config_reload_watch_list(p) stub.watch[#stub.watch + 1] = p end
function wezterm.reload_configuration() stub.reloads = stub.reloads + 1 end
function wezterm.open_with(url) stub.opened[#stub.opened + 1] = url end
function wezterm.config_builder() return {} end
function wezterm.font(...) return { font = { ... } } end
function wezterm.font_with_fallback(...) return { font_with_fallback = { ... } } end
function wezterm.sleep_ms() end
function wezterm.shell_join_args(args)
  local out = {}
  for i, a in ipairs(args) do out[i] = T.sh_quote(a) end
  return table.concat(out, ' ')
end

for _, level in ipairs { 'info', 'warn', 'error' } do
  wezterm['log_' .. level] = function(...)
    local parts = table.pack(...)
    for i = 1, parts.n do parts[i] = tostring(parts[i]) end
    stub.logs[#stub.logs + 1] = { level = level, message = table.concat(parts, ' ') }
  end
end

wezterm.procinfo = {}
function wezterm.procinfo.get_info_for_pid(pid) return stub.procinfo[pid] end
function wezterm.procinfo.current_working_dir_for_pid(pid)
  local i = stub.procinfo[pid]
  return i and i.cwd
end

-- Text ----------------------------------------------------------------------------
-- Visible text of a format item list; errors on an unknown item, like WezTerm. The
-- fork's Click/ClickEnd items are accepted only when stub.fork is true.
function wezterm.format(items)
  local out = {}
  for _, it in ipairs(items) do
    if it == 'ClickEnd' then
      if not stub.fork then error('format: unknown FormatItem ClickEnd') end
    elseif type(it) == 'table' then
      local k = next(it)
      if k == 'Text' then
        out[#out + 1] = it.Text
      elseif k == 'Click' then
        if not stub.fork then error('format: unknown FormatItem Click') end
      elseif not (k == 'Foreground' or k == 'Background' or k == 'Attribute') then
        error('format: unknown FormatItem ' .. tostring(k))
      end
    elseif it ~= 'ResetAttributes' then
      error('format: unknown FormatItem ' .. tostring(it))
    end
  end
  return table.concat(out)
end

function wezterm.column_width(s) return utf8.len(s) or #s end
function wezterm.truncate_right(s, n)
  if (utf8.len(s) or #s) <= n then return s end
  if n <= 0 then return '' end
  return s:sub(1, (utf8.offset(s, n + 1) or #s + 1) - 1)
end
function wezterm.truncate_left(s, n)
  local len = utf8.len(s) or #s
  if len <= n then return s end
  if n <= 0 then return '' end
  return s:sub(utf8.offset(s, len - n + 1))
end
function wezterm.pad_left(s, n) return string.rep(' ', n - (utf8.len(s) or #s)) .. s end
function wezterm.pad_right(s, n) return s .. string.rep(' ', n - (utf8.len(s) or #s)) end

-- Every nerdfont name is one private-use glyph.
wezterm.nerdfonts = setmetatable({}, { __index = function() return '\u{f0000}' end })

-- Time ----------------------------------------------------------------------------
-- time.now() follows T.clock(); :format supports strftime plus %s (epoch) and %.Nf
-- (fraction of the second, '.123' style).
local function time_obj(t)
  return {
    format = function(_, fmt)
      local sec = math.floor(t)
      fmt = fmt:gsub('%%%.(%d)f', function(d)
        d = tonumber(d)
        local frac = math.floor((t - sec) * 10 ^ d + 0.5)
        return string.format('.%0' .. d .. 'd', frac)
      end)
      fmt = fmt:gsub('%%s', tostring(sec))
      return os.date(fmt, sec)
    end,
  }
end

local seq = 0
wezterm.time = {}
function wezterm.time.now() return time_obj(T.now()) end
function wezterm.time.call_after(delay, fn)
  seq = seq + 1
  stub.timers[#stub.timers + 1] = { at = T.now() + delay, fn = fn, seq = seq }
end

-- Runs pending timers in due order (also ones they schedule), at most `max` (default
-- 100); with a fake clock set the clock advances to each timer's time. Returns the count.
function stub.run_timers(max)
  max = max or 100
  local ran = 0
  while #stub.timers > 0 and ran < max do
    table.sort(stub.timers, function(a, b)
      if a.at ~= b.at then return a.at < b.at end
      return a.seq < b.seq
    end)
    local tm = table.remove(stub.timers, 1)
    if T._now and tm.at > T._now then T._now = tm.at end
    tm.fn()
    ran = ran + 1
  end
  return ran
end

-- The Hivegrid picker (config/picker.lua) the last ShowOverlayCommand of `window` opened:
-- { spec = the decoded request, args, answer = function(id) }. answer(id) replies like
-- bin/hivegrid_picker.py (user var hivegrid_pick from the overlay pane; id nil = closed
-- without a choice) and runs the timers, so the caller's callback has run on return.
function stub.picker(window)
  local last
  for _, a in ipairs(window.actions) do
    if a.action.kind == 'ShowOverlayCommand' then last = a.action end
  end
  assert(last, 'no picker overlay was opened')
  local spec = wezterm.json_parse(last.args.set_environment_variables.HIVEGRID_PICKER_SPEC)
  local overlay = wezterm.fake.pane {}
  return {
    spec = spec, args = last.args,
    answer = function(id)
      wezterm.emit('user-var-changed', window, overlay, 'hivegrid_pick',
        wezterm.json_encode { request = spec.request, id = id })
      stub.run_timers()
    end,
  }
end

-- Mux -----------------------------------------------------------------------------
wezterm.mux = {}
function wezterm.mux.get_pane(id) return stub.panes[id] end
function wezterm.mux.get_tab(id) return stub.tabs[id] end
function wezterm.mux.get_window(id) return stub.windows[id] and stub.windows[id]:mux_window() end
function wezterm.mux.all_windows()
  local out = {}
  for _, w in pairs(stub.windows) do out[#out + 1] = w:mux_window() end
  table.sort(out, function(a, b) return a:window_id() < b:window_id() end)
  return out
end

-- Fake objects ----------------------------------------------------------------------
-- fake.pane{ id, title, text, cwd, process_name, process_info, unseen, user_vars, dims }
--   cwd: a path string (becomes a file: Url object) or a table / userdata a test wants
--   returned as is, e.g. wz.fake.url('https://example.com/')
--   records: p.sent (send_text), p.activated (count), p.moved_to_new_tab, p.splits (pane:split
--   requests; the new pane of a `hivegrid:web <url>` request is a web pane)
-- fake.tab{ id, title, panes = { { pane = p | id = n, left, top, width, height,
--   active, zoomed }, ... } }  -> tab.rows is the mutable layout; tab:set_rows(rows)
-- fake.window{ id, tabs = { tab, ... }, active = index, focused, dims, key_table }
--   records: w.toasts { title, msg, url, ms }, w.actions { action, pane },
--   w.status.left/right; perform_action applies ActivateKeyTable/PopKeyTable.
local fake = {}
wezterm.fake = fake
local next_id = 1000

-- A page URL as WezTerm's Url object has it: scheme, host (without the port), port and path
-- fields, tostring giving the whole URL, and no href field.
function fake.url(href)
  local scheme, authority, path = href:match('^(%a[%w+.-]*)://([^/?#]*)([^?#]*)')
  local u = { scheme = scheme or href:match('^(%a[%w+.-]*):'), path = path }
  if authority then
    u.host, u.port = authority:match('^(.-):(%d+)$')
    u.host = u.host or authority
    u.port = tonumber(u.port)
  end
  return setmetatable(u, { __tostring = function() return href end })
end

function fake.pane(o)
  o = o or {}
  local p = { sent = {}, activated = 0, moved_to_new_tab = 0, splits = {}, o = o }
  if not o.id then
    next_id = next_id + 1
    o.id = next_id
  end
  function p:pane_id() return o.id end
  function p:get_title() return o.title or '' end
  function p:get_lines_as_text(_) return o.text or '' end
  function p:get_logical_lines_as_text(_) return o.text or '' end
  function p:get_text_from_region() return o.text or '' end
  function p:get_current_working_dir()
    if type(o.cwd) == 'string' then
      return setmetatable({ file_path = o.cwd, path = o.cwd, scheme = 'file' },
        { __tostring = function() return 'file://' .. o.cwd end })
    end
    return o.cwd
  end
  function p:get_foreground_process_name() return o.process_name end
  function p:get_foreground_process_info() return o.process_info end
  function p:has_unseen_output() return o.unseen or false end
  function p:get_user_vars() return o.user_vars or {} end
  function p:is_alt_screen_active() return o.alt_screen or false end
  function p:get_dimensions()
    return o.dims or { cols = 80, viewport_rows = 24, scrollback_rows = 24, physical_top = 0 }
  end
  function p:get_domain_name() return o.domain or 'local' end
  function p:send_text(s) self.sent[#self.sent + 1] = s end
  function p:send_paste(s) self.sent[#self.sent + 1] = s end
  function p:activate()
    self.activated = self.activated + 1
    stub.activations[#stub.activations + 1] = o.id
    local tab = self._tab
    if tab then
      for _, r in ipairs(tab.rows) do r.active = r.pane == self end
    end
  end
  function p:move_to_new_tab() self.moved_to_new_tab = self.moved_to_new_tab + 1 end
  -- Records the request and adds the new pane to the tab, taking the second half of this
  -- pane's area (right of it, or below it).
  function p:split(opts)
    self.splits[#self.splits + 1] = opts
    next_id = next_id + 1
    local po = { id = next_id }
    local url = opts.args and opts.args[1] == 'hivegrid:web' and opts.args[2]
    if url then
      po.user_vars = { hivegrid_web = '1' }
      po.cwd = fake.url(url)
    end
    local new = fake.pane(po)
    local tab = self._tab
    if tab then
      for _, r in ipairs(tab.rows) do
        if r.pane == self then
          local row = { pane = new, left = r.left, top = r.top, width = r.width, height = r.height, active = false, zoomed = false }
          if opts.direction == 'Right' then
            row.width = r.width // 2
            r.width = r.width - row.width - 1
            row.left = r.left + r.width + 1
          else
            row.height = r.height // 2
            r.height = r.height - row.height - 1
            row.top = r.top + r.height + 1
          end
          new._tab = tab
          tab.rows[#tab.rows + 1] = row
          break
        end
      end
    end
    return new
  end
  function p:tab() return self._tab end
  function p:window() return self._tab and self._tab._window and self._tab._window:mux_window() end
  stub.panes[o.id] = p
  return p
end

function fake.tab(o)
  o = o or {}
  local t = { o = o }
  if not o.id then
    next_id = next_id + 1
    o.id = next_id
  end
  function t:set_rows(rows)
    local out = {}
    for i, r in ipairs(rows or {}) do
      local pane = r.pane or stub.panes[r.id] or fake.pane { id = r.id }
      pane._tab = t
      out[i] = {
        pane = pane, left = r.left or 0, top = r.top or 0, width = r.width or 80,
        height = r.height or 24, active = r.active or false, zoomed = r.zoomed or false,
      }
    end
    if #out > 0 then
      local any = false
      for _, r in ipairs(out) do any = any or r.active end
      if not any then out[1].active = true end
    end
    t.rows = out
  end
  t:set_rows(o.panes)
  function t:tab_id() return o.id end
  function t:get_title() return o.title or '' end
  function t:set_title(s) o.title = s end
  function t:panes()
    local out = {}
    for i, r in ipairs(self.rows) do out[i] = r.pane end
    return out
  end
  function t:panes_with_info()
    local out = {}
    for i, r in ipairs(self.rows) do
      out[i] = {
        index = i - 1, is_active = r.active, is_zoomed = r.zoomed, left = r.left, top = r.top,
        width = r.width, height = r.height, pixel_width = r.width * 8, pixel_height = r.height * 16,
        pane = r.pane,
      }
    end
    return out
  end
  function t:active_pane()
    for _, r in ipairs(self.rows) do
      if r.active then return r.pane end
    end
  end
  function t:window() return self._window and self._window:mux_window() end
  function t:activate()
    local w = self._window
    if w then
      for i, x in ipairs(w.tabs) do
        if x == self then w.active = i end
      end
    end
  end
  stub.tabs[o.id] = t
  return t
end

function fake.window(o)
  o = o or {}
  local w = { o = o, tabs = o.tabs or {}, active = o.active or 1, toasts = {}, actions = {},
    status = {}, key_table = o.key_table, overrides = nil }
  if not o.id then
    next_id = next_id + 1
    o.id = next_id
  end
  for _, t in ipairs(w.tabs) do t._window = w end
  function w:window_id() return o.id end
  function w:is_focused() return o.focused ~= false end
  function w:active_tab() return self.tabs[self.active] end
  function w:active_pane()
    if self.active_pane_override ~= nil then return self.active_pane_override or nil end
    local t = self:active_tab()
    return t and t:active_pane()
  end
  function w:toast_notification(title, msg, url, ms)
    self.toasts[#self.toasts + 1] = { title = title, msg = msg, url = url, ms = ms }
  end
  function w:perform_action(action, pane)
    self.actions[#self.actions + 1] = { action = action, pane = pane }
    if action.kind == 'ActivateKeyTable' then
      self.key_table = action.args.name
    elseif action.kind == 'PopKeyTable' then
      self.key_table = nil
    end
  end
  function w:active_key_table() return self.key_table end
  function w:active_workspace() return 'default' end
  function w:set_left_status(s) self.status.left = s end
  function w:set_right_status(s) self.status.right = s end
  function w:get_dimensions()
    return o.dims or { pixel_width = 1920, pixel_height = 1080, dpi = 96, is_full_screen = false }
  end
  function w:get_config_overrides() return self.overrides end
  function w:set_config_overrides(t) self.overrides = t end
  function w:effective_config() return o.config or {} end
  function w:get_appearance() return 'Dark' end
  function w:gui_window() return self end
  local mux = {}
  function mux:window_id() return o.id end
  function mux:tabs() return w.tabs end
  function mux:active_tab() return w:active_tab() end
  function mux:active_pane() return w:active_pane() end
  function mux:gui_window() return w end
  function mux:get_workspace() return 'default' end
  function mux:tabs_with_info()
    local out = {}
    for i, t in ipairs(w.tabs) do
      out[i] = { index = i - 1, is_active = i == w.active, tab = t }
    end
    return out
  end
  function w:mux_window() return mux end
  stub.windows[o.id] = w
  return w
end

-- One window with one tab of `rows` (fake.tab panes format); returns window, tab.
function fake.grid(rows, wopts)
  local tab = fake.tab { panes = rows }
  wopts = wopts or {}
  wopts.tabs = { tab }
  return fake.window(wopts), tab
end

return wezterm
