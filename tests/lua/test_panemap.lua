-- panemap.numbering: reading-order agent numbers from panes_with_info rectangles.
local T = require 'tests.lua.t'

local function rect(left, top, width, height)
  return { left = left, top = top, width = width or 40, height = height or 12 }
end

-- Numbers in input order, e.g. { 2, 1, 3 }.
local function numbers(infos)
  local num = require('panemap').numbering(infos)
  local out = {}
  for i = 1, #infos do out[i] = num[i] end
  return out
end

T.test('independently split rows with 1-cell jitter keep reading order', function()
  -- Two rows of three split separately: the dividers of row 2 sit one cell right of row 1's
  -- and its top differs by one cell between panes. Input order is scrambled.
  local infos = {
    rect(83, 26), -- row 2, col 3
    rect(41, 0),  -- row 1, col 2
    rect(0, 27),  -- row 2, col 1
    rect(82, 0),  -- row 1, col 3
    rect(42, 26), -- row 2, col 2
    rect(0, 0),   -- row 1, col 1
  }
  T.eq(numbers(infos), { 6, 2, 4, 3, 5, 1 })
end)

T.test('a pane one cell lower in the same row is still numbered by its column', function()
  -- Sorting by raw top would put the right pane (top 0) first.
  T.eq(numbers { rect(0, 1), rect(50, 0) }, { 1, 2 })
end)

T.test('2-cell offsets are the same row, 3 cells start a new one', function()
  T.eq(numbers { rect(0, 2), rect(50, 0) }, { 1, 2 }, 'top 2 vs 0: one row')
  T.eq(numbers { rect(0, 3), rect(50, 0) }, { 2, 1 }, 'top 3 vs 0: two rows')
end)

T.test('2-cell column jitter does not reorder panes of one column across rows', function()
  -- A single column whose panes' lefts differ by up to 2 cells: top to bottom.
  T.eq(numbers { rect(2, 30), rect(0, 0), rect(1, 15) }, { 3, 1, 2 })
end)

T.test('main pane plus a stacked side column reads main, side top, side bottom', function()
  local infos = {
    rect(81, 26, 40, 24), -- side bottom
    rect(0, 0, 80, 50),   -- main, full height
    rect(81, 0, 40, 25),  -- side top
  }
  T.eq(numbers(infos), { 3, 1, 2 })
end)

T.test('3x3 grid in any input order numbers 1..9 row by row', function()
  local cells = {}
  for r = 0, 2 do
    for c = 0, 2 do
      -- Each row split on its own: lefts jitter by r cells, tops by c cells (within tol).
      cells[#cells + 1] = { n = r * 3 + c + 1, info = rect(c * 41 + (r % 2), r * 17 + (c % 2)) }
    end
  end
  local order = { 5, 9, 1, 7, 3, 8, 2, 6, 4 }
  local infos, want = {}, {}
  for i, k in ipairs(order) do
    infos[i] = cells[k].info
    want[i] = cells[k].n
  end
  T.eq(numbers(infos), want)
end)

T.test('identical rectangles keep input order and numbers stay a permutation', function()
  T.eq(numbers { rect(0, 0), rect(0, 0), rect(0, 0) }, { 1, 2, 3 })
  -- Same clusters on both axes: the raw top, then the raw left decide.
  T.eq(numbers { rect(1, 1), rect(2, 0), rect(0, 1) }, { 3, 1, 2 })
end)

T.test('no panes gives no numbers', function()
  T.eq(require('panemap').numbering({}), {})
end)
