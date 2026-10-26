-- config/picker.lua: the request/answer protocol of the Hivegrid picker overlay and the
-- InputSelector fallback of stock WezTerm.
local T = require 'tests.lua.t'

local function setup(wz)
  local p = wz.fake.pane { id = 5 }
  local win = wz.fake.grid { { pane = p } }
  return require 'picker', win, p
end

local ROWS = {
  { id = 'a', key = '1', glyph = '?', color = 'violet', cols = { { text = 'alpha' }, { segs = { { text = 'x', color = 'red' } } } } },
  { id = 'b', key = '2', cols = { { text = 'beta' } }, meta = 'here' },
}

T.test('the request goes to the overlay with the palette; the answer reaches the callback with the origin pane', function(wz)
  local picker, win, p = setup(wz)
  local got = {}
  picker.open(win, p, { title = 'T', rows = ROWS }, function(w, pane, id) got[#got + 1] = { w, pane, id } end)
  local pick = wz.stub.picker(win)
  T.eq(pick.args.args[2], 'picker')
  T.eq(pick.spec.title, 'T')
  T.eq(#pick.spec.rows, 2)
  T.ok(pick.spec.palette.violet:match('^#%x+$'), 'the palette travels with the request')
  pick.answer('b')
  T.eq(#got, 1)
  T.eq({ got[1][1], got[1][2], got[1][3] }, { win, p, 'b' })
  pick.answer('b')
  T.eq(#got, 1, 'a request is answered once')
end)

T.test('closing without a choice and stray answers call nothing', function(wz)
  local picker, win, p = setup(wz)
  local calls = 0
  picker.open(win, p, { rows = ROWS }, function() calls = calls + 1 end)
  local pick = wz.stub.picker(win)
  pick.answer(nil)
  wz.emit('user-var-changed', win, p, 'hivegrid_pick', '{"request":"nope","id":"a"}')
  wz.emit('user-var-changed', win, p, 'hivegrid_pick', 'not json')
  T.eq(calls, 0)
end)

T.test('the callback waits until the overlay no longer covers the tab', function(wz)
  local picker, win, p = setup(wz)
  local calls = 0
  picker.open(win, p, { rows = ROWS }, function() calls = calls + 1 end)
  local pick = wz.stub.picker(win)
  local overlay = wz.fake.pane { id = 77 }
  win.active_pane_override = overlay
  wz.emit('user-var-changed', win, overlay, 'hivegrid_pick', wz.json_encode { request = pick.spec.request, id = 'a' })
  T.eq(calls, 0, 'not while the overlay is up')
  wz.stub.run_timers(3)
  T.eq(calls, 0)
  win.active_pane_override = nil
  wz.stub.run_timers()
  T.eq(calls, 1)
end)

T.test('stock WezTerm: an InputSelector with the same rows; a confirm lists No first', function(wz)
  local picker, win, p = setup(wz)
  picker.has_overlay = false
  local got
  picker.open(win, p, { title = 'T', rows = ROWS }, function(_, pane, id) got = { pane, id } end)
  local sel = win.actions[#win.actions].action
  T.eq(sel.kind, 'InputSelector')
  T.eq({ sel.args.choices[1].id, sel.args.choices[2].id }, { 'a', 'b' })
  T.ok(sel.args.choices[1].label:find('alpha', 1, true) and sel.args.choices[1].label:find('x', 1, true))
  sel.args.action.fn(win, wz.fake.pane {}, 'a')
  T.eq(got, { p, 'a' }, 'the callback gets the pane it was opened from')
  picker.open(win, p, { confirm = { text = 'Sure?', yes = 'Do it', no = 'Keep' } }, function() end)
  sel = win.actions[#win.actions].action
  T.eq({ sel.args.choices[1].id, sel.args.choices[2].id }, { 'no', 'yes' })
  T.eq(sel.args.description, 'Sure?')
end)
