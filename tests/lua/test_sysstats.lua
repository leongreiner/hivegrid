-- sysstats: the /proc, df and nvidia-smi parsers, CPU/RAM sampling from a fixture /proc,
-- the Jetson load file, the one-child-per-second pacing with its back-offs, sample survival
-- across a config reload, and the width-budget tiers.
local T = require 'tests.lua.t'

local T0 = 1700000000
local DF = 'Filesystem     1024-blocks      Used Available Capacity Mounted on\n'
  .. '/dev/nvme0n1p2  1000000000 250000000 750000000      25% /\n'
local NV = '37, 4096, 16384\n'
local GIB = 1048576 -- KiB

local function stat(total, idle)
  -- user nice system idle iowait irq softirq steal: idle split over idle + iowait.
  local busy = total - idle
  return string.format('cpu  %d 0 0 %d %d 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8\n', busy, idle - 10, 10)
end

local function meminfo(total_kib, avail_kib)
  return string.format('MemTotal: %d kB\nMemFree: 1 kB\nMemAvailable: %d kB\n', total_kib, avail_kib)
end

-- A fixture /proc and /sys (no Jetson unless opts.jetson), df and nvidia-smi scripted.
local function setup(wz, on_child, opts)
  opts = opts or {}
  T.clock(T0)
  local proc, sys = T.tmpdir(), T.tmpdir()
  T.setenv('HIVEGRID_PROC_DIR', proc)
  T.setenv('HIVEGRID_SYS_DIR', sys)
  T.write(proc .. '/stat', stat(1000, 840))
  T.write(proc .. '/meminfo', meminfo(32 * GIB, 16 * GIB))
  if opts.jetson then T.write(sys .. '/devices/gpu.0/load', opts.jetson) end
  wz.stub.on_child = on_child or function(argv)
    if argv[1] == 'df' then return true, DF end
    if argv[1] == 'nvidia-smi' then return true, NV end
    return false, ''
  end
  local config = {}
  return require('sysstats')(config, wz), config, proc, sys
end

local function spawned(wz, name)
  local n = 0
  for _, argv in ipairs(wz.stub.children) do
    if argv[1] == name then n = n + 1 end
  end
  return n
end

-- Parsers ------------------------------------------------------------------------------------

T.test('cpu: total of the first 8 fields (guest is inside user), idle includes iowait', function(wz)
  local p = setup(wz).parse
  local total, idle = p.cpu('cpu  100 5 50 800 40 3 2 0 10 5\ncpu0 50 2 25 400 20 1 1 0 5 2\n')
  T.eq({ total, idle }, { 1000, 840 })
  T.eq({ p.cpu('cpu  1 2 3 4\n') }, { nil }, 'too few fields')
  T.eq({ p.cpu('cpu0 1 2 3 4 5 6 7 8\n') }, { nil }, 'a per-core line is not the aggregate')
  T.eq({ p.cpu(nil) }, { nil })
end)

T.test('meminfo: MemAvailable, not MemFree; incomplete or zero totals are rejected', function(wz)
  local p = setup(wz).parse
  local text = 'MemTotal:       32000000 kB\nMemFree:          500000 kB\nMemAvailable:   20000000 kB\n'
  T.eq({ p.meminfo(text) }, { 32000000, 20000000 })
  T.eq({ p.meminfo('MemTotal: 100 kB\nMemFree: 50 kB\n') }, { nil })
  T.eq({ p.meminfo('MemTotal: 0 kB\nMemAvailable: 0 kB\n') }, { nil })
end)

T.test('df: the data line after the header gives used% as df computes it and free KiB', function(wz)
  local p = setup(wz).parse
  local pct, free = p.df(DF)
  T.near(pct, 25)
  T.eq(free, 750000000)
  -- Reserved blocks: used + available < size, Capacity is used / (used + available).
  pct = p.df('Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 1000 300 600 34% /\n')
  T.near(pct, 100 * 300 / 900)
  T.eq({ p.df('Filesystem 1024-blocks Used Available Capacity Mounted on\n') }, { nil }, 'header only')
  T.eq({ p.df('x 0 0 0 0% /') }, { nil }, 'empty filesystem')
  T.eq({ p.df('') }, { nil })
end)

T.test('nvidia-smi: the first GPU only; N/A output is no sample', function(wz)
  local p = setup(wz).parse
  T.eq({ p.nvidia('37, 4096, 16384\n90, 100, 200\n') }, { 37, 4096, 16384 })
  T.eq({ p.nvidia('[N/A], [N/A], 16384\n') }, { nil })
  T.eq({ p.nvidia('') }, { nil })
end)

T.test('jetson load is permille, capped at 100%', function(wz)
  local p = setup(wz).parse
  T.eq(p.jetson('500\n'), 50)
  T.eq(p.jetson('1200'), 100)
  T.eq(p.jetson(''), nil)
end)

-- Sampling ----------------------------------------------------------------------------------

T.test('one child process per second: GPU first, the disk on the next second', function(wz)
  local ss = setup(wz)
  local text = ss.text()
  T.eq({ spawned(wz, 'nvidia-smi'), spawned(wz, 'df') }, { 1, 0 })
  T.ok(text:find('GPU 37%', 1, true), text)
  T.ok(not text:find('DSK', 1, true), text)
  ss.text()
  T.eq({ spawned(wz, 'nvidia-smi'), spawned(wz, 'df') }, { 1, 0 }, 'same second: nothing more')
  T.clock(T0 + 1)
  text = ss.text()
  T.eq({ spawned(wz, 'nvidia-smi'), spawned(wz, 'df') }, { 1, 1 })
  T.ok(text:find('DSK 25%', 1, true), text)
  T.ok(text:find('GPU 37%', 1, true), 'GPU kept between samples: ' .. text)
end)

T.test('a missing nvidia-smi is probed once and never spawned again', function(wz)
  local ss = setup(wz, function(argv)
    if argv[1] == 'nvidia-smi' then error('No such file or directory') end
    if argv[1] == 'df' then return true, DF end
  end)
  for s = 0, 300, 5 do
    T.clock(T0 + s)
    T.ok(not ss.text():find('GPU', 1, true))
  end
  T.eq(spawned(wz, 'nvidia-smi'), 1)
  T.ok(spawned(wz, 'df') >= 1, 'the disk is still sampled')
end)

T.test('a driver hiccup backs off a minute instead of spawning every tick', function(wz)
  local ss = setup(wz, function(argv)
    if argv[1] == 'nvidia-smi' then return true, 'NVIDIA-SMI has failed\n' end
    if argv[1] == 'df' then return true, DF end
  end)
  for s = 0, 59 do
    T.clock(T0 + s)
    ss.text()
  end
  T.eq(spawned(wz, 'nvidia-smi'), 1)
  T.clock(T0 + 61)
  ss.text()
  T.eq(spawned(wz, 'nvidia-smi'), 2)
end)

T.test('the disk is sampled every 30 s and a failed df is retried about a minute later', function(wz)
  local df_ok = true
  local ss = setup(wz, function(argv)
    if argv[1] == 'df' then return df_ok, df_ok and DF or '' end
    if argv[1] == 'nvidia-smi' then error('missing') end
  end)
  ss.text()
  T.eq(spawned(wz, 'df'), 0, 'the GPU probe took this second')
  for s = 1, 30 do
    T.clock(T0 + s)
    ss.text()
  end
  T.eq(spawned(wz, 'df'), 1, 'one df in the first 30 s')
  df_ok = false
  T.clock(T0 + 31)
  ss.text()
  T.eq(spawned(wz, 'df'), 2)
  T.ok(ss.text():find('DSK 25%', 1, true), 'a failed df keeps the last value')
  for s = 32, 89 do
    T.clock(T0 + s)
    ss.text()
  end
  T.eq(spawned(wz, 'df'), 2, 'no retry storm after a failure')
  T.clock(T0 + 95)
  ss.text()
  T.eq(spawned(wz, 'df'), 3)
end)

T.test('samples survive a config reload without spawning again', function(wz)
  local ss = setup(wz)
  ss.text()
  T.clock(T0 + 1)
  ss.text()
  T.eq({ spawned(wz, 'nvidia-smi'), spawned(wz, 'df') }, { 1, 1 })
  local again = require('sysstats')({}, wz) -- what a reload does
  local text = again.text()
  T.ok(text:find('DSK 25%', 1, true) and text:find('GPU 37%', 1, true), text)
  T.eq({ spawned(wz, 'nvidia-smi'), spawned(wz, 'df') }, { 1, 1 }, 'nothing re-spawned in the same second')
end)

-- Rendering --------------------------------------------------------------------------------

T.test('segments: the richest tier that fits the budget, nothing when none fits', function(wz)
  local ss = setup(wz)
  ss.text()
  T.clock(T0 + 1)
  local tiers = ss.tiers()
  T.ok(#tiers >= 2, 'several tiers')
  for i = 2, #tiers do T.ok(tiers[i].w <= tiers[i - 1].w, 'tier ' .. i .. ' not narrower') end
  local _, w = ss.segments()
  T.eq(w, tiers[1].w, 'no budget: the richest')
  local _, w2 = ss.segments { budget = tiers[1].w - 1 }
  T.ok(w2 < tiers[1].w and w2 > 0, 'a smaller budget steps down')
  for _, t in ipairs(tiers) do
    if t.w <= tiers[1].w - 1 then
      T.eq(w2, t.w, 'the first tier that fits')
      break
    end
  end
  T.eq(ss.min_width(), tiers[#tiers].w)
  local items, w0 = ss.segments { budget = ss.min_width() - 1 }
  T.eq({ items, w0 }, { {}, 0 })
end)

T.test('compact text keeps only CPU and RAM', function(wz)
  local ss, _, proc = setup(wz)
  ss.text()
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 2)
  local full, compact = ss.text(), ss.text { compact = true }
  T.ok(full:find('GPU', 1, true) and full:find('DSK', 1, true), full)
  T.eq(compact, 'CPU 50%  RAM 50%')
end)

-- Colour of the first cell of `needle` in the rendered items (ASCII text only).
local function fg_at(items, needle)
  local cells, chars = T.render(items), {}
  for i, c in ipairs(cells) do chars[i] = c.ch end
  local at = table.concat(chars):find(needle, 1, true)
  T.ok(at, 'no ' .. needle .. ' in ' .. table.concat(chars))
  return cells[at].fg
end

local function text_of(items)
  local chars = {}
  for i, c in ipairs(T.render(items)) do chars[i] = c.ch end
  return table.concat(chars)
end

T.test('level: warning from 60 %, critical above 85 %', function(wz)
  local ss = setup(wz)
  T.eq({ ss.level(0), ss.level(59.9), ss.level(60), ss.level(85), ss.level(85.1), ss.level(100) },
    { 'ok', 'ok', 'warn', 'warn', 'crit', 'crit' })
end)

T.test('a calm machine: one muted chip with CPU, RAM and GPU, no disk, no amounts, no colour', function(wz)
  local ss, _, proc = setup(wz)
  local P = require('theme').P
  ss.text()
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 2)
  ss.text() -- the second to sample the disk too
  T.clock(T0 + 3)
  local rich = ss.tiers()[1].items
  T.eq(text_of(rich), 'CPU  50%  RAM  50%  GPU  37%')
  for _, c in ipairs(T.render(rich)) do
    T.ok(c.fg == P.muted or c.fg == P.dim, 'only muted text: ' .. c.ch .. ' ' .. tostring(c.fg))
    T.ok(c.bg == nil, 'bare text on the header band')
  end
  T.eq(fg_at(rich, '50%'), P.muted)
  T.eq(fg_at(rich, 'CPU'), P.dim)
end)

T.test('a metric past its threshold takes amber or red and its amount; the rest stays muted', function(wz)
  local df = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 1000000000 950000000 50000000 95% /\n'
  local ss, _, proc = setup(wz, function(argv)
    if argv[1] == 'df' then return true, df end
    if argv[1] == 'nvidia-smi' then return true, '70, 12288, 16384\n' end
    return false, ''
  end)
  local P = require('theme').P
  T.write(proc .. '/meminfo', meminfo(32 * GIB, 3 * GIB))
  ss.text()
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 2)
  ss.text()
  T.clock(T0 + 3)
  local rich = ss.tiers()[1].items
  local text = text_of(rich)
  T.eq(fg_at(rich, '50%'), P.muted) -- CPU
  T.eq(fg_at(rich, '91%'), P.red) -- RAM, above 85
  T.eq(fg_at(rich, '70%'), P.amber) -- GPU, from 60
  T.eq(fg_at(rich, '95%'), P.red) -- the disk appears when it runs full
  T.ok(text:find('29.0G/32G', 1, true), text)
  T.ok(text:find('12.0/16G', 1, true), text)
  T.ok(text:find('DSK  95% 48G free', 1, true), text)
  T.ok(not text:find('CPU  50% %S+G'), 'CPU has no amount')
end)

T.test('tiers narrow from CPU, RAM and GPU to CPU and RAM to the CPU alone; a hot metric stays', function(wz)
  local ss, _, proc = setup(wz)
  ss.text()
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 2)
  local tiers = ss.tiers()
  T.eq(#tiers, 3)
  local rich, pair, narrow = text_of(tiers[1].items), text_of(tiers[2].items), text_of(tiers[3].items)
  T.ok(rich:find('GPU', 1, true) and rich:find('RAM', 1, true), rich)
  T.ok(pair:find('RAM', 1, true) and not pair:find('GPU', 1, true), pair)
  T.eq(narrow, 'CPU  50%')
  T.ok(tiers[1].w > tiers[2].w and tiers[2].w > tiers[3].w, 'each tier is narrower')
  -- RAM runs hot: it is in the narrowest tier too, a problem is never narrowed away.
  T.write(proc .. '/meminfo', meminfo(32 * GIB, 2 * GIB))
  T.clock(T0 + 4)
  T.ok(text_of(ss.tiers()[3].items):find('RAM  94%', 1, true))
end)

T.test('Alt+s toggles the stats', function(wz)
  local ss, config = setup(wz)
  T.eq(ss.visible(), true)
  local toggle
  for _, k in ipairs(config.keys) do
    if k.key == 's' and k.mods == 'ALT' then toggle = k.action end
  end
  wz.stub.perform(toggle, nil, nil)
  T.eq(ss.visible(), false)
  wz.stub.perform(toggle, nil, nil)
  T.eq(ss.visible(), true)
end)

-- CPU, RAM and the Jetson GPU from fixture files -----------------------------------------------

T.test('CPU needs two readings; its value is the busy share between them', function(wz)
  local ss, _, proc = setup(wz)
  T.ok(not ss.text():find('CPU', 1, true), 'one reading is no percentage')
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 1)
  T.ok(not ss.text():find('CPU', 1, true), 're-read at most every 2 s')
  T.clock(T0 + 2)
  T.ok(ss.text():find('CPU 50%', 1, true), ss.text())
end)

T.test('after a gap of more than 10 s the CPU baseline restarts instead of averaging the gap', function(wz)
  local ss, _, proc = setup(wz)
  ss.text()
  T.write(proc .. '/stat', stat(1200, 940))
  T.clock(T0 + 2)
  ss.text()
  T.write(proc .. '/stat', stat(101200, 100800)) -- 1% busy over the hidden minute
  T.clock(T0 + 62)
  T.ok(not ss.text():find('CPU 1%', 1, true), 'the gap is not reported: ' .. ss.text())
  T.write(proc .. '/stat', stat(101400, 100850)) -- 75% busy in the last 2 s
  T.clock(T0 + 64)
  T.ok(ss.text():find('CPU 75%', 1, true), ss.text())
end)

T.test('a CPU counter going backwards gives no bogus value', function(wz)
  local ss, _, proc = setup(wz)
  ss.text()
  T.write(proc .. '/stat', stat(500, 400))
  T.clock(T0 + 2)
  T.ok(not ss.text():find('CPU', 1, true), ss.text())
  T.write(proc .. '/stat', stat(700, 500))
  T.clock(T0 + 4)
  T.ok(ss.text():find('CPU 50%', 1, true), ss.text())
end)

T.test('RAM counts available memory as free, not just MemFree', function(wz)
  local ss, _, proc = setup(wz)
  T.ok(ss.text():find('RAM 50%', 1, true), ss.text())
  T.write(proc .. '/meminfo', meminfo(32 * GIB, 8 * GIB))
  T.clock(T0 + 2)
  T.ok(ss.text():find('RAM 75%', 1, true), ss.text())
end)

T.test('missing /proc files drop CPU and RAM but keep the other stats', function(wz)
  local ss, _, proc = setup(wz)
  os.remove(proc .. '/stat')
  os.remove(proc .. '/meminfo')
  local text = ss.text()
  T.ok(not text:find('CPU', 1, true) and not text:find('RAM', 1, true), text)
  T.ok(text:find('GPU 37%', 1, true), text)
end)

T.test('a Jetson load file is the GPU source and nvidia-smi is never run', function(wz)
  local ss = setup(wz, nil, { jetson = '730\n' })
  local text = ss.text()
  T.ok(text:find('GPU 73%', 1, true), text)
  T.eq(spawned(wz, 'nvidia-smi'), 0)
  T.eq(spawned(wz, 'df'), 1, 'the second free for the disk')
  local rich = wz.format(ss.tiers()[1].items)
  T.ok(rich:find('GPU', 1, true) and not rich:find('/16G', 1, true), 'no memory amount on Jetson: ' .. rich)
end)

-- History log ----------------------------------------------------------------------------------

local function lines(path)
  local out = {}
  for l in (T.read(path) or ''):gmatch('[^\n]+') do out[#out + 1] = l end
  return out
end

T.test('record() logs a line every 15 s with the mean CPU, with the header hiding the stats or not', function(wz)
  local log = T.tmpdir() .. '/sysstats.jsonl'
  T.setenv('HIVEGRID_SYSSTATS_LOG', log)
  local ss, _, proc = setup(wz)
  for s = 0, 29 do
    T.clock(T0 + s)
    if s == 2 then T.write(proc .. '/stat', stat(1200, 940)) end
    ss.record()
  end
  T.eq(lines(log), {
    string.format('{"ts":%d,"ram":50.0,"gpu":37.0}', T0), -- CPU needs two readings, the disk a second
    string.format('{"ts":%d,"cpu":50.0,"ram":50.0,"dsk":25.0,"gpu":37.0}', T0 + 15),
  })
end)

T.test('with the stats hidden the GPU is read once per log line, not every 3 s', function(wz)
  local log = T.tmpdir() .. '/sysstats.jsonl'
  T.setenv('HIVEGRID_SYSSTATS_LOG', log)
  wz.GLOBAL.sysstats_visible = false
  local ss = setup(wz)
  for s = 0, 29 do
    T.clock(T0 + s)
    ss.record()
  end
  T.eq(spawned(wz, 'nvidia-smi'), 2)
  T.eq(#lines(log), 2)
end)

T.test('the log file is created with its directory and rotates to .1 at 2 MiB', function(wz)
  local dir = T.tmpdir() .. '/new/hivegrid'
  local log = dir .. '/sysstats.jsonl'
  T.setenv('HIVEGRID_SYSSTATS_LOG', log)
  local ss = setup(wz, function(argv)
    if argv[1] == 'mkdir' then return os.execute('mkdir -p ' .. T.sh_quote(argv[3])) == true, '' end
    return false, ''
  end)
  ss.record()
  T.eq(#lines(log), 1)
  T.write(log, string.rep('x', 2 * 1024 * 1024))
  T.clock(T0 + 15)
  ss.record()
  T.eq(T.read(log), nil, 'the full file moved away')
  T.ok(#T.read(log .. '.1') > 2 * 1024 * 1024, 'the rotated file holds the old data and the line that filled it')
  T.clock(T0 + 30)
  ss.record()
  T.eq(#lines(log), 1, 'a fresh file after the rotation')
end)
