-- luacheck config for config/*.lua (WezTerm), config/yazi (yazi plugin API) and tests/lua.
-- WezTerm passes callbacks positional arguments the config often does not use, and a few
-- table-heavy lines are long, so those two warnings are off; everything else is on.
std = 'lua54'
max_line_length = false
unused_args = false

files['config/yazi'] = {
  -- yazi's Lua API: globals the config reads, and the runtime options and components it overrides.
  read_globals = { 'ya', 'cx', 'ui', 'th', 'fs', 'ps', 'Url', 'Command', 'Cha', 'File', 'Error' },
  globals = {
    'rt', 'Root', 'Tab', 'Tabs', 'Header', 'Status', 'Rail', 'Current', 'Parent', 'Preview',
    'Entity', 'Linemode', 'Marker', 'Modal', 'Progress',
  },
}

files['tests/lua'] = {
  ignore = { '122' }, -- tests patch os.getenv / os.time on purpose (and restore them)
}
