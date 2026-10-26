-- Talks to the Hivegrid WezTerm config (sidebar.lua) through user vars (OSC 1337
-- SetUserVar), written to the controlling terminal rather than yazi's stdout so they reach
-- WezTerm even while yazi owns the alternate screen.
--   plugin hivegrid open    open a terminal in the hovered folder (a file's folder), like a
--                           double click (init.lua): the user var hivegrid_open_dir
--   plugin hivegrid close   close the explorer the way its bar button does (the user var
--                           sidebar_click), so the keyboard returns to the grid pane; a plain
--                           quit leaves it on the bar
-- Also the previewer of empty files (yazi.toml): "Empty file" centred like an empty
-- folder's "No items", instead of yazi's reversed slab.
local M = {}

local ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

local function base64(s)
  local out = {}
  for i = 1, #s, 3 do
    local a, b, c = s:byte(i, i + 2)
    local n = a * 65536 + (b or 0) * 256 + (c or 0)
    for k = 3, 0, -1 do
      local idx = math.floor(n / 64 ^ k) % 64
      out[#out + 1] = ALPHABET:sub(idx + 1, idx + 1)
    end
    if not b then out[#out - 1], out[#out] = '=', '=' elseif not c then out[#out] = '=' end
  end
  return table.concat(out)
end

function M.tty_write(s)
  local tty = io.open('/dev/tty', 'w')
  if not tty then return false end
  tty:write(s)
  tty:close()
  return true
end

function M.set_var(name, value)
  return M.tty_write('\27]1337;SetUserVar=' .. name .. '=' .. base64(value) .. '\7')
end

function M.open_dir(path) M.set_var('hivegrid_open_dir', path) end

local hovered = ya.sync(function()
  local h = cx.active.current.hovered
  return h and tostring(h.url.path)
end)

local serial = 0

function M:entry(job)
  local what = job.args[1]
  if what == 'open' then
    local path = hovered()
    if path then M.open_dir(path) end
  elseif what == 'close' then
    -- Outside WezTerm nothing would close the pane; quit instead.
    serial = serial + 1
    if not os.getenv('WEZTERM_PANE') or not M.set_var('sidebar_click', 'files:yazi' .. serial) then
      ya.emit('quit', {})
    end
  end
end

function M:peek(job)
  -- procfs and sysfs files report size 0 but have content; yazi's own previewer reads them.
  local url = job.file.url
  if url:starts_with('/sys/') or url:starts_with('/proc/') then return require('empty'):peek(job) end
  ya.preview_widget(job, ui.Text('Empty file'):area(job.area):align(ui.Align.CENTER))
end

function M:seek() end

return M
