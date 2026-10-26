-- Minimal test API for tests/lua (run by tests/lua/run.lua; no globals).
--   local T = require 'tests.lua.t'
--   T.test('name', function(wz) ... end)   -- wz: a fresh wezterm stub (T.fresh() ran first)
--   T.xfail('name', 'reason', fn)   -- known bug: must fail until fixed, then becomes T.test
--   T.eq(actual, expected[, msg])  deep compare, reports the first differing path
--   T.ok(v[, msg])   T.near(a, b[, eps, msg])   T.raises(fn[, lua_pattern]) -> message
--   T.fresh() -> new stub: drops every module loaded since startup (config modules and
--     wezterm), resets env overrides, the clock and gives HOME/XDG dirs fresh temp dirs
--   T.setenv(k, v)  overrides os.getenv (v = nil -> unset)      T.clock(epoch | nil)
--   T.tmpdir() -> new empty dir     T.write(path, text)   T.read(path)   T.root, T.sandbox
--   T.render(format_items) -> { { ch, fg, bg, bold }, ... } per drawn code point
local T = { tests = {}, root = '.', sandbox = nil, _env = {}, _now = nil, _baseline = nil }

local real_getenv, real_time = os.getenv, os.time

-- Environment variables that must never reach the user's live setup.
local function sandboxed(k)
  return k == 'HOME' or k:match('^XDG_') or k:match('^HIVEGRID') or k:match('^CLAUDE')
    or k:match('^WEZTERM') or k:match('^OMP')
end

function os.getenv(k)
  local v = T._env[k]
  if v ~= nil then return v or nil end
  if sandboxed(k) then return nil end
  return real_getenv(k)
end

function os.time(t)
  if t == nil and T._now then return math.floor(T._now) end
  return real_time(t)
end

function T.setenv(k, v)
  if v == nil then T._env[k] = false else T._env[k] = tostring(v) end
end

-- Current fake epoch (seconds, may be fractional); real time when no clock is set.
function T.clock(t) T._now = t end
function T.now() return T._now or real_time() end

local function sh_quote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end
T.sh_quote = sh_quote

local counter = 0
function T.tmpdir()
  counter = counter + 1
  local d = string.format('%s/d%04d', T.sandbox, counter)
  assert(os.execute('mkdir -p ' .. sh_quote(d)), 'mkdir failed: ' .. d)
  return d
end

function T.write(path, text)
  local dir = path:match('^(.*)/[^/]*$')
  if dir then os.execute('mkdir -p ' .. sh_quote(dir)) end
  local f = assert(io.open(path, 'w'))
  f:write(text)
  f:close()
  return path
end

function T.read(path)
  local f = io.open(path, 'r')
  if not f then return nil end
  local s = f:read('a')
  f:close()
  return s
end

function T.fresh()
  for k in pairs(package.loaded) do
    if not T._baseline[k] then package.loaded[k] = nil end
  end
  T._env, T._now = {}, nil
  local base = T.tmpdir()
  local home, run, cache, conf = base .. '/home', base .. '/run', base .. '/cache', base .. '/config'
  os.execute(string.format('mkdir -p %s %s %s %s', sh_quote(home), sh_quote(run), sh_quote(cache), sh_quote(conf)))
  T.setenv('HOME', home)
  T.setenv('XDG_RUNTIME_DIR', run)
  T.setenv('XDG_CACHE_HOME', cache)
  T.setenv('XDG_CONFIG_HOME', conf)
  return require 'wezterm'
end

-- Registration --------------------------------------------------------------

-- A known bug its owner will fix: counted as an expected failure while it fails, reported
-- as a failure once it passes (then turn it into T.test).
function T.xfail(name, reason, fn)
  T.tests[#T.tests + 1] = { file = T._file, name = name, fn = fn, xfail = reason }
end

function T.test(name, fn)
  T.tests[#T.tests + 1] = { file = T._file, name = name, fn = fn }
end

-- Failures are tables so the runner can tell an assertion from a crash.
local Failure = {}
Failure.__index = Failure
Failure.__tostring = function(f) return f.msg end

local function fail(msg, level)
  error(setmetatable({ msg = msg }, Failure), (level or 1) + 1)
end
T.fail = function(msg) fail(msg, 2) end
T.is_failure = function(e) return getmetatable(e) == Failure end

-- Pretty printer -------------------------------------------------------------

local function sorted_keys(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b)
    local ta, tb = type(a), type(b)
    if ta ~= tb then return ta < tb end
    if ta == 'number' or ta == 'string' then return a < b end
    return tostring(a) < tostring(b)
  end)
  return keys
end

local function show(v, depth, seen)
  depth, seen = depth or 0, seen or {}
  if type(v) == 'string' then return string.format('%q', v) end
  if type(v) ~= 'table' then return tostring(v) end
  if seen[v] then return '<cycle>' end
  if depth > 4 then return '{...}' end
  seen[v] = true
  local parts, n = {}, #v
  for i = 1, n do parts[#parts + 1] = show(v[i], depth + 1, seen) end
  for _, k in ipairs(sorted_keys(v)) do
    if not (math.type(k) == 'integer' and k >= 1 and k <= n) then
      local key = (type(k) == 'string' and k:match('^[%a_][%w_]*$')) and k or ('[' .. show(k, depth + 1, seen) .. ']')
      parts[#parts + 1] = key .. ' = ' .. show(v[k], depth + 1, seen)
    end
  end
  seen[v] = nil
  local s = '{ ' .. table.concat(parts, ', ') .. ' }'
  if #parts == 0 then s = '{}' end
  return s
end
T.show = show

-- First difference between a and b as (path, a_value, b_value), or nil when equal.
local function diff(a, b, path, seen)
  if a == b then return nil end
  if type(a) ~= 'table' or type(b) ~= 'table' then return path, a, b end
  seen = seen or {}
  if seen[a] == b then return nil end
  seen[a] = b
  for _, k in ipairs(sorted_keys(a)) do
    local p, x, y = diff(a[k], b[k], path .. '[' .. show(k) .. ']', seen)
    if p then return p, x, y end
  end
  for _, k in ipairs(sorted_keys(b)) do
    if a[k] == nil then return path .. '[' .. show(k) .. ']', nil, b[k] end
  end
  return nil
end

-- Assertions -----------------------------------------------------------------

function T.eq(actual, expected, msg)
  local p, x, y = diff(actual, expected, '')
  if p == nil then return end
  local head = msg and (msg .. ': ') or ''
  local where = p ~= '' and (' at ' .. p) or ''
  local text = string.format('%snot equal%s: got %s, expected %s', head, where, show(x), show(y))
  if p ~= '' then
    text = text .. string.format('\n    actual:   %s\n    expected: %s', show(actual), show(expected))
  end
  fail(text, 2)
end

function T.ok(v, msg)
  if not v then fail(msg or ('expected a true value, got ' .. show(v)), 2) end
  return v
end

function T.near(a, b, eps, msg)
  eps = eps or 1e-9
  if type(a) ~= 'number' or type(b) ~= 'number' or math.abs(a - b) > eps then
    fail(string.format('%s%s not within %g of %s', msg and (msg .. ': ') or '', show(a), eps, show(b)), 2)
  end
end

function T.raises(fn, pattern)
  local ok, err = pcall(fn)
  if ok then fail('expected an error, none raised', 2) end
  local s = tostring(err)
  if pattern and not s:find(pattern) then
    fail(string.format('error %q does not match %q', s, pattern), 2)
  end
  return s
end

-- Cells drawn by a wezterm.format item list: { { ch, fg, bg, bold }, ... } (one entry per
-- code point; 'ResetAttributes' clears colours and intensity; Click items are skipped).
function T.render(items)
  local cells, fg, bg, bold = {}, nil, nil, false
  for _, it in ipairs(items) do
    if it == 'ResetAttributes' then
      fg, bg, bold = nil, nil, false
    elseif type(it) == 'table' then
      if it.Foreground then fg = it.Foreground.Color or it.Foreground.AnsiColor end
      if it.Background then bg = it.Background.Color or it.Background.AnsiColor end
      if it.Attribute and it.Attribute.Intensity then bold = it.Attribute.Intensity == 'Bold' end
      if it.Text then
        for _, c in utf8.codes(it.Text) do
          cells[#cells + 1] = { ch = utf8.char(c), fg = fg, bg = bg, bold = bold }
        end
      end
    end
  end
  return cells
end

return T
