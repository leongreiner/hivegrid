-- Test runner for the WezTerm Lua config: lua5.4 tests/lua/run.lua [files...]
-- Runs every tests/lua/test_*.lua (or the given files), prints each failure with its
-- message and traceback and a summary line; exits 1 on any failure.
local script = arg and arg[0] or 'tests/lua/run.lua'
local rel = (script:match('^(.*)/[^/]*$') or '.') .. '/../..' -- the repo root, two levels up

local function capture(cmd)
  local p = assert(io.popen(cmd))
  local out = p:read('a')
  p:close()
  return out
end

local ROOT = capture('cd ' .. "'" .. rel:gsub("'", "'\\''") .. "'" .. ' && pwd'):gsub('%s+$', '')
package.path = table.concat({
  ROOT .. '/config/?.lua', ROOT .. '/?.lua', ROOT .. '/?/init.lua',
}, ';')
package.cpath = ''
package.preload['wezterm'] = function() return dofile(ROOT .. '/tests/lua/wezterm_stub.lua') end

local T = require 'tests.lua.t'
T.root = ROOT
T.sandbox = capture('mktemp -d "${TMPDIR:-/tmp}/hivegrid-luatest.XXXXXX"'):gsub('%s+$', '')
assert(T.sandbox ~= '', 'mktemp failed')
T._baseline = {}
for k in pairs(package.loaded) do T._baseline[k] = true end

local files = {}
if #arg > 0 then
  for i = 1, #arg do files[#files + 1] = arg[i] end
else
  for line in capture('ls ' .. ROOT .. '/tests/lua/test_*.lua 2>/dev/null'):gmatch('[^\n]+') do
    files[#files + 1] = line
  end
end

local passed, failed, xfailed = 0, 0, 0
local function report(where, err, tb)
  failed = failed + 1
  local msg = tostring(T.is_failure(err) and err.msg or err)
  print(string.format('FAIL %s: %s', where, msg))
  if tb then
    -- Drop the runner, the assertion helpers and C frames.
    for line in tb:gmatch('[^\n]+') do
      if not (line:find('tests/lua/t%.lua:') or line:find('tests/lua/run%.lua:')
          or line:find('%[C%]') or line:find('^stack traceback') or line:find('^%s*%(%.%.%.tail')) then
        print('    ' .. line:gsub('^%s+', ''))
      end
    end
  end
end

local clock = os.clock()
for _, file in ipairs(files) do
  local short = file:gsub('^' .. ROOT:gsub('%p', '%%%0') .. '/', '')
  T._file, T.tests = short, {}
  T.fresh()
  local ok, err = xpcall(dofile, debug.traceback, file)
  if not ok then
    report(short .. ' (load)', err)
  end
  for _, t in ipairs(T.tests) do
    local wz = T.fresh()
    local tb
    local good, e = xpcall(t.fn, function(e)
      tb = debug.traceback('', 2)
      return e
    end, wz)
    if t.xfail then
      if good then
        report(short .. ': ' .. t.name, 'passes now; it was expected to fail (' .. t.xfail .. '): make it a T.test')
      else
        xfailed = xfailed + 1
      end
    elseif good then
      passed = passed + 1
    else
      report(short .. ': ' .. t.name, e, tb)
    end
  end
end
T.fresh()
os.execute("rm -rf '" .. T.sandbox:gsub("'", "'\\''") .. "'")

local xf = xfailed > 0 and string.format(', %d expected failures', xfailed) or ''
print(string.format('%d passed, %d failed%s (%.2fs cpu)', passed, failed, xf, os.clock() - clock))
os.exit(failed == 0 and 0 or 1)
