-- Double click on an entry asks WezTerm (the user var hivegrid_open_dir, handled in
-- sidebar.lua) to open a terminal in that folder, or in the file's folder.
-- yazi's own click handler only hovers the entry (right click also opens it), so a double
-- click does nothing by itself in yazi.
local DOUBLE_CLICK_SECS = 0.4
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

-- Written to the controlling terminal rather than yazi's stdout so it reaches WezTerm
-- even while yazi owns the alternate screen.
local function announce(path)
  local tty = io.open('/dev/tty', 'w')
  if not tty then return end
  tty:write('\27]1337;SetUserVar=hivegrid_open_dir=', base64(path), '\7')
  tty:close()
end

-- Matched by screen cell, not by entry: a click in the parent column navigates, so the
-- same cell shows a different entry on the second click.
local last = {}

local click = Entity.click
function Entity:click(event, up)
  click(self, event, up)
  if up or not event.is_left then return end

  local now = ya.time()
  if last.x == event.x and last.y == event.y and now - last.t <= DOUBLE_CLICK_SECS then
    local path = last.path
    last = {}
    announce(path)
  else
    last = { x = event.x, y = event.y, t = now, path = tostring(self._file.url.path) }
  end
end
