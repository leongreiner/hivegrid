#!/usr/bin/env python3
"""Synthetic X input for an Xvfb display through XTest (ctypes, no deps).

Usage (DISPLAY must be set):
  xin.py move X Y
  xin.py click X Y [BUTTON] [COUNT]      BUTTON 1 left, 2 middle, 3 right; COUNT 2 = double click
  xin.py down X Y [BUTTON] | up X Y [BUTTON]
  xin.py drag X1 Y1 X2 Y2 [BUTTON]
  xin.py scroll X Y up|down [N]
  xin.py key CHORD [CHORD...]            e.g. ctrl+space  ctrl+shift+e  alt+n  Escape  Return  F1
  xin.py type TEXT                       printable ASCII typed key by key
  xin.py resize WINID W H                (WINID hex or decimal) move to 0,0 and resize
  xin.py pos                             print pointer position
"""
import ctypes as C
import sys
import time

X = C.cdll.LoadLibrary('libX11.so.6')
T = C.cdll.LoadLibrary('libXtst.so.6')
X.XOpenDisplay.restype = C.c_void_p
X.XOpenDisplay.argtypes = [C.c_char_p]
X.XFlush.argtypes = [C.c_void_p]
X.XSync.argtypes = [C.c_void_p, C.c_int]
X.XStringToKeysym.restype = C.c_ulong
X.XStringToKeysym.argtypes = [C.c_char_p]
X.XKeysymToKeycode.restype = C.c_ubyte
X.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
X.XkbKeycodeToKeysym.restype = C.c_ulong
X.XkbKeycodeToKeysym.argtypes = [C.c_void_p, C.c_ubyte, C.c_int, C.c_int]
X.XMoveResizeWindow.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint]
X.XDefaultRootWindow.restype = C.c_ulong
X.XDefaultRootWindow.argtypes = [C.c_void_p]
X.XQueryPointer.argtypes = [C.c_void_p, C.c_ulong] + [C.c_void_p] * 7
X.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
T.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
T.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
T.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]

d = X.XOpenDisplay(None)
if not d:
    sys.exit('cannot open display')

ALIASES = {
    'ctrl': 'Control_L', 'control': 'Control_L', 'shift': 'Shift_L', 'alt': 'Alt_L',
    'super': 'Super_L', 'enter': 'Return', 'esc': 'Escape', 'space': 'space', 'tab': 'Tab',
    'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right', 'bs': 'BackSpace',
    'backspace': 'BackSpace', 'del': 'Delete', 'pgup': 'Prior', 'pgdn': 'Next',
    '|': 'bar', '_': 'underscore', '\\': 'backslash', '-': 'minus', ',': 'comma', '.': 'period',
    '[': 'bracketleft', ']': 'bracketright', '/': 'slash', '=': 'equal', '+': 'plus',
}


def flush():
    X.XFlush(d)
    X.XSync(d, 0)


def keycode(name):
    name = ALIASES.get(name.lower(), name) if len(name) > 1 or name in ALIASES else name
    ks = X.XStringToKeysym(name.encode())
    if not ks:
        sys.exit(f'unknown key {name!r}')
    kc = X.XKeysymToKeycode(d, ks)
    if not kc:
        sys.exit(f'no keycode for {name!r}')
    return kc, ks


def key_event(kc, down):
    T.XTestFakeKeyEvent(d, kc, 1 if down else 0, 0)
    flush()


def chord(spec):
    parts = spec.split('+') if spec not in ('+',) else ['+']
    if spec.endswith('++'):
        parts = spec[:-2].split('+') + ['+']
    codes = [keycode(p)[0] for p in parts if p]
    for kc in codes:
        key_event(kc, True)
        time.sleep(0.02)
    for kc in reversed(codes):
        key_event(kc, False)
        time.sleep(0.02)


def type_text(text):
    shift = keycode('Shift_L')[0]
    for ch in text:
        if ch == '\n':
            chord('Return')
            continue
        ks = ord(ch)
        kc = X.XKeysymToKeycode(d, ks)
        if not kc:
            continue
        need_shift = X.XkbKeycodeToKeysym(d, kc, 0, 0) != ks
        if need_shift:
            key_event(shift, True)
        key_event(kc, True)
        key_event(kc, False)
        if need_shift:
            key_event(shift, False)
        time.sleep(0.01)


def move(x, y):
    T.XTestFakeMotionEvent(d, -1, int(x), int(y), 0)
    flush()


def button(b, down):
    T.XTestFakeButtonEvent(d, int(b), 1 if down else 0, 0)
    flush()


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, a = argv[0], argv[1:]
    if cmd == 'move':
        move(a[0], a[1])
    elif cmd == 'click':
        b = int(a[2]) if len(a) > 2 else 1
        n = int(a[3]) if len(a) > 3 else 1
        move(a[0], a[1])
        time.sleep(0.05)
        for _ in range(n):
            button(b, True)
            time.sleep(0.03)
            button(b, False)
            time.sleep(0.06)
    elif cmd in ('down', 'up'):
        move(a[0], a[1])
        button(int(a[2]) if len(a) > 2 else 1, cmd == 'down')
    elif cmd == 'drag':
        b = int(a[4]) if len(a) > 4 else 1
        x1, y1, x2, y2 = map(int, a[:4])
        move(x1, y1)
        time.sleep(0.05)
        button(b, True)
        steps = 12
        for i in range(1, steps + 1):
            move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
            time.sleep(0.03)
        button(b, False)
    elif cmd == 'scroll':
        move(a[0], a[1])
        b = 4 if a[2] == 'up' else 5
        for _ in range(int(a[3]) if len(a) > 3 else 3):
            button(b, True)
            button(b, False)
            time.sleep(0.03)
    elif cmd == 'key':
        for spec in a:
            chord(spec)
            time.sleep(0.08)
    elif cmd == 'type':
        type_text(' '.join(a))
    elif cmd == 'resize':
        X.XMoveResizeWindow(d, int(a[0], 0), 0, 0, int(a[1]), int(a[2]))
        flush()
    elif cmd == 'focus':
        # No window manager on Xvfb: without this WezTerm never gets FocusIn and drops clicks.
        X.XSetInputFocus(d, int(a[0], 0), 1, 0)  # RevertToPointerRoot, CurrentTime
        flush()
    elif cmd == 'pos':
        root = X.XDefaultRootWindow(d)
        r, c = C.c_ulong(), C.c_ulong()
        rx, ry, wx, wy = C.c_int(), C.c_int(), C.c_int(), C.c_int()
        m = C.c_uint()
        X.XQueryPointer(d, root, C.byref(r), C.byref(c), C.byref(rx), C.byref(ry),
                        C.byref(wx), C.byref(wy), C.byref(m))
        print(rx.value, ry.value)
    else:
        sys.exit(f'unknown command {cmd}')


main(sys.argv[1:])
