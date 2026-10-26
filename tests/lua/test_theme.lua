-- config/theme.lua drawing helpers: colour blends, gradients, widths, bars, pills, clicks.
local T = require 'tests.lua.t'

local function fgs(cells)
  local out = {}
  for i, c in ipairs(cells) do out[i] = c.fg end
  return out
end

local function chars(cells)
  local out = {}
  for i, c in ipairs(cells) do out[i] = c.ch end
  return table.concat(out)
end

local function rep(v, n)
  local out = {}
  for i = 1, n do out[i] = v end
  return out
end

T.test('lerp: endpoints exact, midpoint rounds, mixed-case input', function()
  local th = require 'theme'
  T.eq(th.lerp('#102030', '#f0e0d0', 0), '#102030')
  T.eq(th.lerp('#102030', '#f0e0d0', 1), '#f0e0d0')
  T.eq(th.lerp('#000000', '#ffffff', 0.5), '#808080') -- 127.5 rounds up
  T.eq(th.lerp('#000000', '#FFFFFF', 0.25), '#404040')
  T.eq(th.lerp('#ffffff', '#000000', 0.5), '#808080')
end)

T.test('gradient: one background per glyph, sampled at cell centres', function()
  local th = require 'theme'
  local items = th.gradient({ { fg = '#111111', text = 'a' }, { fg = '#222222', text = '▬é', bold = true } },
    '#000000', '#ffffff')
  local cells = T.render(items)
  T.eq(chars(cells), 'a▬é', 'multi-byte glyphs are not split')
  T.eq(cells[1].bg, th.lerp('#000000', '#ffffff', 0.5 / 3))
  T.eq(cells[3].bg, th.lerp('#000000', '#ffffff', 2.5 / 3))
  T.eq(fgs(cells), { '#111111', '#222222', '#222222' })
  T.eq({ cells[1].bold, cells[2].bold }, { false, true })
  T.eq(th.width(items), 3)
end)

T.test('gradient: first_bg overrides only the first cell', function()
  local th = require 'theme'
  local cells = T.render(th.gradient({ { fg = '#ffffff', text = 'ab' } }, '#000000', '#ffffff', '#123456'))
  T.eq(cells[1].bg, '#123456')
  T.eq(cells[2].bg, th.lerp('#000000', '#ffffff', 0.75))
end)

T.test('width counts code points of Text items only', function()
  local th = require 'theme'
  T.eq(th.width { { Text = '▬▬▬' }, 'ResetAttributes', { Foreground = { Color = '#000000' } },
    { Text = th.CAP_L .. 'x' .. th.CAP_R }, { Text = '' } }, 6)
  T.eq(th.width {}, 0)
end)

-- Meter -----------------------------------------------------------------------------

local function meter_cells(pct, cells)
  local th = require 'theme'
  local fill, bg = '#ff0000', '#000000'
  local out = T.render(th.meter(pct, cells, fill, bg))
  return out, fill, th.lerp(bg, th.P.edge_hi, 0.48), bg
end

T.test('meter: always exactly `cells` glyphs on the given background', function()
  for _, pct in ipairs { -20, 0, 0.1, 1, 33, 55, 99, 100, 140 } do
    local cells = meter_cells(pct, 7)
    T.eq(#cells, 7, 'pct ' .. pct)
    for _, c in ipairs(cells) do
      T.eq(c.ch, '▬', 'pct ' .. pct)
      T.eq(c.bg, '#000000', 'pct ' .. pct)
    end
  end
end)

T.test('meter: out-of-range percentages clamp to empty / full', function()
  local cells, fill, track = meter_cells(-5, 4)
  T.eq(fgs(cells), rep(track, 4), 'negative -> empty')
  cells = meter_cells(0, 4)
  T.eq(fgs(cells), rep(track, 4), '0 -> empty')
  cells = meter_cells(100, 4)
  T.eq(fgs(cells), rep(fill, 4), '100 -> full')
  cells = meter_cells(250, 4)
  T.eq(fgs(cells), rep(fill, 4), 'over 100 -> full')
end)

T.test('meter: edge cell blends in proportion; any usage shows', function()
  local th = require 'theme'
  local cells, fill, track = meter_cells(55, 10) -- 5.5 cells
  local want = rep(fill, 5)
  want[6] = th.lerp(track, fill, 0.5)
  for k = 7, 10 do want[k] = track end
  T.eq(fgs(cells), want)
  cells = meter_cells(1, 10) -- 0.1 cell would round to nothing
  T.ok(cells[1].fg ~= track, '1% still lights the first cell')
  T.eq(cells[2].fg, track)
end)

-- Line ------------------------------------------------------------------------------

local function line(pct, cells)
  local th = require 'theme'
  return T.render(th.line(pct, cells, '#ff0000', '#333333'))
end

T.test('line: halves, full and track add up to `cells`', function()
  local cells = line(50, 5) -- 5 halves: 2 full, 1 half, 2 track
  T.eq(chars(cells), '━━╸━━')
  T.eq(fgs(cells), { '#ff0000', '#ff0000', '#ff0000', '#333333', '#333333' })
  for _, pct in ipairs { -10, 0, 1, 49, 50, 99, 100, 180 } do
    T.eq(#line(pct, 6), 6, 'pct ' .. pct)
  end
end)

T.test('line: clamps out-of-range and shows any non-zero usage', function()
  T.eq(fgs(line(-3, 4)), rep('#333333', 4))
  T.eq(fgs(line(100, 4)), rep('#ff0000', 4))
  T.eq(fgs(line(400, 4)), rep('#ff0000', 4))
  T.eq(chars(line(1, 10)), '╸' .. string.rep('━', 9))
  T.eq(line(1, 10)[1].fg, '#ff0000')
end)

-- Pill / group / click ----------------------------------------------------------------

T.test('pill: every body glyph sits on the pill colour, even after a reset', function()
  local th = require 'theme'
  local items = th.pill({ { Text = 'ab' }, 'ResetAttributes', { Text = 'cd' } }, '#224466')
  local cells = T.render(items)
  T.eq(chars(cells), th.CAP_L .. ' abcd ' .. th.CAP_R)
  T.eq(cells[1].fg, '#224466', 'left cap drawn in the pill colour')
  T.eq(cells[1].bg, nil, 'left cap sits on the bar')
  for i = 2, #cells - 1 do T.eq(cells[i].bg, '#224466', 'cell ' .. i) end
  T.eq(cells[#cells].bg, nil, 'right cap sits on the bar')
  T.eq(items[#items], 'ResetAttributes', 'nothing leaks into what follows')
end)

T.test('group: one divider between neighbours only', function()
  local th = require 'theme'
  local text = chars(T.render(th.group({ { { Text = 'a' } }, { { Text = 'b' } }, { { Text = 'c' } } }, '#224466')))
  T.eq(text, th.CAP_L .. ' a │ b │ c ' .. th.CAP_R)
  T.eq(chars(T.render(th.group({ { { Text = 'a' } } }, '#224466'))), th.CAP_L .. ' a ' .. th.CAP_R)
end)

T.test('click on stock WezTerm returns the items unchanged (format must not fail)', function(wz)
  local th = require 'theme'
  T.eq(th.has_click, false)
  local items = { { Text = 'x' }, 'ResetAttributes' }
  T.eq(th.click('id', items), items)
  wz.format(th.click('id', items)) -- would raise on stock WezTerm with Click items
end)

T.test('click on the fork re-opens the region after each inner reset', function(wz)
  wz.stub.fork = true
  local th = require 'theme'
  T.eq(th.has_click, true)
  local a, b = { Text = 'a' }, { Text = 'b' }
  T.eq(th.click('id', { a, 'ResetAttributes', b, 'ResetAttributes' }),
    { { Click = 'id' }, a, 'ResetAttributes', { Click = 'id' }, b, 'ResetAttributes', 'ClickEnd' })
end)
