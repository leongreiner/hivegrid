-- Hivegrid settings: reads ~/.config/hivegrid/settings.json, written by `hivegrid-panel settings`.
-- Wire: local S = require('settings')(config, wezterm)   (config may be nil; the api is built once)
--   S.theme_palette()   -> overrides for theme.P ({ name = '#rrggbb' }), empty without a theme
--   S.theme_terminal()  -> overrides for theme.T (colours, ansi, brights)
--   S.key(name, default) -> { key = 'f', mods = 'CTRL' } from "CTRL+f" style strings (mods nil
--                           when the binding has none); `default` is used when the file has no entry
--   S.accounts()        -> visible omp/Claude accounts { label, profile, dir, creds }: the file's
--                          list (today's three when it has none) plus any ~/.claude[-X] directory
--                          holding a .credentials.json that the list does not mention
--   S.signature()       -> string that changes when the account list changes
-- The file's directory sits on the config reload watch list, so a save from the panel
-- reloads the config. Credentials are only tested for existence, never read.
local api

local DEFAULT_ACCOUNTS = {
  { label = 'main', profile = 'work', dir = '~/.claude' },
  { label = '2', profile = 'team', dir = '~/.claude-2' },
  { label = 'private', profile = 'private', dir = '~/.claude-private' },
}
-- Profiles paired with a label by convention; any other label pairs with a profile of
-- the same name when ~/.omp/profiles/<label> exists.
local KNOWN_PROFILES = { main = 'work', ['2'] = 'team', private = 'private' }
local SCAN_TTL = 10

local function build(wezterm)
  local HOME = os.getenv('HOME') or ''
  local PATH = os.getenv('HIVEGRID_SETTINGS') or (HOME .. '/.config/hivegrid/settings.json')
  local DIR = PATH:match('^(.*)/[^/]*$') or '.'

  local function is_dir(p)
    local ok = pcall(wezterm.read_dir, p)
    return ok
  end
  if not is_dir(DIR) then pcall(wezterm.run_child_process, { 'mkdir', '-p', DIR }) end
  pcall(wezterm.add_to_config_reload_watch_list, DIR)

  local function slurp(path)
    local f = io.open(path, 'r')
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s
  end

  local data = {}
  do
    local text = slurp(PATH)
    if text then
      local ok, parsed = pcall(wezterm.json_parse, text)
      if ok and type(parsed) == 'table' then data = parsed end
    else
      -- Also watched as a file: the directory watch misses nothing, this just keeps the path in logs.
      pcall(wezterm.add_to_config_reload_watch_list, PATH)
    end
  end

  local function is_hex(v) return type(v) == 'string' and v:match('^#%x%x%x%x%x%x$') ~= nil end

  local function colour_map(src)
    local out = {}
    if type(src) ~= 'table' then return out end
    for k, v in pairs(src) do
      if type(v) == 'string' and is_hex(v) then
        out[k] = v:lower()
      elseif type(v) == 'table' and (k == 'ansi' or k == 'brights') then
        local list = {}
        for i = 1, 8 do list[i] = is_hex(v[i]) and v[i]:lower() or nil end
        if #list == 8 then out[k] = list end
      end
    end
    return out
  end

  local theme = type(data.theme) == 'table' and data.theme or {}
  local S = {}
  function S.theme_palette() return colour_map(theme.palette) end
  function S.theme_terminal() return colour_map(theme.terminal) end
  function S.path() return PATH end

  -- "CTRL+SHIFT+|" -> key '|', mods 'CTRL|SHIFT'.
  local function parse_combo(s)
    if type(s) ~= 'string' or s == '' then return nil end
    local mods, key = s:match('^(.*)%+(.+)$')
    if not mods then return { key = s } end
    return { key = key, mods = (mods:gsub('%+', '|')) }
  end

  function S.key(name, default)
    local keys = type(data.keys) == 'table' and data.keys or {}
    return parse_combo(keys[name]) or parse_combo(default)
  end

  -- Accounts ------------------------------------------------------------------

  local function expand(p)
    if p:sub(1, 2) == '~/' then return HOME .. p:sub(2) end
    return p
  end

  local function has_creds(dir)
    local f = io.open(expand(dir) .. '/.credentials.json', 'r')
    if not f then return false end
    f:close()
    return true
  end

  local function profile_names()
    local names = {}
    local ok, entries = pcall(wezterm.read_dir, HOME .. '/.omp/profiles')
    for _, p in ipairs(ok and entries or {}) do names[p:match('([^/]+)$')] = true end
    return names
  end

  local function pair_profile(label, profiles)
    return KNOWN_PROFILES[label] or (profiles[label] and label) or nil
  end

  local scan = { t = -math.huge, list = nil }
  local function visible_accounts()
    local now = os.time()
    if scan.list and now - scan.t < SCAN_TTL then return scan.list end
    local profiles = profile_names()
    local base = type(data.accounts) == 'table' and data.accounts or nil
    local list, seen = {}, {}
    for _, a in ipairs(base or DEFAULT_ACCOUNTS) do
      if type(a) == 'table' and type(a.label) == 'string' and type(a.dir) == 'string' then
        seen[expand(a.dir)] = true
        if not a.hidden then
          list[#list + 1] = {
            label = a.label,
            profile = type(a.profile) == 'string' and a.profile ~= '' and a.profile or nil,
            dir = expand(a.dir),
            creds = has_creds(a.dir),
          }
        end
      end
    end
    local ok, entries = pcall(wezterm.read_dir, HOME)
    local extra = {}
    for _, p in ipairs(ok and entries or {}) do
      local name = p:match('([^/]+)$') or ''
      local label = name == '.claude' and 'main' or name:match('^%.claude%-(.+)$')
      if label and not seen[p] and has_creds(p) then
        extra[#extra + 1] = { label = label, profile = pair_profile(label, profiles), dir = p, creds = true }
      end
    end
    table.sort(extra, function(a, b) return a.label < b.label end)
    for _, e in ipairs(extra) do list[#list + 1] = e end
    scan.t, scan.list = now, list
    return list
  end
  function S.accounts() return visible_accounts() end

  function S.signature()
    local parts = {}
    for _, a in ipairs(visible_accounts()) do parts[#parts + 1] = a.label .. '=' .. (a.profile or '') end
    return table.concat(parts, ',')
  end

  return S
end

return function(_config, wezterm)
  if not api then api = build(wezterm) end
  return api
end
