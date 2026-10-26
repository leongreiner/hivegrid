-- System stats for the header's right status: CPU, RAM, disk of /, GPU.
-- Wire: local ss = require('sysstats')(config, wezterm)
--   ss.visible()        -> boolean (ALT+s toggles; default true)
--   ss.segments(opts)   -> wezterm.format items, width in cells; opts.budget picks the richest
--                          tier (bars, amounts) that fits; {} , 0 if none does
--   ss.text(opts)       -> plain text of the same content, for tests
-- Sampling happens inside segments()/text() behind per-source caches, so no
-- update-status handler is registered and a hidden segment costs nothing.
-- Units: all sizes are GiB (1024^3), printed with a "G" suffix.
return function(config, wezterm)
  local theme = require 'theme'
  local P = theme.P
  local CPU_MEM_TTL, GPU_TTL, DISK_TTL, GPU_RETRY = 2, 3, 30, 60
  local JETSON_LOAD = { '/sys/devices/platform/gpu.0/load', '/sys/devices/gpu.0/load' }

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
    if pct > 85 then return P.red end
    if pct >= 60 then return P.amber end
    return P.green
  end

  local function gib(kib) return kib / 1048576 end
  local function size(g) return g >= 10 and string.format('%.0f', g) or string.format('%.1f', g) end

  -- One entry per available source: { label, pct, amount, inner }. inner is the width of
  -- the bar's text area in the amount tier, fixed per source so a changing number
  -- never shifts the groups beside it.
  local function metrics()
    local out = {}
    if cpu.pct then out[#out + 1] = { 'CPU', cpu.pct, nil, 6 } end
    if mem.total then
      local used = gib(mem.total - mem.avail)
      out[#out + 1] = {
        'RAM', 100 * (mem.total - mem.avail) / mem.total,
        string.format('%.1fG/%sG', used, size(gib(mem.total))), 10,
      }
    end
    if disk.pct then
      out[#out + 1] = { 'DSK', disk.pct, string.format('%sG free', size(gib(disk.free_kib))), 10 }
    end
    if gpu.util and gpu.kind == 'nvidia' and gpu.total_mib then
      out[#out + 1] = {
        'GPU', gpu.util, string.format('%.1f/%sG', gpu.used_mib / 1024, size(gpu.total_mib / 1024)), 8,
      }
    elseif gpu.util and gpu.kind == 'jetson' then
      out[#out + 1] = { 'GPU', gpu.util, nil, 6 }
    end
    return out
  end

  -- Richest first; the caller takes the first tier that fits its cell budget.
  -- bar: gauges with the value inside (inside = 'amount': the size, 'pct': the
  -- percentage); without bar only label and percentage. only = labels kept.
  local TIERS = {
    { bar = true, inside = 'amount' },
    { bar = true, inside = 'pct', inner = 5 },
    { bar = false },
    { bar = false, only = { CPU = true, RAM = true } },
  }

  -- Items of one metric: muted label, then a gauge with its value inside (bar tiers)
  -- or the coloured percentage.
  local function body(m, tier)
    local items = { { Foreground = { Color = P.muted } }, { Text = m[1] .. ' ' } }
    if tier.bar then
      local text = tier.inside == 'amount' and m[3] or string.format('%.0f%%', m[2])
      theme.extend(items, theme.gauge_text(
        m[2], (tier.inner or m[4]) + 2, color(m[2]), P.pill, text))
    else
      items[#items + 1] = { Foreground = { Color = color(m[2]) } }
      items[#items + 1] = { Text = string.format('%3.0f%%', m[2]) }
    end
    return items
  end

  -- One continuous pill per tier, a thin divider between the metrics.
  local function render(tier)
    local parts = {}
    for _, m in ipairs(metrics()) do
      if not tier.only or tier.only[m[1]] then parts[#parts + 1] = body(m, tier) end
    end
    if #parts == 0 then return {} end
    return theme.group(parts, P.pill)
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

  function api.visible()
    return wezterm.GLOBAL.sysstats_visible ~= false
  end

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

  -- Width of the narrowest tier, so the header can keep room for it.
  function api.min_width()
    refresh()
    return cached(TIERS[#TIERS]).w
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
