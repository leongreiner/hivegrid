-- The header's right side (config/header.lua, wired in config/wezterm.lua): the lead chip with
-- project, git branch and the running-agent count, the width tiers that keep it and drop the
-- metadata first, and the whole header at several window widths.
local T = require 'tests.lua.t'

local T0 = 1700000000

-- A repository fixture: <root>/.git/HEAD naming `head`; returns the root.
local function repo(name, head)
  local root = T.tmpdir() .. '/' .. name
  T.write(root .. '/.git/HEAD', head or 'ref: refs/heads/main\n')
  return root
end

local function omp(wz, id, title, cwd)
  return wz.fake.pane { id = id, title = title, process_name = '/usr/bin/omp', text = 'omp screen', cwd = cwd }
end

local function module(wz, as)
  as = as or { running = function() return { working = 0, busy = 0, attention = 0, limit = 0 } end, focus_dir = function() end }
  return require('header')({}, wz, as)
end

local function plain(items)
  local out = {}
  for _, c in ipairs(T.render(items)) do out[#out + 1] = c.ch end
  return table.concat(out)
end

-- Git ------------------------------------------------------------------------------------------

T.test('git: the branch and root from .git/HEAD, a subdirectory and a detached HEAD included', function(wz)
  local hd = module(wz)
  local root = repo('proj', 'ref: refs/heads/feature/x-1\n')
  T.write(root .. '/src/deep/f.txt', 'x')
  T.eq({ hd.git(root) }, { 'feature/x-1', root })
  T.eq({ hd.git(root .. '/src/deep') }, { 'feature/x-1', root }, 'found from a subdirectory')
  local det = repo('det', 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678\n')
  T.eq({ hd.git(det) }, { 'a1b2c3d', det }, 'a detached HEAD reads as the short hash')
  T.eq({ hd.git(T.tmpdir()) }, { nil, nil }, 'outside a repository')
  T.eq({ hd.git(nil) }, { nil, nil })
end)

T.test('git: a worktree or submodule .git file points at the real git directory', function(wz)
  local hd = module(wz)
  local base = T.tmpdir()
  T.write(base .. '/main/.git/worktrees/wt/HEAD', 'ref: refs/heads/ux/1\n')
  T.write(base .. '/wt/.git', 'gitdir: ' .. base .. '/main/.git/worktrees/wt\n')
  T.eq({ hd.git(base .. '/wt') }, { 'ux/1', base .. '/wt' })
end)

T.test('git: the answer is cached for 5 seconds, then the new branch shows', function(wz)
  local hd = module(wz)
  T.clock(T0)
  local root = repo('proj')
  T.eq((hd.git(root)), 'main')
  T.write(root .. '/.git/HEAD', 'ref: refs/heads/other\n')
  T.clock(T0 + 2)
  T.eq((hd.git(root)), 'main', 'cached')
  T.clock(T0 + 6)
  T.eq((hd.git(root)), 'other')
end)

-- Tiers and the plan -----------------------------------------------------------------------------

local COUNTS = { working = 2, busy = 1, attention = 1 }

T.test('the lead chip: project bold and bright, branch, then the agent states in the shared vocabulary', function(wz)
  local hd = module(wz)
  local theme = require 'theme'
  local P, STATES = theme.P, theme.STATES
  local tiers = hd.tiers { project = 'hivegrid-workspace-long', branch = 'main', counts = COUNTS }
  T.eq(#tiers, 4)
  local rich = tiers[1]
  local text = plain(rich.items)
  T.ok(text:find('hivegrid-workspace-long · main', 1, true), text)
  T.ok(text:find('? 1 needs you', 1, true) and text:find('↻ 2 working', 1, true) and text:find('◔ 1 busy', 1, true), text)
  T.ok(text:find('needs you', 1, true) < text:find('working', 1, true), 'most urgent first')
  local cells = T.render(rich.items)
  local function cell(ch)
    for _, c in ipairs(cells) do
      if c.ch == ch then return c end
    end
  end
  T.eq({ cell('h').fg, cell('h').bold }, { P.bright, true })
  T.eq(cell('?').fg, STATES.attention.color)
  T.eq(cell('↻').fg, STATES.working.color)
  T.eq(cell('◔').fg, STATES.busy.color)
  for i = 2, #tiers do T.ok(tiers[i].w < tiers[i - 1].w, 'tier ' .. i .. ' narrower') end
  T.eq(rich.w, theme.width(rich.items))
  local last = plain(tiers[4].items)
  T.ok(last:find('hivegrid-…', 1, true) and last:find('?1', 1, true) and not last:find('main', 1, true), last)
end)

T.test('a usage limit shows with the ⊘ glyph in the state colour, after needs you and before working', function(wz)
  local hd = module(wz)
  local theme = require 'theme'
  local STATES = theme.STATES
  T.eq({ STATES.limit.glyph, STATES.limit.text, STATES.limit.key }, { '⊘', 'limit', 'red' })
  local counts = { working = 2, busy = 1, attention = 1, limit = 3 }
  local tiers = hd.tiers { project = 'proj', counts = counts }
  local text = plain(tiers[1].items)
  T.ok(text:find('⊘ 3 limit', 1, true), text)
  T.ok(text:find('needs you', 1, true) < text:find('limit', 1, true)
    and text:find('limit', 1, true) < text:find('working', 1, true), 'order: needs you, limit, working: ' .. text)
  for _, c in ipairs(T.render(tiers[1].items)) do
    if c.ch == '⊘' then T.eq(c.fg, STATES.limit.color) end
  end
  local short = plain(tiers[4].items)
  T.ok(short:find('⊘3', 1, true), short)
  local alone = hd.tiers { project = 'p', counts = { working = 0, busy = 0, attention = 0, limit = 1 } }[1]
  T.ok(plain(alone.items):find('⊘ 1 limit', 1, true) and not plain(alone.items):find('working', 1, true))
end)

T.test('the context model counts a limit as an agent that wants a look', function(wz)
  local hd = module(wz, {
    running = function() return { working = 0, busy = 0, attention = 0, limit = 1 } end,
    focus_dir = function() end,
  })
  local m = hd.context({ active_workspace = function() return 'default' end })
  T.eq(m and m.counts.limit, 1, 'no project and no workspace, but a limit: the chip still shows')
end)

T.test('the lead chip keeps the workspace, cuts long names, and shows nothing when there is nothing', function(wz)
  local hd = module(wz)
  local long = hd.tiers { workspace = 'review', project = string.rep('x', 40), counts = COUNTS }
  T.ok(plain(long[1].items):find('review · x', 1, true))
  T.ok(plain(long[1].items):find('…', 1, true), 'a name past 28 characters is cut')
  T.ok(not plain(long[2].items):find('review', 1, true), 'the workspace is the first thing to go')
  T.eq(hd.tiers(nil), {})
  T.eq(hd.tiers({ counts = { working = 0, busy = 0, attention = 0, limit = 0 } })[1].w, 0)
  T.eq(hd.tiers({ counts = { working = 0, busy = 0, attention = 0 } })[1].w, 0, 'a model without a limit count')
end)

T.test('the agent counts are one click target and name only states that want a look', function(wz)
  wz.stub.fork = true
  local hd = module(wz)
  local t = hd.tiers { project = 'p', counts = { working = 3, busy = 0, attention = 0 } }[1]
  local clicks = 0
  for _, it in ipairs(t.items) do
    if type(it) == 'table' and it.Click then
      T.eq(it.Click, 'agents')
      clicks = clicks + 1
    end
  end
  T.eq(clicks, 1)
  local text = plain(t.items)
  T.ok(text:find('3 working', 1, true) and not text:find('busy', 1, true) and not text:find('idle', 1, true), text)
end)

-- Groups of fixed width, to test the plan in isolation.
local function group(...)
  local tiers = {}
  for i, w in ipairs { ... } do tiers[i] = { w = w, items = {} } end
  return { tiers = tiers }
end

T.test('plan: metadata narrows first; the lead chip keeps its words longest and is never dropped', function(wz)
  local hd = module(wz)
  local function run(avail)
    local g = { A = group(40, 30, 20, 12), B = group(40, 30, 20, 12, 5), C = group(30, 14, 4) }
    local ok = hd.fit(g, 0, avail)
    return { ok, g.A.tier, g.B.tier, g.C.tier }
  end
  -- every group costs its width plus 2
  T.eq(run(200), { true, 1, 1, 1 }, 'room for everything')
  T.eq(run(100), { true, 1, 2, 2 }, 'quota and system narrow before the context')
  local mid = run(80)
  T.eq(mid[2], 2, 'then the context gives up its workspace')
  T.ok(mid[3] >= 2 and mid[4] >= 2)
  T.eq(run(10), { false, 4, math.huge, 3 }, 'the quota is dropped, the context cut to its last tier, the CPU chip stays')
end)

T.test('plan: a tier that is not narrower than the current one is skipped', function(wz)
  local hd = module(wz)
  local g = { A = group(10), B = group(8, 8, 8), C = group(6) }
  T.eq(hd.fit(g, 0, 1), false)
  T.eq({ g.A.tier, g.B.tier, g.C.tier }, { 1, math.huge, 1 }, 'equal-width tiers are skipped, only the drop acts')
end)

-- The whole header ---------------------------------------------------------------------------------

-- Loads config/wezterm.lua over fixture quota and system files; returns a window of one tab.
local function main(wz, rows, px)
  wz.stub.fork = true
  local proc = T.tmpdir()
  T.setenv('HIVEGRID_PROC_DIR', proc)
  T.setenv('HIVEGRID_SYS_DIR', T.tmpdir())
  T.write(proc .. '/stat', 'cpu  1000 0 0 840 10 0 0 0 0 0\n')
  T.write(proc .. '/meminfo', 'MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n')
  wz.stub.on_child = function(argv)
    if argv[1] == 'nvidia-smi' then return true, '37, 4096, 16384\n' end
    return false, ''
  end
  local cache = T.tmpdir()
  T.setenv('CLAUDE_QUOTA_CACHE_DIR', cache)
  T.setenv('HIVEGRID_SETTINGS', T.write(T.tmpdir() .. '/s.json', wz.json_encode {
    accounts = { { label = 'main', dir = '~/.claude' }, { label = '2', dir = '~/.claude-2' } },
  }))
  for _, e in ipairs { { 'main', 30, 85 }, { '2', 10, 20 } } do
    T.write(cache .. '/' .. e[1] .. '.json', wz.json_encode {
      label = e[1], state = 'ok', updated_at = T0 - 10,
      five_hour = { pct = e[2], reset_at = T0 + 3600 }, seven_day = { pct = e[3], reset_at = T0 + 86400 },
    })
  end
  dofile(T.root .. '/config/wezterm.lua')
  local win = wz.fake.grid(rows, {
    focused = false, dims = { pixel_width = px, pixel_height = 900, dpi = 96, is_full_screen = false },
  })
  for i = 1, 3 do -- CPU needs two readings
    T.write(proc .. '/stat', string.format('cpu  %d 0 0 %d 10 0 0 0 0 0\n', 1000 + 100 * i, 840 + 100 * i))
    T.clock(T0 + i * 3)
    wz.emit('update-status', win, win:active_pane())
  end
  return win.status.right
end

local function agents(wz, root)
  local function pane(id, title, left, top)
    return { pane = omp(wz, id, title, root), left = left, top = top, width = 40, height = 12, active = id == 1 }
  end
  return { pane(1, 'π ⠋ a', 0, 0), pane(2, 'π ⠋ b', 41, 0), pane(3, 'π ! c', 0, 13), pane(4, 'π > d', 41, 13) }
end

T.test('the whole header on a wide window: project, branch and agents lead, then quota and system', function(wz)
  T.clock(T0)
  local right = main(wz, agents(wz, repo('hivegrid')), 2560)
  local order = {}
  for _, needle in ipairs { 'hivegrid', 'main', '1 needs you', '2 working', '5h', 'CPU' } do
    order[#order + 1] = right:find(needle, 1, true)
    T.ok(order[#order], needle .. ' in: ' .. right)
  end
  for i = 2, #order do T.ok(order[i] > order[i - 1], 'left to right in the importance order: ' .. right) end
  T.ok(not right:find('idle', 1, true), 'an idle agent is no running agent')
  T.ok(right:find('GPU', 1, true) and right:find('RAM', 1, true))
  T.ok(not right:find('DSK', 1, true), 'the disk shows only when it runs full')
end)

T.test('the whole header narrows with the window and keeps the project and the CPU chip', function(wz)
  local seen = {}
  for i, px in ipairs { 2560, 1920, 1280, 640 } do
    if i > 1 then wz = T.fresh() end
    T.clock(T0)
    local right = main(wz, agents(wz, repo('hivegrid')), px)
    seen[#seen + 1] = right
    T.ok(right:find('hivegrid', 1, true), px .. ': ' .. right)
    T.ok(right:find('CPU', 1, true), px .. ': ' .. right)
    T.ok(right:find('working', 1, true) or right:find('↻', 1, true), px .. ': the agents stay')
  end
  T.ok(seen[1]:find('main', 1, true) and seen[1]:find('GPU', 1, true), 'wide: everything')
  T.ok(seen[1]:find('5h', 1, true))
  T.ok(not seen[4]:find('5h', 1, true) and not seen[4]:find('GPU', 1, true), 'narrow: the metadata went first')
  for i = 2, #seen do T.ok(#seen[i] <= #seen[i - 1], 'never wider than the window before') end
end)

T.test('without a repository the project is the directory and there is no branch', function(wz)
  T.clock(T0)
  local dir = T.tmpdir() .. '/notes'
  T.write(dir .. '/x', '')
  local right = main(wz, agents(wz, dir), 1920)
  T.ok(right:find('notes', 1, true) and not right:find('notes ·', 1, true), 'no branch outside a repository: ' .. right)
end)
