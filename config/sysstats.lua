-- System stats for the header's right status: CPU, RAM, GPU and, when it runs full, disk of /.
-- A calm summary chip: muted labels and numbers, a colour only on a metric past its warn
-- (amber) or critical (red) threshold; the monitor panel (click) holds the detail.
-- Wire: local ss = require('sysstats')(config, wezterm)
--   ss.visible()        -> boolean (ALT+s toggles; default true)
--   ss.segments(opts)   -> wezterm.format items, width in cells; opts.budget picks the richest
--                          tier that fits; {} , 0 if none does
--   ss.tiers()          -> every tier richest first as { items, w }
--   ss.level(pct)       -> 'ok' | 'warn' | 'crit' (the thresholds the colours follow)
--   ss.text(opts)       -> plain text of the same content, for tests
--   ss.record()         -> sample and log without rendering (the header calls it every tick)
-- Sampling happens inside segments()/text() behind per-source caches, and the header's
-- update-status handler calls record() every tick, so the stats keep being sampled and
-- logged (see "History log" below) while the segment is hidden or the monitor panel closed.
-- Units: all sizes are GiB (1024^3), printed with a "G" suffix.
return function(config, wezterm)
  local theme = require 'theme'
  local P = theme.P
  local CPU_MEM_TTL, GPU_TTL, DISK_TTL, GPU_RETRY = 2, 3, 30, 60
  local LOG_EVERY, LOG_MAX = 15, 2 * 1024 * 1024
  -- HIVEGRID_PROC_DIR / HIVEGRID_SYS_DIR point the readers at fixture trees in tests.
  local PROC = os.getenv('HIVEGRID_PROC_DIR') or '/proc'
  local SYS = os.getenv('HIVEGRID_SYS_DIR') or '/sys'
  local JETSON_LOAD = { SYS .. '/devices/platform/gpu.0/load', SYS .. '/devices/gpu.0/load' }
  local LOG_PATH = os.getenv('HIVEGRID_SYSSTATS_LOG')
    or ((os.getenv('XDG_CACHE_HOME') or (wezterm.home_dir .. '/.cache')) .. '/hivegrid/sysstats.jsonl')

  local cpu = { prev_total = nil, prev_idle = nil, t = 0, pct = nil }
  local cpu_sum, cpu_n = 0, 0 -- CPU readings since the last log line, so a line is their mean
  local mem = { t = 0, total = nil, avail = nil }
  local disk = { t = 0, pct = nil, free_kib = nil }
  local gpu = { t = 0, kind = nil, retry_at = 0, util = nil, used_mib = nil, total_mib = nil }
  local last_spawn = 0

  -- A config reload (a settings save, an edited .lua) rebuilds this module; the last
  -- samples survive it in wezterm.GLOBAL so the header does not lose CPU (which needs two
  -- readings) and DSK (spawned at most once a second) for the next few ticks.
  local SAVED = { cpu = cpu, mem = mem, disk = disk, gpu = gpu }
  do
    local ok, saved = pcall(wezterm.json_parse, wezterm.GLOBAL.sysstats_state or '')
    if ok and type(saved) == 'table' then
      for name, t in pairs(SAVED) do
        for k, v in pairs(type(saved[name]) == 'table' and saved[name] or {}) do t[k] = v end
      end
    end
  end
  local saved_stamp
  local function save()
    local stamp = cpu.t .. ':' .. mem.t .. ':' .. disk.t .. ':' .. gpu.t .. ':' .. tostring(gpu.kind)
    if stamp == saved_stamp then return end
    saved_stamp = stamp
    wezterm.GLOBAL.sysstats_state = wezterm.json_encode(SAVED)
  end

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
    local total, idle = parse_cpu(slurp(PROC .. '/stat'))
    if not total then return end
    -- After a long gap (segment hidden) the delta would average idle history: rebaseline.
    if cpu.prev_total and now - cpu.t <= 10 and total > cpu.prev_total then
      cpu.pct = 100 * (1 - (idle - cpu.prev_idle) / (total - cpu.prev_total))
      cpu_sum, cpu_n = cpu_sum + cpu.pct, cpu_n + 1
    end
    cpu.prev_total, cpu.prev_idle, cpu.t = total, idle, now
  end

  local function refresh_mem(now)
    if now - mem.t < CPU_MEM_TTL then return end
    local total, avail = parse_meminfo(slurp(PROC .. '/meminfo'))
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

  -- Hidden header segment: nobody watches the 3 s GPU reading, the log needs one per line.
  local function gpu_ttl()
    return wezterm.GLOBAL.sysstats_visible == false and LOG_EVERY or GPU_TTL
  end

  local function refresh_gpu(now)
    if now - gpu.t < gpu_ttl() or now < gpu.retry_at or gpu.kind == 'none' then return false end
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

  -- History log -------------------------------------------------------------------

  -- One JSON line every LOG_EVERY seconds for the monitor panel's long ranges, written
  -- whether the panel is open or not: {"ts":..,"cpu":..,"ram":..,"dsk":..,"gpu":..}, a
  -- key missing where the source is. `ts` in GLOBAL spaces the lines across windows and
  -- config reloads. The file rotates to `.1` at LOG_MAX bytes, so about 4 to 8 days remain.
  local mkdir_at = -math.huge

  local function append(line, now)
    local f = io.open(LOG_PATH, 'a')
    if not f and now - mkdir_at >= 60 then
      mkdir_at = now
      pcall(wezterm.run_child_process, { 'mkdir', '-p', (LOG_PATH:gsub('/[^/]*$', '')) })
      f = io.open(LOG_PATH, 'a')
    end
    if not f then return end
    f:write(line)
    local size = f:seek('end')
    f:close()
    if size and size >= LOG_MAX then os.rename(LOG_PATH, LOG_PATH .. '.1') end
  end

  local function log_sample(now)
    if now - (wezterm.GLOBAL.sysstats_logged or 0) < LOG_EVERY then return end
    local parts = {}
    local function add(key, v)
      if v then parts[#parts + 1] = string.format('"%s":%.1f', key, v) end
    end
    add('cpu', cpu_n > 0 and cpu_sum / cpu_n or nil)
    add('ram', mem.total and 100 * (mem.total - mem.avail) / mem.total or nil)
    add('dsk', disk.pct)
    add('gpu', gpu.kind ~= 'none' and gpu.util or nil)
    if #parts == 0 then return end
    wezterm.GLOBAL.sysstats_logged = now
    cpu_sum, cpu_n = 0, 0
    append(string.format('{"ts":%d,%s}\n', now, table.concat(parts, ',')), now)
  end

  local function refresh()
    local now = os.time()
    refresh_cpu(now)
    refresh_mem(now)
    local spawned = refresh_gpu(now)
    if not spawned then refresh_disk(now) end
    save()
    log_sample(now)
  end

  -- Model and rendering -----------------------------------------------------

  -- Thresholds, mirrored by load_color() in bin/hivegrid_panels.py (the monitor panel's value
  -- colour): a load above CRIT is critical, from WARN up a warning.
  local WARN, CRIT = 60, 85
  local function level(pct)
    if pct > CRIT then return 'crit' end
    return pct >= WARN and 'warn' or 'ok'
  end
  -- The header colours only what needs a look: ok is the plain muted text.
  local TONE = { ok = P.muted, warn = P.amber, crit = P.red }

  local function gib(kib) return kib / 1048576 end
  local function size(g) return g >= 10 and string.format('%.0f', g) or string.format('%.1f', g) end

  -- One entry per available source, in header order: { label, pct, amount, amount width }.
  local function metrics()
    local out = {}
    if cpu.pct then out[#out + 1] = { 'CPU', cpu.pct } end
    if mem.total then
      local used = gib(mem.total - mem.avail)
      out[#out + 1] = {
        'RAM', 100 * (mem.total - mem.avail) / mem.total,
        string.format('%.1fG/%sG', used, size(gib(mem.total))), 9,
      }
    end
    if gpu.util and gpu.kind == 'nvidia' and gpu.total_mib then
      out[#out + 1] = {
        'GPU', gpu.util, string.format('%.1f/%sG', gpu.used_mib / 1024, size(gpu.total_mib / 1024)), 8,
      }
    elseif gpu.util and gpu.kind == 'jetson' then
      out[#out + 1] = { 'GPU', gpu.util }
    end
    if disk.pct then
      out[#out + 1] = { 'DSK', disk.pct, string.format('%sG free', size(gib(disk.free_kib))), 9 }
    end
    return out
  end

  -- Richest first; the caller takes the first tier that fits its cell budget. only =
  -- labels kept. Disk is in no tier's list: a disk past WARN is shown anyway, as is any
  -- other metric past it (a problem is never narrowed away), with its amount in the richest
  -- tier. The narrowest keeps the CPU alone.
  local TIERS = {
    { only = { CPU = true, RAM = true, GPU = true }, amount = true },
    { only = { CPU = true, RAM = true } },
    { only = { CPU = true } },
  }

  local function shown(m, tier)
    return level(m[2]) ~= 'ok' or tier.only[m[1]] == true
  end

  -- Items of one metric: dim label, the percentage muted, or amber / red past its threshold
  -- (then followed by the amount in the richest tier).
  local function body(m, tier)
    local lvl = level(m[2])
    local items = {
      { Foreground = { Color = P.dim } }, { Text = m[1] .. ' ' },
      { Foreground = { Color = TONE[lvl] } }, { Text = string.format('%3.0f%%', m[2]) },
    }
    if tier.amount and lvl ~= 'ok' and m[3] then
      items[#items + 1] = { Foreground = { Color = P.muted } }
      items[#items + 1] = { Text = ' ' .. m[3] }
    end
    return items
  end

  -- One quiet chip per tier, bare text on the header band, the metrics two spaces apart.
  local function render(tier)
    local items = {}
    for _, m in ipairs(metrics()) do
      if shown(m, tier) then
        if #items > 0 then items[#items + 1] = { Text = '  ' } end
        theme.extend(items, body(m, tier))
      end
    end
    if #items == 0 then return {} end
    return theme.click('system', items)
  end

  -- Renders only change when a source was sampled, so a tick between samples reuses
  -- the last items instead of rebuilding every tier.
  local memo = { stamp = {}, tiers = {} }
  local function cached(tier)
    local s = memo.stamp
    if s[1] ~= cpu.t or s[2] ~= mem.t or s[3] ~= disk.t or s[4] ~= gpu.t then
      s[1], s[2], s[3], s[4] = cpu.t, mem.t, disk.t, gpu.t
      memo.tiers = {}
    end
    local hit = memo.tiers[tier]
    if not hit then
      local items = render(tier)
      hit = { items = items, w = theme.width(items) }
      memo.tiers[tier] = hit
    end
    return hit
  end

  local api = {}

  -- Sample and log without rendering: the header calls this every tick, visible or not.
  function api.record() refresh() end

  function api.visible()
    return wezterm.GLOBAL.sysstats_visible ~= false
  end

  api.level = level

  -- Items and their width in cells: the richest tier within opts.budget (all
  -- tiers when absent). Returns {}, 0 when nothing fits or nothing is sampled.
  function api.segments(opts)
    refresh()
    local budget = opts and opts.budget or math.huge
    for _, tier in ipairs(TIERS) do
      local hit = cached(tier)
      if hit.w > 0 and hit.w <= budget then return hit.items, hit.w end
    end
    return {}, 0
  end

  -- Every tier richest first as { items, w }, widths included; tiers with nothing to
  -- show are skipped.
  function api.tiers()
    refresh()
    local out = {}
    for _, tier in ipairs(TIERS) do
      local hit = cached(tier)
      if hit.w > 0 then out[#out + 1] = hit end
    end
    return out
  end

  -- Width of the narrowest tier with something to show, so the header can keep room for it.
  function api.min_width()
    refresh()
    for i = #TIERS, 1, -1 do
      local w = cached(TIERS[i]).w
      if w > 0 then return w end
    end
    return 0
  end

  function api.text(opts)
    refresh()
    local compact = opts and opts.compact
    local strs = {}
    for _, m in ipairs(metrics()) do
      if not compact or m[1] == 'CPU' or m[1] == 'RAM' then
        strs[#strs + 1] = string.format('%s %.0f%%', m[1], m[2])
      end
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
