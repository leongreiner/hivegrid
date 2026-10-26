-- System stats for the header's right status: CPU, RAM, disk of /, GPU.
-- Wire: local ss = require('sysstats')(config, wezterm)
--   ss.visible()        -> boolean (ALT+s toggles; default true)
--   ss.segments(opts)   -> wezterm.format items; opts.compact drops labels and disk
--   ss.text(opts)       -> plain text of the same content, for tests
-- Sampling happens inside segments()/text() behind per-source caches, so no
-- update-status handler is registered and a hidden segment costs nothing.
-- Units: all sizes are GiB (1024^3), printed with a "G" suffix.
return function(config, wezterm)
  local MUTED, GREEN, AMBER, RED = '#8b949e', '#23d18b', '#e5a50a', '#f14c4c'
  local CPU_MEM_TTL, GPU_TTL, DISK_TTL, GPU_RETRY = 2, 3, 30, 60
  local JETSON_LOAD = { '/sys/devices/platform/gpu.0/load', '/sys/devices/gpu.0/load' }
  local BARS = { '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█' }

  local cpu = { prev_total = nil, prev_idle = nil, t = 0, pct = nil }
  local mem = { t = 0, total = nil, avail = nil }
  local disk = { t = 0, pct = nil, free_kib = nil }
  local gpu = { t = 0, kind = nil, retry_at = 0, util = nil, used_mib = nil, total_mib = nil }
  local last_spawn = 0

  -- Pure parsers -----------------------------------------------------------

  -- Returns total, idle jiffies from the aggregate "cpu " line (idle includes iowait).
  local function parse_cpu(text)
    local line = text and text:match('^cpu%s+([^\n]+)')
    if not line then return nil end
    local f, total = {}, 0
    for n in line:gmatch('%d+') do
      f[#f + 1] = tonumber(n)
    end
    if #f < 5 then return nil end
    -- guest/guest_nice (fields 9, 10) are already counted in user/nice.
    for i = 1, math.min(#f, 8) do total = total + f[i] end
    return total, f[4] + f[5]
  end

  -- MemAvailable, not MemFree: the latter counts reclaimable page cache as used.
  local function parse_meminfo(text)
    if not text then return nil end
    local total = tonumber(text:match('MemTotal:%s+(%d+)'))
    local avail = tonumber(text:match('MemAvailable:%s+(%d+)'))
    if not total or not avail or total == 0 then return nil end
    return total, avail -- KiB
  end

  -- `df -P -k /`: returns used%, free KiB. used% = used/(used+avail) as df's own Capacity.
  local function parse_df(text)
    if not text then return nil end
    local last
    for line in text:gmatch('[^\n]+') do last = line end
    if not last then return nil end
    local used, avail = last:match('^%S+%s+%d+%s+(%d+)%s+(%d+)')
    used, avail = tonumber(used), tonumber(avail)
    if not used or not avail or used + avail == 0 then return nil end
    return 100 * used / (used + avail), avail
  end

  -- First GPU only: "util, mem_used_MiB, mem_total_MiB".
  local function parse_nvidia(text)
    if not text then return nil end
    local u, m, t = text:match('^%s*(%d+)%s*,%s*(%d+)%s*,%s*(%d+)')
    if not u then return nil end
    return tonumber(u), tonumber(m), tonumber(t)
  end

  -- Jetson gpu.0/load is 0-1000 permille.
  local function parse_jetson(text)
    local v = text and tonumber(text:match('%d+'))
    return v and math.min(100, v / 10) or nil
  end

  -- Sources -----------------------------------------------------------------

  local function slurp(path)
    local f = io.open(path, 'r')
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s
  end

  local function refresh_cpu(now)
    if now - cpu.t < CPU_MEM_TTL then return end
    local total, idle = parse_cpu(slurp('/proc/stat'))
    if not total then return end
    -- After a long gap (segment hidden) the delta would average idle history: rebaseline.
    if cpu.prev_total and now - cpu.t <= 10 and total > cpu.prev_total then
      cpu.pct = 100 * (1 - (idle - cpu.prev_idle) / (total - cpu.prev_total))
    end
    cpu.prev_total, cpu.prev_idle, cpu.t = total, idle, now
  end

  local function refresh_mem(now)
    if now - mem.t < CPU_MEM_TTL then return end
    local total, avail = parse_meminfo(slurp('/proc/meminfo'))
    if total then mem.total, mem.avail, mem.t = total, avail, now end
  end

  -- At most one child process per second across all windows, so a tick where
  -- disk and GPU are both stale spawns only one; the other waits for the next.
  local function spawn(args, now)
    if last_spawn == now then return nil end
    last_spawn = now
    local ok, success, stdout = pcall(wezterm.run_child_process, args)
    return ok, success and stdout or nil
  end

  local function refresh_gpu(now)
    if now - gpu.t < GPU_TTL or now < gpu.retry_at or gpu.kind == 'none' then return false end
    if gpu.kind == nil then
      for _, p in ipairs(JETSON_LOAD) do
        if io.open(p, 'r') then
          gpu.kind, gpu.path = 'jetson', p
          break
        end
      end
      gpu.kind = gpu.kind or 'nvidia'
    end
    if gpu.kind == 'jetson' then
      local util = parse_jetson(slurp(gpu.path))
      if util then gpu.util, gpu.t = util, now end
      return false
    end
    local ok, out = spawn({
      'nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total',
      '--format=csv,noheader,nounits',
    }, now)
    if ok == nil then return false end
    if not ok then -- binary missing: cached as the availability probe, never retried
      gpu.kind = 'none'
      return true
    end
    local u, m, t = parse_nvidia(out)
    if u then
      gpu.util, gpu.used_mib, gpu.total_mib, gpu.t = u, m, t, now
    else -- driver hiccup (e.g. reload): back off instead of spawning every tick
      gpu.util, gpu.retry_at = nil, now + GPU_RETRY
    end
    return true
  end

  local function refresh_disk(now)
    if now - disk.t < DISK_TTL then return end
    local ok, out = spawn({ 'df', '-P', '-k', '/' }, now)
    if ok == nil then return end
    local pct, free = parse_df(out)
    if pct then
      disk.pct, disk.free_kib, disk.t = pct, free, now
    else
      disk.t = now - DISK_TTL + GPU_RETRY -- retry in a minute-ish, not every tick
    end
  end

  local function refresh()
    local now = os.time()
    refresh_cpu(now)
    refresh_mem(now)
    local spawned = refresh_gpu(now)
    if not spawned then refresh_disk(now) end
  end

  -- Model and rendering -----------------------------------------------------

  local function color(pct)
    if pct > 85 then return RED end
    if pct >= 60 then return AMBER end
    return GREEN
  end

  local function bar(pct)
    local i = math.floor(math.max(0, math.min(99.9, pct)) / 100 * 8) + 1
    return BARS[i]
  end

  local function gib(kib) return kib / 1048576 end
  local function size(g) return g >= 10 and string.format('%.0f', g) or string.format('%.1f', g) end

  -- Each part: { label, text, pct, color-pct }; pct drives bar and color.
  local function parts(opts)
    local compact = opts and opts.compact
    local out = {}
    if cpu.pct then
      out[#out + 1] = { 'CPU', string.format('%.0f%%', cpu.pct), cpu.pct }
    end
    if mem.total then
      local used = gib(mem.total - mem.avail)
      out[#out + 1] = {
        'RAM', string.format('%s/%sG', string.format('%.1f', used), size(gib(mem.total))),
        100 * (mem.total - mem.avail) / mem.total,
      }
    end
    if disk.pct and not compact then
      out[#out + 1] = {
        'DSK', string.format('%.0f%% %sG free', disk.pct, size(gib(disk.free_kib))), disk.pct,
      }
    end
    if gpu.util and gpu.kind == 'nvidia' and gpu.total_mib then
      out[#out + 1] = {
        'GPU', string.format('%.0f%% %.1f/%sG', gpu.util, gpu.used_mib / 1024, size(gpu.total_mib / 1024)),
        gpu.util,
      }
    elseif gpu.util and gpu.kind == 'jetson' then
      out[#out + 1] = { 'GPU', string.format('%.0f%% shared', gpu.util), gpu.util }
    end
    return out
  end

  local api = {}

  function api.visible()
    return wezterm.GLOBAL.sysstats_visible ~= false
  end

  function api.segments(opts)
    refresh()
    local compact = opts and opts.compact
    local items = {}
    for i, p in ipairs(parts(opts)) do
      if i > 1 then items[#items + 1] = { Text = '  ' } end
      if not compact then
        items[#items + 1] = { Foreground = { Color = MUTED } }
        items[#items + 1] = { Text = p[1] .. ' ' }
      end
      items[#items + 1] = { Foreground = { Color = color(p[3]) } }
      items[#items + 1] = { Text = p[2] .. ' ' .. bar(p[3]) }
    end
    if #items > 0 then
      items[#items + 1] = { Text = '  ' }
    end
    return items
  end

  function api.text(opts)
    refresh()
    local compact = opts and opts.compact
    local strs = {}
    for _, p in ipairs(parts(opts)) do
      strs[#strs + 1] = (compact and '' or (p[1] .. ' ')) .. p[2] .. ' ' .. bar(p[3])
    end
    return table.concat(strs, '  ')
  end

  -- Exposed for tests of the pure parsers.
  api.parse = {
    cpu = parse_cpu, meminfo = parse_meminfo, df = parse_df,
    nvidia = parse_nvidia, jetson = parse_jetson,
  }

  -- Plain ALT+s, deliberately not behind the leader.
  if wezterm.GLOBAL.sysstats_visible == nil then wezterm.GLOBAL.sysstats_visible = true end
  config.keys = config.keys or {}
  table.insert(config.keys, {
    key = 's',
    mods = 'ALT',
    action = wezterm.action_callback(function()
      wezterm.GLOBAL.sysstats_visible = not api.visible()
    end),
  })

  return api
end
