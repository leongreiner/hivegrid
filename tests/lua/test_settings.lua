-- config/settings.lua: settings.json parsing (theme colours, key bindings, accounts).
-- Every test gets a fresh module (the api is built once per load) and a temp HOME.
local T = require 'tests.lua.t'

-- Writes settings.json (string or table) into a temp dir, points HIVEGRID_SETTINGS at it
-- and returns the built api.
local function load(wz, content)
  local path = T.tmpdir() .. '/settings.json'
  if content ~= nil then
    T.write(path, type(content) == 'table' and wz.json_encode(content) or content)
  end
  T.setenv('HIVEGRID_SETTINGS', path)
  return require('settings')(nil, wz), path
end

local function home() return os.getenv('HOME') end

local function creds(dir) T.write(dir .. '/.credentials.json', '{}') end

local function labels(list)
  local out = {}
  for i, a in ipairs(list) do out[i] = a.label end
  return out
end

-- Theme ---------------------------------------------------------------------------

T.test('malformed JSON falls back to defaults everywhere', function()
  for _, text in ipairs { '{"theme": {"palette": {"accent": "#ffffff"}', 'not json', '"a string"', '42', '' } do
    T.fresh()
    local wz = require 'wezterm'
    local S = load(wz, text)
    T.eq(S.theme_palette(), {}, text)
    T.eq(S.theme_terminal(), {}, text)
    T.eq(S.key('equalize', 'CTRL+SHIFT+e'), { key = 'e', mods = 'CTRL|SHIFT' }, text)
    T.eq(S.accounts(), {}, text)
  end
end)

T.test('palette keeps only #rrggbb strings, lowercased', function(wz)
  local S = load(wz, {
    theme = { palette = {
      accent = '#ABCDEF', text = 'red', muted = '#abc', dim = '#1234567', bright = 5,
      green = ' #00ff00', amber = '#00ff0g', cyan = '#0a0B0c',
    } },
  })
  T.eq(S.theme_palette(), { accent = '#abcdef', cyan = '#0a0b0c' })
end)

T.test('ansi/brights lists need eight valid colours', function(wz)
  local eight = { '#000000', '#111111', '#222222', '#333333', '#444444', '#555555', '#666666', '#7777AA' }
  local seven = { '#000000', '#111111', '#222222', '#333333', '#444444', '#555555', '#666666' }
  local bad_last = { '#000000', '#111111', '#222222', '#333333', '#444444', '#555555', '#666666', 'blue' }
  local S = load(wz, {
    theme = { terminal = { ansi = seven, brights = eight, split = '#ABCDEF', selection_bg = bad_last } },
  })
  local term = S.theme_terminal()
  T.eq(term.ansi, nil, 'a 7-colour ansi list is rejected')
  T.eq(term.brights[8], '#7777aa')
  T.eq(#term.brights, 8)
  T.eq(term.split, '#abcdef')
  T.eq(term.selection_bg, nil, 'a list is only valid for ansi/brights')
  T.fresh()
  wz = require 'wezterm'
  S = load(wz, { theme = { terminal = { ansi = bad_last, brights = 'nope' } } })
  T.eq(S.theme_terminal(), {}, 'invalid last entry / non-list brights')
end)

T.test('theme palette is applied to theme.P key by key, unknown names ignored', function(wz)
  load(wz, { theme = { palette = { accent = '#010203', no_such_token = '#ffffff' },
    terminal = { split = '#0A0B0C' } } })
  local theme = require 'theme'
  T.eq(theme.P.accent, '#010203')
  T.eq(theme.P.no_such_token, nil)
  T.eq(theme.T.split, '#0a0b0c')
  T.eq(theme.P.base, '#0b1220', 'untouched keys keep their default')
end)

T.test('an ansi list with one bad colour in the middle is dropped, not holed', function(wz)
  local S = load(wz, '{"theme": {"terminal": {'
    .. '"ansi": ["#000000", "#111111", "#222222", "bad", "#444444", "#555555", "#666666", "#777777"],'
    .. '"brights": ["#000000", null, "#222222", "#333333", "#444444", "#555555", "#666666", "#777777"]}}}')
  T.eq(S.theme_terminal(), {})
end)

-- Keys ----------------------------------------------------------------------------

T.test('key combos: modifiers joined with |, the last + separates the key', function(wz)
  local S = load(wz, { keys = {
    pipe = 'CTRL+SHIFT+|', plus = 'CTRL++', lone_plus = '+', plain = 'f', three = 'SUPER+ALT+Enter',
    empty = '', number = 7,
  } })
  T.eq(S.key('pipe'), { key = '|', mods = 'CTRL|SHIFT' })
  T.eq(S.key('plus'), { key = '+', mods = 'CTRL' })
  T.eq(S.key('lone_plus'), { key = '+' })
  T.eq(S.key('plain'), { key = 'f' })
  T.eq(S.key('three'), { key = 'Enter', mods = 'SUPER|ALT' })
  T.eq(S.key('empty', 'CTRL+a'), { key = 'a', mods = 'CTRL' }, 'empty string -> default')
  T.eq(S.key('number', 'CTRL+a'), { key = 'a', mods = 'CTRL' }, 'non-string -> default')
  T.eq(S.key('missing', 'CTRL+SHIFT+a'), { key = 'a', mods = 'CTRL|SHIFT' })
  T.eq(S.key('missing'), nil)
end)

T.test('keys that is not an object is ignored', function(wz)
  local S = load(wz, { keys = { 'CTRL+x' } })
  T.eq(S.key('1', 'CTRL+a'), { key = 'a', mods = 'CTRL' })
end)

-- Accounts ------------------------------------------------------------------------

T.test('no settings file: only the discovered accounts, tilde expanded, no profile', function(wz)
  creds(home() .. '/.claude')
  local S = load(wz, nil)
  T.eq(S.accounts(), { { label = 'main', dir = home() .. '/.claude', creds = true } })
  T.eq(S.signature(), 'main=')
end)

T.test('discovered ~/.claude-X with credentials are appended sorted by label', function(wz)
  creds(home() .. '/.claude-zeta')
  creds(home() .. '/.claude-alpha')
  os.execute('mkdir -p ' .. T.sh_quote(home() .. '/.claude-nocreds'))
  creds(home() .. '/.clauderc-x') -- not a .claude-<label> directory
  local S = load(wz, { accounts = { { label = 'work', dir = '/abs/work' } } })
  T.eq(labels(S.accounts()), { 'work', 'alpha', 'zeta' })
  local alpha = S.accounts()[2]
  T.eq(alpha.dir, home() .. '/.claude-alpha')
  T.eq(alpha.creds, true)
end)

T.test('hidden accounts are skipped and their directory is not rediscovered', function(wz)
  creds(home() .. '/.claude-2')
  creds(home() .. '/.claude-extra')
  local S = load(wz, { accounts = {
    { label = 'main', dir = '~/.claude', profile = 'alpha' },
    { label = '2', dir = '~/.claude-2', profile = 'beta', hidden = true },
  } })
  T.eq(labels(S.accounts()), { 'main', 'extra' })
end)

T.test('malformed account entries are skipped, empty profile means none', function(wz)
  local S = load(wz, { accounts = {
    { label = 'ok', dir = '~/x', profile = '' },
    { label = 5, dir = '~/y' },
    { label = 'nodir' },
    'junk',
  } })
  T.eq(S.accounts(), { { label = 'ok', dir = home() .. '/x', creds = false } })
end)

T.test('discovered accounts pair with the profile of the same name; main first, then by label', function(wz)
  for _, d in ipairs { '.claude', '.claude-2', '.claude-work', '.claude-solo' } do creds(home() .. '/' .. d) end
  os.execute('mkdir -p ' .. T.sh_quote(home() .. '/.omp/profiles/work'))
  os.execute('mkdir -p ' .. T.sh_quote(home() .. '/.omp/profiles/other')) -- no account of that name
  local S = load(wz, { accounts = {} }) -- an empty list: everything is discovered
  local got = {}
  for _, a in ipairs(S.accounts()) do got[a.label] = a.profile or false end
  T.eq(got, { main = false, ['2'] = false, work = 'work', solo = false })
  T.eq(labels(S.accounts()), { 'main', '2', 'solo', 'work' })
end)

T.test('a profile directory named after the label pairs; an explicit profile wins', function(wz)
  for _, d in ipairs { '.claude', '.claude-2' } do creds(home() .. '/' .. d) end
  os.execute('mkdir -p ' .. T.sh_quote(home() .. '/.omp/profiles/main'))
  os.execute('mkdir -p ' .. T.sh_quote(home() .. '/.omp/profiles/2'))
  local S = load(wz, { accounts = { { label = '2', dir = '~/.claude-2', profile = 'beta' } } })
  T.eq(S.signature(), '2=beta,main=main')
end)

T.test('account scan is cached for 10 s, then picks up new directories', function(wz)
  T.clock(1000)
  local S = load(wz, { accounts = { { label = 'main', dir = '~/.claude' } } })
  local before = S.signature()
  T.eq(before, 'main=')
  creds(home() .. '/.claude-2')
  T.clock(1009)
  T.eq(labels(S.accounts()), { 'main' }, 'still cached 9 s later')
  T.clock(1010)
  T.eq(labels(S.accounts()), { 'main', '2' }, 'rescanned after 10 s')
  T.eq(S.signature(), 'main=,2=')
end)

-- File watching -------------------------------------------------------------------

T.test('a missing settings directory is created and watched for reloads', function(wz)
  local dir = T.tmpdir() .. '/not/yet'
  T.setenv('HIVEGRID_SETTINGS', dir .. '/settings.json')
  require('settings')(nil, wz)
  T.eq(wz.stub.children[1], { 'mkdir', '-p', dir })
  local watched = {}
  for _, p in ipairs(wz.stub.watch) do watched[p] = true end
  T.ok(watched[dir], 'directory watched')
end)

T.test('without HIVEGRID_SETTINGS the file lives under ~/.config/hivegrid', function(wz)
  T.write(home() .. '/.config/hivegrid/settings.json', '{"keys": {"arrange": "ALT+a"}}')
  local S = require('settings')(nil, wz)
  T.eq(S.path(), home() .. '/.config/hivegrid/settings.json')
  T.eq(S.key('arrange', 'CTRL+SHIFT+a'), { key = 'a', mods = 'ALT' })
end)
