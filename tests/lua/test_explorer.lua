-- config/explorer.lua: the generated yazi theme and the YAZI_CONFIG_HOME it assembles.
local T = require 'tests.lua.t'

-- Lets mkdir/ln run for real (paths are all inside the sandbox).
local function real_children(wz)
  wz.stub.on_child = function(argv)
    if argv[1] ~= 'mkdir' and argv[1] ~= 'ln' then return false end
    local q = {}
    for i, a in ipairs(argv) do q[i] = T.sh_quote(a) end
    return os.execute(table.concat(q, ' ') .. ' 2>/dev/null') == true
  end
end

local function exists(path)
  return os.execute('test -e ' .. T.sh_quote(path) .. ' || test -L ' .. T.sh_quote(path)) == true
end

local function link_target(path)
  local p = io.popen('readlink ' .. T.sh_quote(path))
  local s = p:read('a'):gsub('%s+$', '')
  p:close()
  return s
end

T.test('theme.toml: unique sections and keys, every style value well formed', function()
  local th = require 'theme'
  local toml = require('explorer').theme_toml(th.P, th.T)
  local colours = {}
  for _, v in pairs(th.P) do colours[v] = true end
  for _, v in pairs(th.T) do if type(v) == 'string' then colours[v] = true end end

  local sections, keys, section, in_array = {}, {}, nil, false
  local n = 0
  for line in toml:gmatch('([^\n]*)\n') do
    n = n + 1
    local where = 'line ' .. n .. ': ' .. line
    if in_array then
      if line == ']' then
        in_array = false
      else
        T.ok(line:match('^  { .* },$'), where)
        for c in line:gmatch('fg = "([^"]*)"') do T.ok(colours[c], 'unknown colour ' .. where) end
      end
    elseif line == '' or line:match('^#') then
    elseif line:match('^%[[%w_]+%]$') then
      section = line
      T.ok(not sections[section], 'duplicate section ' .. where)
      sections[section], keys = true, {}
    else
      local k, v = line:match('^([%w_]+) = (.+)$')
      T.ok(k, 'not a key = value line: ' .. where)
      T.ok(section, 'key before any section: ' .. where)
      T.ok(not keys[k], 'duplicate key ' .. where)
      keys[k] = true
      if v == '[' then
        in_array = true
      elseif v:sub(1, 1) == '{' then
        T.ok(v:match('^{ .* }$') or v == '{}', 'inline table ' .. where)
        local body = v:match('^{ (.*) }$') or ''
        for field in (body ~= '' and body .. ', ' or ''):gmatch('(.-), ') do
          local fk, fv = field:match('^([%w_]+) = (.+)$')
          T.ok(fk, 'field ' .. where)
          if fk == 'fg' or fk == 'bg' then
            T.ok(colours[fv:match('^"(.*)"$') or ''], 'colour value ' .. where)
          else
            T.ok(fv == 'true' or fv:match('^"[^"]*"$'), 'value ' .. where)
          end
        end
      else
        T.ok(v:match('^"[^"]*"$') or v:match('^%-?%d+$') or v == 'true' or v == 'false',
          'scalar value ' .. where)
      end
    end
  end
  T.ok(not in_array, 'unterminated array')
  T.ok(sections['[mgr]'] and sections['[filetype]'], 'main sections present')
end)

T.test('theme.toml follows the palette chosen in settings', function()
  T.write(os.getenv('HOME') .. '/.config/hivegrid/settings.json',
    '{"theme": {"palette": {"accent": "#a1b2c3"}, "terminal": {"split": "#0f0e0d"}}}')
  local th = require 'theme'
  local toml = require('explorer').theme_toml(th.P, th.T)
  T.ok(toml:find('cwd = { fg = "#a1b2c3", bold = true }', 1, true), 'accent used for the cwd')
  T.ok(toml:find('border_style = { fg = "#0f0e0d" }', 1, true), 'split colour used for borders')
end)

T.test('palette.lua loads back to the palette', function()
  local th = require 'theme'
  local P = { accent = '#4f9dff', text = 'a "quoted" \\ value\n' }
  local chunk = assert(load(require('explorer').palette_lua(P)))
  T.eq(chunk(), P)
  T.eq(assert(load(require('explorer').palette_lua(th.P)))(), th.P)
end)

T.test('config_home: cache not writable -> the static config', function(wz)
  -- run_child_process (mkdir) fails by default in the stub.
  local home = os.getenv('HOME')
  T.eq(require('explorer').config_home(), home .. '/.config/hivegrid/yazi')
  T.ok(not exists(os.getenv('XDG_CACHE_HOME') .. '/hivegrid/yazi/theme.toml'), 'nothing written')
end)

T.test('config_home: generated files plus links to every static entry, stale links dropped', function(wz)
  real_children(wz)
  local home, cache = os.getenv('HOME'), os.getenv('XDG_CACHE_HOME') .. '/hivegrid/yazi'
  local src = home .. '/.config/hivegrid/yazi'
  T.write(src .. '/yazi.toml', 'a')
  T.write(src .. '/keymap.toml', 'b')
  T.write(src .. '/plugins/x.yazi/main.lua', 'c')
  T.write(src .. '/theme.toml', 'static theme must not shadow the generated one')
  T.write(cache .. '/gone.toml', 'stale')
  T.eq(require('explorer').config_home(), cache)

  local th = require 'theme'
  T.eq(T.read(cache .. '/theme.toml'), require('explorer').theme_toml(th.P, th.T))
  T.eq(assert(load(T.read(cache .. '/palette.lua')))(), th.P)
  T.eq(link_target(cache .. '/yazi.toml'), src .. '/yazi.toml')
  T.eq(link_target(cache .. '/plugins'), src .. '/plugins')
  T.ok(not exists(cache .. '/gone.toml'), 'entry without a source removed')

  -- A second run with an unchanged source keeps everything, and a removed source entry
  -- loses its link.
  os.remove(src .. '/keymap.toml')
  T.eq(require('explorer').config_home(), cache)
  T.ok(not exists(cache .. '/keymap.toml'), 'dangling link removed')
  T.eq(link_target(cache .. '/yazi.toml'), src .. '/yazi.toml')
  T.ok(exists(cache .. '/theme.toml'))
end)
