"""Pure data, the terminal emulator and its CSS of the phone page, served at /term.js and /term.css."""

TERM_JS = r"""
(function (root) {
  "use strict";

  /* ---------- character width ---------- */

  var ZERO = [
    0x0300, 0x036F, 0x0483, 0x0489, 0x0591, 0x05BD, 0x05BF, 0x05BF, 0x05C1, 0x05C2, 0x05C4, 0x05C5,
    0x05C7, 0x05C7, 0x0610, 0x061A, 0x064B, 0x065F, 0x0670, 0x0670, 0x06D6, 0x06DC, 0x06DF, 0x06E4,
    0x06E7, 0x06E8, 0x06EA, 0x06ED, 0x0711, 0x0711, 0x0730, 0x074A, 0x07A6, 0x07B0, 0x07EB, 0x07F3,
    0x0816, 0x0819, 0x081B, 0x0823, 0x0825, 0x0827, 0x0829, 0x082D, 0x0859, 0x085B, 0x08D3, 0x0902,
    0x093A, 0x093A, 0x093C, 0x093C, 0x0941, 0x0948, 0x094D, 0x094D, 0x0951, 0x0957, 0x0962, 0x0963,
    0x0981, 0x0981, 0x09BC, 0x09BC, 0x09C1, 0x09C4, 0x09CD, 0x09CD, 0x09E2, 0x09E3, 0x0A01, 0x0A02,
    0x0A3C, 0x0A3C, 0x0A41, 0x0A42, 0x0A47, 0x0A48, 0x0A4B, 0x0A4D, 0x0A70, 0x0A71, 0x0A81, 0x0A82,
    0x0ABC, 0x0ABC, 0x0AC1, 0x0AC5, 0x0AC7, 0x0AC8, 0x0ACD, 0x0ACD, 0x0AE2, 0x0AE3, 0x0B01, 0x0B01,
    0x0B3C, 0x0B3C, 0x0B3F, 0x0B3F, 0x0B41, 0x0B44, 0x0B4D, 0x0B4D, 0x0B56, 0x0B56, 0x0B82, 0x0B82,
    0x0BC0, 0x0BC0, 0x0BCD, 0x0BCD, 0x0C3E, 0x0C40, 0x0C46, 0x0C48, 0x0C4A, 0x0C4D, 0x0C55, 0x0C56,
    0x0CBC, 0x0CBC, 0x0CBF, 0x0CBF, 0x0CC6, 0x0CC6, 0x0CCC, 0x0CCD, 0x0CE2, 0x0CE3, 0x0D41, 0x0D44,
    0x0D4D, 0x0D4D, 0x0DCA, 0x0DCA, 0x0DD2, 0x0DD4, 0x0DD6, 0x0DD6, 0x0E31, 0x0E31, 0x0E34, 0x0E3A,
    0x0E47, 0x0E4E, 0x0EB1, 0x0EB1, 0x0EB4, 0x0EBC, 0x0EC8, 0x0ECD, 0x0F18, 0x0F19, 0x0F35, 0x0F35,
    0x0F37, 0x0F37, 0x0F39, 0x0F39, 0x0F71, 0x0F7E, 0x0F80, 0x0F84, 0x0F86, 0x0F87, 0x0F90, 0x0FBC,
    0x0FC6, 0x0FC6, 0x102D, 0x1030, 0x1032, 0x1037, 0x1039, 0x103A, 0x1058, 0x1059, 0x1160, 0x11FF,
    0x135D, 0x135F, 0x1712, 0x1714, 0x1732, 0x1734, 0x1752, 0x1753, 0x1772, 0x1773, 0x17B4, 0x17B5,
    0x17B7, 0x17BD, 0x17C6, 0x17C6, 0x17C9, 0x17D3, 0x17DD, 0x17DD, 0x180B, 0x180F, 0x18A9, 0x18A9,
    0x1920, 0x1922, 0x1927, 0x1928, 0x1932, 0x1932, 0x1939, 0x193B, 0x1A17, 0x1A18, 0x1AB0, 0x1AFF,
    0x1B00, 0x1B03, 0x1B34, 0x1B34, 0x1B36, 0x1B3A, 0x1B3C, 0x1B3C, 0x1B42, 0x1B42, 0x1B6B, 0x1B73,
    0x1DC0, 0x1DFF, 0x200B, 0x200F, 0x202A, 0x202E, 0x2060, 0x2064, 0x2066, 0x206F, 0x20D0, 0x20FF,
    0x2CEF, 0x2CF1, 0x2DE0, 0x2DFF, 0x302A, 0x302F, 0x3099, 0x309A, 0xA66F, 0xA672, 0xA674, 0xA67D,
    0xA69E, 0xA69F, 0xA6F0, 0xA6F1, 0xA802, 0xA802, 0xA806, 0xA806, 0xA80B, 0xA80B, 0xA825, 0xA826,
    0xFB1E, 0xFB1E, 0xFE00, 0xFE0F, 0xFE20, 0xFE2F, 0xFEFF, 0xFEFF, 0xFFF9, 0xFFFB, 0x1D167, 0x1D169,
    0x1D173, 0x1D182, 0x1D185, 0x1D18B, 0x1D1AA, 0x1D1AD, 0x1F3FB, 0x1F3FF, 0xE0001, 0xE0001,
    0xE0020, 0xE007F, 0xE0100, 0xE01EF
  ];

  var WIDE = [
    0x1100, 0x115F, 0x231A, 0x231B, 0x2329, 0x232A, 0x23E9, 0x23EC, 0x23F0, 0x23F0, 0x23F3, 0x23F3,
    0x25FD, 0x25FE, 0x2614, 0x2615, 0x2648, 0x2653, 0x267F, 0x267F, 0x2693, 0x2693, 0x26A1, 0x26A1,
    0x26AA, 0x26AB, 0x26BD, 0x26BE, 0x26C4, 0x26C5, 0x26CE, 0x26CE, 0x26D4, 0x26D4, 0x26EA, 0x26EA,
    0x26F2, 0x26F3, 0x26F5, 0x26F5, 0x26FA, 0x26FA, 0x26FD, 0x26FD, 0x2705, 0x2705, 0x270A, 0x270B,
    0x2728, 0x2728, 0x274C, 0x274C, 0x274E, 0x274E, 0x2753, 0x2755, 0x2757, 0x2757, 0x2795, 0x2797,
    0x27B0, 0x27B0, 0x27BF, 0x27BF, 0x2B1B, 0x2B1C, 0x2B50, 0x2B50, 0x2B55, 0x2B55, 0x2E80, 0x303E,
    0x3041, 0x33FF, 0x3400, 0x4DBF, 0x4E00, 0xA4CF, 0xA960, 0xA97F, 0xAC00, 0xD7A3, 0xF900, 0xFAFF,
    0xFE10, 0xFE19, 0xFE30, 0xFE6F, 0xFF00, 0xFF60, 0xFFE0, 0xFFE6, 0x1F004, 0x1F004, 0x1F0CF, 0x1F0CF,
    0x1F18E, 0x1F18E, 0x1F191, 0x1F19A, 0x1F200, 0x1F202, 0x1F210, 0x1F23B, 0x1F240, 0x1F248,
    0x1F250, 0x1F251, 0x1F260, 0x1F265, 0x1F300, 0x1F320, 0x1F32D, 0x1F335, 0x1F337, 0x1F37C,
    0x1F37E, 0x1F393, 0x1F3A0, 0x1F3CA, 0x1F3CF, 0x1F3D3, 0x1F3E0, 0x1F3F0, 0x1F3F4, 0x1F3F4,
    0x1F3F8, 0x1F43E, 0x1F440, 0x1F440, 0x1F442, 0x1F4FC, 0x1F4FF, 0x1F53D, 0x1F54B, 0x1F54E,
    0x1F550, 0x1F567, 0x1F57A, 0x1F57A, 0x1F595, 0x1F596, 0x1F5A4, 0x1F5A4, 0x1F5FB, 0x1F64F,
    0x1F680, 0x1F6C5, 0x1F6CC, 0x1F6CC, 0x1F6D0, 0x1F6D2, 0x1F6D5, 0x1F6D7, 0x1F6DC, 0x1F6DF,
    0x1F6EB, 0x1F6EC, 0x1F6F4, 0x1F6FC, 0x1F7E0, 0x1F7EB, 0x1F7F0, 0x1F7F0, 0x1F90C, 0x1F93A,
    0x1F93C, 0x1F945, 0x1F947, 0x1F9FF, 0x1FA70, 0x1FAFF, 0x20000, 0x2FFFD, 0x30000, 0x3FFFD
  ];

  function inTable(t, cp) {
    var lo = 0, hi = (t.length >> 1) - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (cp < t[mid * 2]) hi = mid - 1;
      else if (cp > t[mid * 2 + 1]) lo = mid + 1;
      else return true;
    }
    return false;
  }

  function wcwidth(cp) {
    cp = +cp;
    if (!(cp >= 0x20) || (cp >= 0x7f && cp < 0xa0)) return 0;
    if (cp < 0x300) return 1;
    if (inTable(ZERO, cp)) return 0;
    if (cp < 0x1100) return 1;
    return inTable(WIDE, cp) ? 2 : 1;
  }

  /* ---------- tables ---------- */

  var GRAPHICS = {
    "`": "\u25c6", a: "\u2592", b: "\u2409", c: "\u240c", d: "\u240d", e: "\u240a", f: "\u00b0",
    g: "\u00b1", h: "\u2424", i: "\u240b", j: "\u2518", k: "\u2510", l: "\u250c", m: "\u2514",
    n: "\u253c", o: "\u23ba", p: "\u23bb", q: "\u2500", r: "\u23bc", s: "\u23bd", t: "\u251c",
    u: "\u2524", v: "\u2534", w: "\u252c", x: "\u2502", y: "\u2264", z: "\u2265", "{": "\u03c0",
    "|": "\u2260", "}": "\u00a3", "~": "\u00b7", _: " "
  };
  var ASC = [], GFX = [];
  (function () {
    for (var i = 0; i < 128; i++) {
      var ch = String.fromCharCode(i);
      ASC[i] = ch;
      GFX[i] = GRAPHICS[ch] || ch;
    }
  })();

  var MOUSE_MODES = { 1000: 1, 1002: 2, 1003: 4, 1005: 8, 1006: 16, 1015: 32 };
  var BIDI = /[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;

  function clampInt(v, lo, hi, def) {
    v = Math.floor(+v);
    if (!(v === v)) return def;
    return v < lo ? lo : v > hi ? hi : v;
  }

  function num(s) {
    var v = -1;
    for (var i = 0; i < s.length; i++) {
      var d = s.charCodeAt(i) - 48;
      if (d >= 0 && d <= 9) v = v < 0 ? d : Math.min(v * 10 + d, 65535);
    }
    return v;
  }

  function pal(n) { return n >= 0 && n <= 255 ? n : null; }
  function rgb(r, g, b) {
    if (r === undefined || g === undefined || b === undefined) return null;
    return 0x1000000 | (Math.min(Math.max(r, 0), 255) << 16) | (Math.min(Math.max(g, 0), 255) << 8) | Math.min(Math.max(b, 0), 255);
  }

  function rowText(row) {
    var c = row.c, out = "";
    for (var i = 0; i < c.length; i++) out += c[i];
    return out.replace(/ +$/, "");
  }

  /* ---------- emulator ---------- */

  function Emulator(cols, rows, opt) {
    opt = opt || {};
    this.cols = clampInt(cols, 1, 1000, 80);
    this.rows = clampInt(rows, 1, 1000, 24);
    this.sbMax = opt.scrollback === undefined ? 2000 : clampInt(opt.scrollback, 0, 1000000, 2000);
    this.onReply = opt.onReply || null;
    this.onTitle = opt.onTitle || null;
    this.onBell = opt.onBell || null;
    this.quiet = false;
    this.modes = { appCursor: false, bracketedPaste: false, autowrap: true, insert: false, mouse: false };
    this.version = 0;
    this.epoch = 0;
    this.sbPushed = 0;
    this.styleEpoch = 0;
    this._init();
  }

  Emulator.prototype._init = function () {
    var m = this.modes;
    m.appCursor = false; m.bracketedPaste = false; m.autowrap = true; m.insert = false; m.mouse = false;
    this.mm = 0;
    this.styles = [{ fg: -1, bg: -1, fl: 0 }];
    this.styleMap = Object.create(null);
    this.styleMap["-1,-1,0"] = 0;
    this.styleLimit = 4096;
    this.styleEpoch++;
    this.pf = -1; this.pb = -1; this.pl = 0; this.penId = 0; this.blankId = 0;
    this.scrollback = [];
    this.epoch++;
    this.alt = false;
    this.other = null;
    this.grid = this._blankGrid();
    this.cx = 0; this.cy = 0;
    this.wrapNext = false;
    this.top = 0; this.bot = this.rows - 1;
    this.cursorVisible = true;
    this.title = "";
    this.g = ["B", "B"]; this.gl = 0; this.gfx = false;
    this.saved = null; this.savedAlt = null;
    this.lastCh = ""; this.lastW = 1;
    this._tabs(0);
    this.pend = "";
    this.st = 0;
    this.cb = ""; this.ci = ""; this.cbad = false; this.ei = "";
    this.sb = ""; this.sn = 0; this.sesc = false;
  };

  Emulator.prototype._tabs = function (from) {
    this.tabs = this.tabs || [];
    for (var i = from; i < this.cols; i++) this.tabs[i] = i % 8 === 0;
    this.tabs.length = this.cols;
  };

  Emulator.prototype._newRow = function (id) {
    var n = this.cols, c = new Array(n), s = new Array(n);
    for (var i = 0; i < n; i++) { c[i] = " "; s[i] = id; }
    return { c: c, s: s, v: this.version };
  };

  Emulator.prototype._blankGrid = function () {
    var g = [];
    for (var y = 0; y < this.rows; y++) g.push(this._newRow(0));
    return g;
  };

  Emulator.prototype._intern = function (fg, bg, fl) {
    var key = fg + "," + bg + "," + fl;
    var id = this.styleMap[key];
    if (id === undefined) {
      id = this.styles.length;
      this.styles.push({ fg: fg, bg: bg, fl: fl });
      this.styleMap[key] = id;
    }
    return id;
  };

  Emulator.prototype._applyPen = function () {
    if (this.styles.length > this.styleLimit) this._rebuildStyles();
    this.penId = this._intern(this.pf, this.pb, this.pl);
    this.blankId = this.pb === -1 ? 0 : this._intern(-1, this.pb, 0);
  };

  Emulator.prototype._rebuildStyles = function () {
    var old = this.styles, remap = [];
    this.styles = [{ fg: -1, bg: -1, fl: 0 }];
    this.styleMap = Object.create(null);
    this.styleMap["-1,-1,0"] = 0;
    this.styleEpoch++;
    var self = this, v = this.version;
    function fix(rows) {
      for (var y = 0; y < rows.length; y++) {
        var s = rows[y].s;
        for (var x = 0; x < s.length; x++) {
          var o = s[x], n = remap[o];
          if (n === undefined) {
            var st = old[o];
            n = remap[o] = self._intern(st.fg, st.bg, st.fl);
          }
          s[x] = n;
        }
        rows[y].v = v;
      }
    }
    fix(this.grid);
    if (this.other) fix(this.other);
    fix(this.scrollback);
    this.styleLimit = Math.max(4096, this.styles.length * 2);
  };

  Emulator.prototype._reply = function (s) {
    if (this.quiet || !this.onReply) return;
    try { this.onReply(s); } catch (e) { /* the page's callback must not break parsing */ }
  };

  Emulator.prototype._pushSb = function (row) {
    var sb = this.scrollback;
    sb.push(row);
    this.sbPushed++;
    while (sb.length > this.sbMax) sb.shift();
  };

  /* --- scrolling and erasing --- */

  Emulator.prototype._scrollUp = function (n, toSb) {
    var top = this.top, bot = this.bot, g = this.grid, h = bot - top + 1;
    if (n > h) n = h;
    for (var k = 0; k < n; k++) {
      var gone = g.splice(top, 1)[0];
      if (toSb && top === 0 && !this.alt && this.sbMax > 0) this._pushSb(gone);
      g.splice(bot, 0, this._newRow(this.blankId));
    }
  };

  Emulator.prototype._scrollDown = function (n) {
    var top = this.top, bot = this.bot, g = this.grid, h = bot - top + 1;
    if (n > h) n = h;
    for (var k = 0; k < n; k++) {
      g.splice(bot, 1);
      g.splice(top, 0, this._newRow(this.blankId));
    }
  };

  Emulator.prototype._index = function () {
    if (this.cy === this.bot) this._scrollUp(1, true);
    else if (this.cy < this.rows - 1) this.cy++;
  };

  Emulator.prototype._reverseIndex = function () {
    if (this.cy === this.top) this._scrollDown(1);
    else if (this.cy > 0) this.cy--;
  };

  Emulator.prototype._erase = function (row, a, b) {
    var c = row.c, s = row.s, id = this.blankId;
    if (a < 0) a = 0;
    if (b > c.length) b = c.length;
    if (a >= b) return;
    if (c[a] === "" && a > 0) c[a - 1] = " ";
    if (b < c.length && c[b] === "") c[b] = " ";
    for (var i = a; i < b; i++) { c[i] = " "; s[i] = id; }
    row.v = this.version;
  };

  Emulator.prototype._fixTail = function (row) {
    var c = row.c, l = c[c.length - 1];
    if (l && l !== " " && wcwidth(l.codePointAt(0)) === 2) c[c.length - 1] = " ";
  };

  Emulator.prototype._insertCells = function (row, x, n) {
    var c = row.c, s = row.s, cols = c.length, j;
    if (n > cols - x) n = cols - x;
    if (n <= 0) return;
    if (c[x] === "" && x > 0) c[x - 1] = " ";
    for (j = cols - 1; j >= x + n; j--) { c[j] = c[j - n]; s[j] = s[j - n]; }
    for (j = x; j < x + n; j++) { c[j] = " "; s[j] = this.blankId; }
    if (x + n < cols && c[x + n] === "") c[x + n] = " ";
    this._fixTail(row);
    row.v = this.version;
  };

  Emulator.prototype._deleteCells = function (row, x, n) {
    var c = row.c, s = row.s, cols = c.length, j;
    if (n > cols - x) n = cols - x;
    if (n <= 0) return;
    if (c[x] === "" && x > 0) c[x - 1] = " ";
    for (j = x; j < cols - n; j++) { c[j] = c[j + n]; s[j] = s[j + n]; }
    for (j = cols - n; j < cols; j++) { c[j] = " "; s[j] = this.blankId; }
    if (c[x] === "") c[x] = " ";
    row.v = this.version;
  };

  /* --- printing --- */

  Emulator.prototype._combine = function (ch) {
    var row = this.grid[this.cy], x = this.wrapNext ? this.cx : this.cx - 1;
    if (x >= 0 && row.c[x] === "") x--;
    if (x < 0) return;
    row.c[x] += ch;
    row.v = this.version;
  };

  Emulator.prototype._print = function (ch, w) {
    var cols = this.cols;
    if (w === 0) { this._combine(ch); return; }
    if (this.wrapNext) { this.wrapNext = false; this.cx = 0; this._index(); }
    if (w === 2) {
      if (cols < 2) w = 1;
      else if (this.cx >= cols - 1) {
        if (!this.modes.autowrap) return;
        this._erase(this.grid[this.cy], this.cx, cols);
        this.cx = 0;
        this._index();
      }
    }
    var row = this.grid[this.cy], c = row.c, s = row.s, x = this.cx, id = this.penId;
    if (this.modes.insert) this._insertCells(row, x, w);
    if (c[x] === "") { if (x > 0) c[x - 1] = " "; }
    else if (c[x + 1] === "") c[x + 1] = " ";
    if (w === 2 && c[x + 1] !== "" && c[x + 2] === "") c[x + 2] = " ";
    c[x] = ch; s[x] = id;
    if (w === 2) { c[x + 1] = ""; s[x + 1] = id; }
    row.v = this.version;
    this.lastCh = ch; this.lastW = w;
    x += w;
    if (x >= cols) { this.cx = cols - 1; this.wrapNext = this.modes.autowrap; }
    else this.cx = x;
  };

  /* --- control and escape handling --- */

  Emulator.prototype._tab = function (dir) {
    var x = this.cx, t = this.tabs;
    if (dir > 0) {
      x++;
      while (x < this.cols - 1 && !t[x]) x++;
      this.cx = Math.min(x, this.cols - 1);
    } else {
      x--;
      while (x > 0 && !t[x]) x--;
      this.cx = Math.max(x, 0);
    }
    this.wrapNext = false;
  };

  Emulator.prototype._ctl = function (code) {
    switch (code) {
      case 0x07:
        if (this.onBell) { try { this.onBell(); } catch (e) { /* ignore */ } }
        break;
      case 0x08: if (this.cx > 0) this.cx--; this.wrapNext = false; break;
      case 0x09: this._tab(1); break;
      case 0x0a: case 0x0b: case 0x0c: this.wrapNext = false; this._index(); break;
      case 0x0d: this.cx = 0; this.wrapNext = false; break;
      case 0x0e: this.gl = 1; this.gfx = this.g[1] === "0"; break;
      case 0x0f: this.gl = 0; this.gfx = this.g[0] === "0"; break;
    }
  };

  Emulator.prototype._snap = function () {
    return {
      cx: this.cx, cy: this.cy, pf: this.pf, pb: this.pb, pl: this.pl,
      g0: this.g[0], g1: this.g[1], gl: this.gl, wrap: this.wrapNext
    };
  };

  Emulator.prototype._restore = function (sn) {
    if (!sn) {
      this.cx = 0; this.cy = 0; this.wrapNext = false;
      this.pf = -1; this.pb = -1; this.pl = 0;
      this.g[0] = "B"; this.g[1] = "B"; this.gl = 0;
    } else {
      this.cx = Math.min(sn.cx, this.cols - 1);
      this.cy = Math.min(sn.cy, this.rows - 1);
      this.pf = sn.pf; this.pb = sn.pb; this.pl = sn.pl;
      this.g[0] = sn.g0; this.g[1] = sn.g1; this.gl = sn.gl;
      this.wrapNext = sn.wrap && this.cx === this.cols - 1;
    }
    this.gfx = this.g[this.gl] === "0";
    this._applyPen();
  };

  Emulator.prototype._escFinal = function (code) {
    switch (code) {
      case 0x37: this.saved = this._snap(); break;                       // 7
      case 0x38: this._restore(this.saved); break;                       // 8
      case 0x44: this.wrapNext = false; this._index(); break;            // D
      case 0x45: this.cx = 0; this.wrapNext = false; this._index(); break; // E
      case 0x4d: this.wrapNext = false; this._reverseIndex(); break;     // M
      case 0x48: this.tabs[this.cx] = true; break;                       // H
      case 0x63: this.reset(); break;                                    // c
    }
  };

  Emulator.prototype._escInter = function (inter, code) {
    if (inter === "(") { this.g[0] = code === 0x30 ? "0" : "B"; if (this.gl === 0) this.gfx = this.g[0] === "0"; }
    else if (inter === ")") { this.g[1] = code === 0x30 ? "0" : "B"; if (this.gl === 1) this.gfx = this.g[1] === "0"; }
  };

  Emulator.prototype._osc = function () {
    var s = this.sb, semi = s.indexOf(";");
    this.sb = "";
    if (semi < 0) return;
    var n = s.slice(0, semi);
    if (n !== "0" && n !== "1" && n !== "2") return;
    var t = s.slice(semi + 1).replace(BIDI, "");
    var cps = 0, i = 0;
    while (i < t.length && cps < 120) {
      var cc = t.charCodeAt(i);
      i += cc >= 0xd800 && cc <= 0xdbff && i + 1 < t.length ? 2 : 1;
      cps++;
    }
    this.title = t.slice(0, i);
    if (this.onTitle) { try { this.onTitle(this.title); } catch (e) { /* ignore */ } }
  };

  Emulator.prototype._setAlt = function (on) {
    if (on === this.alt) return;
    if (on) {
      this.savedAlt = this._snap();
      this.other = this.grid;
      this.grid = this._blankGrid();
      this.alt = true;
    } else {
      this.grid = this.other;
      this.other = null;
      this.alt = false;
      this._restore(this.savedAlt);
      this.savedAlt = null;
    }
    this.top = 0; this.bot = this.rows - 1;
    this.wrapNext = false;
  };

  Emulator.prototype._decMode = function (P, set) {
    for (var i = 0; i < P.length; i++) {
      var v = P[i], m = this.modes;
      if (v === 1) m.appCursor = set;
      else if (v === 7) { m.autowrap = set; if (!set) this.wrapNext = false; }
      else if (v === 25) this.cursorVisible = set;
      else if (v === 47 || v === 1047 || v === 1049) this._setAlt(set);
      else if (v === 2004) m.bracketedPaste = set;
      else if (MOUSE_MODES[v]) {
        if (set) this.mm |= MOUSE_MODES[v]; else this.mm &= ~MOUSE_MODES[v];
        m.mouse = this.mm !== 0;
      }
    }
  };

  Emulator.prototype._softReset = function () {
    var m = this.modes;
    m.insert = false; m.autowrap = true; m.appCursor = false;
    this.cursorVisible = true;
    this.top = 0; this.bot = this.rows - 1;
    this.pf = -1; this.pb = -1; this.pl = 0; this._applyPen();
    this.g[0] = "B"; this.g[1] = "B"; this.gl = 0; this.gfx = false;
    this.saved = null;
    this.wrapNext = false;
  };

  Emulator.prototype._sgr = function (P, SUB) {
    if (!P.length) P = [0];
    for (var i = 0; i < P.length; i++) {
      var v = P[i] < 0 ? 0 : P[i], sb;
      switch (v) {
        case 0: this.pf = -1; this.pb = -1; this.pl = 0; break;
        case 1: this.pl |= 1; break;
        case 2: this.pl |= 2; break;
        case 3: this.pl |= 4; break;
        case 4:
          sb = SUB && SUB[i];
          if (sb && sb.length > 1 && sb[1] === 0) this.pl &= ~8; else this.pl |= 8;
          break;
        case 5: case 6: this.pl |= 16; break;
        case 7: this.pl |= 32; break;
        case 8: this.pl |= 64; break;
        case 9: this.pl |= 128; break;
        case 21: this.pl |= 8; break;
        case 22: this.pl &= ~3; break;
        case 23: this.pl &= ~4; break;
        case 24: this.pl &= ~8; break;
        case 25: this.pl &= ~16; break;
        case 27: this.pl &= ~32; break;
        case 28: this.pl &= ~64; break;
        case 29: this.pl &= ~128; break;
        case 38: case 48: case 58:
          var col = null;
          sb = SUB && SUB[i];
          if (sb) {
            if (sb[1] === 5) col = pal(sb[2]);
            else if (sb[1] === 2) col = sb.length >= 6 ? rgb(sb[3], sb[4], sb[5]) : sb.length === 5 ? rgb(sb[2], sb[3], sb[4]) : null;
          } else if (P[i + 1] === 5) { col = pal(P[i + 2]); i += 2; }
          else if (P[i + 1] === 2) { col = rgb(P[i + 2], P[i + 3], P[i + 4]); i += 4; }
          if (col !== null) { if (v === 38) this.pf = col; else if (v === 48) this.pb = col; }
          break;
        case 39: this.pf = -1; break;
        case 49: this.pb = -1; break;
        default:
          if (v >= 30 && v <= 37) this.pf = v - 30;
          else if (v >= 40 && v <= 47) this.pb = v - 40;
          else if (v >= 90 && v <= 97) this.pf = v - 90 + 8;
          else if (v >= 100 && v <= 107) this.pb = v - 100 + 8;
      }
    }
    this._applyPen();
  };

  Emulator.prototype._csi = function (fc) {
    var buf = this.cb, pre = "", inter = this.ci, f = String.fromCharCode(fc);
    var c0 = buf.charCodeAt(0);
    if (c0 >= 0x3c && c0 <= 0x3f) { pre = buf.charAt(0); buf = buf.slice(1); }
    var P = [], SUB = null;
    if (buf.length) {
      var segs = buf.split(";");
      for (var k = 0; k < segs.length; k++) {
        if (segs[k].indexOf(":") >= 0) {
          var parts = segs[k].split(":"), nums = [];
          for (var q = 0; q < parts.length; q++) nums.push(num(parts[q]));
          if (!SUB) SUB = [];
          SUB[k] = nums;
          P.push(nums[0]);
        } else P.push(num(segs[k]));
      }
    }
    var cols = this.cols, rows = this.rows;
    var n = P[0] > 0 ? P[0] : 1;
    var row, i;

    if (inter !== "") {
      if (inter === "!" && f === "p" && pre === "") this._softReset();
      return;
    }
    if (pre === "?") {
      if (f === "h" || f === "l") this._decMode(P, f === "h");
      else if (f === "J" || f === "K") this._csi2(f, P);
      else if (f === "n" && P[0] === 6) this._reply("\x1b[" + (this.cy + 1) + ";" + (this.cx + 1) + "R");
      return;
    }
    if (pre === ">") {
      if (f === "c") this._reply("\x1b[>0;0;0c");
      return;
    }
    if (pre !== "") return;

    switch (f) {
      case "A": this.cy = Math.max(this.cy >= this.top ? this.top : 0, this.cy - n); this.wrapNext = false; break;
      case "B": this.cy = Math.min(this.cy <= this.bot ? this.bot : rows - 1, this.cy + n); this.wrapNext = false; break;
      case "C": case "a": this.cx = Math.min(cols - 1, this.cx + n); this.wrapNext = false; break;
      case "D": this.cx = Math.max(0, this.cx - n); this.wrapNext = false; break;
      case "E": this.cy = Math.min(this.cy <= this.bot ? this.bot : rows - 1, this.cy + n); this.cx = 0; this.wrapNext = false; break;
      case "F": this.cy = Math.max(this.cy >= this.top ? this.top : 0, this.cy - n); this.cx = 0; this.wrapNext = false; break;
      case "G": case "`": this.cx = Math.min(cols, n) - 1; this.wrapNext = false; break;
      case "H": case "f":
        this.cy = Math.min(rows, n) - 1;
        this.cx = Math.min(cols, P[1] > 0 ? P[1] : 1) - 1;
        this.wrapNext = false;
        break;
      case "d": this.cy = Math.min(rows, n) - 1; this.wrapNext = false; break;
      case "e": this.cy = Math.min(rows - 1, this.cy + n); this.wrapNext = false; break;
      case "I": for (i = 0; i < Math.min(n, cols); i++) this._tab(1); break;
      case "Z": for (i = 0; i < Math.min(n, cols); i++) this._tab(-1); break;
      case "J": case "K": this._csi2(f, P); break;
      case "X": this._erase(this.grid[this.cy], this.cx, this.cx + n); break;
      case "@": this._insertCells(this.grid[this.cy], this.cx, n); break;
      case "P": this._deleteCells(this.grid[this.cy], this.cx, n); break;
      case "L":
        if (this.cy >= this.top && this.cy <= this.bot) {
          var t0 = this.top;
          this.top = this.cy; this._scrollDown(n); this.top = t0;
          this.cx = 0; this.wrapNext = false;
        }
        break;
      case "M":
        if (this.cy >= this.top && this.cy <= this.bot) {
          var t1 = this.top;
          this.top = this.cy; this._scrollUp(n, false); this.top = t1;
          this.cx = 0; this.wrapNext = false;
        }
        break;
      case "S": this._scrollUp(n, true); break;
      case "T": if (P.length <= 1) this._scrollDown(n); break;
      case "r":
        var tp = P[0] > 0 ? P[0] : 1, bt = P[1] > 0 ? Math.min(P[1], rows) : rows;
        if (tp < bt) { this.top = tp - 1; this.bot = bt - 1; this.cx = 0; this.cy = 0; this.wrapNext = false; }
        break;
      case "s": this.saved = this._snap(); break;
      case "u": this._restore(this.saved); break;
      case "b":
        if (this.lastCh) {
          var cnt = Math.min(n, cols * rows);
          for (i = 0; i < cnt; i++) this._print(this.lastCh, this.lastW);
        }
        break;
      case "m": this._sgr(P, SUB); break;
      case "h": case "l":
        for (i = 0; i < P.length; i++) if (P[i] === 4) this.modes.insert = f === "h";
        break;
      case "g":
        if (P[0] === 3) { for (i = 0; i < cols; i++) this.tabs[i] = false; }
        else if (!(P[0] > 0)) this.tabs[this.cx] = false;
        break;
      case "c": if (!(P[0] > 0)) this._reply("\x1b[?62;c"); break;
      case "n":
        if (P[0] === 5) this._reply("\x1b[0n");
        else if (P[0] === 6) this._reply("\x1b[" + (this.cy + 1) + ";" + (this.cx + 1) + "R");
        break;
      case "t": if (P[0] === 18) this._reply("\x1b[8;" + rows + ";" + cols + "t"); break;
    }
  };

  Emulator.prototype._csi2 = function (f, P) {
    var mode = P[0] > 0 ? P[0] : 0, g = this.grid, y, row = g[this.cy];
    if (f === "J") {
      if (mode === 0) {
        this._erase(row, this.cx, this.cols);
        for (y = this.cy + 1; y < this.rows; y++) this._erase(g[y], 0, this.cols);
      } else if (mode === 1) {
        for (y = 0; y < this.cy; y++) this._erase(g[y], 0, this.cols);
        this._erase(row, 0, this.cx + 1);
      } else if (mode === 2) {
        for (y = 0; y < this.rows; y++) this._erase(g[y], 0, this.cols);
      } else if (mode === 3) {
        this.scrollback = [];
        this.epoch++;
      }
    } else {
      if (mode === 0) this._erase(row, this.cx, this.cols);
      else if (mode === 1) this._erase(row, 0, this.cx + 1);
      else if (mode === 2) this._erase(row, 0, this.cols);
    }
  };

  Emulator.prototype.write = function (str) {
    if (typeof str !== "string") str = str == null ? "" : String(str);
    this.version++;
    if (this.pend) { str = this.pend + str; this.pend = ""; }
    var n = str.length, i = 0;
    while (i < n) {
      var code = str.charCodeAt(i), st = this.st;
      if (st === 0) {
        if (code >= 0x20) {
          if (code < 0x7f) {
            this._print(this.gfx && code >= 0x5f ? GFX[code] : ASC[code], 1);
            i++;
            continue;
          }
          if (code < 0xa0) { i++; continue; }
          var ch, cp = code;
          if (code >= 0xd800 && code <= 0xdbff) {
            var lo = i + 1 < n ? str.charCodeAt(i + 1) : 0;
            if (lo >= 0xdc00 && lo <= 0xdfff) {
              cp = ((code - 0xd800) << 10) + (lo - 0xdc00) + 0x10000;
              ch = str.substr(i, 2);
              i += 2;
            } else if (i + 1 >= n) {
              this.pend = str.charAt(i);
              break;
            } else { cp = 0xfffd; ch = "\ufffd"; i++; }
          } else if (code >= 0xdc00 && code <= 0xdfff) { cp = 0xfffd; ch = "\ufffd"; i++; }
          else { ch = str.charAt(i); i++; }
          this._print(ch, wcwidth(cp));
          continue;
        }
        if (code === 0x1b) this.st = 1; else this._ctl(code);
        i++;
        continue;
      }
      if (code === 0x18 || code === 0x1a) { this.st = 0; i++; continue; }
      switch (st) {
        case 1:
          i++;
          if (code === 0x5b) { this.st = 3; this.cb = ""; this.ci = ""; this.cbad = false; }
          else if (code === 0x5d) { this.st = 4; this.sb = ""; this.sn = 0; this.sesc = false; }
          else if (code === 0x50 || code === 0x58 || code === 0x5e || code === 0x5f) { this.st = 5; this.sn = 0; this.sesc = false; }
          else if (code >= 0x20 && code <= 0x2f) { this.st = 2; this.ei = String.fromCharCode(code); }
          else if (code === 0x1b) { /* stay */ }
          else if (code < 0x20) this._ctl(code);
          else { this.st = 0; if (code < 0x7f) this._escFinal(code); }
          break;
        case 2:
          i++;
          if (code >= 0x20 && code <= 0x2f) { if (this.ei.length < 3) this.ei += String.fromCharCode(code); }
          else if (code >= 0x30 && code <= 0x7e) { this.st = 0; this._escInter(this.ei, code); }
          else if (code === 0x1b) this.st = 1;
          else if (code < 0x20) this._ctl(code);
          else if (code !== 0x7f) this.st = 0;
          break;
        case 3:
          i++;
          if (code >= 0x30 && code <= 0x3f) {
            if (this.ci !== "" || this.cb.length >= 256) this.cbad = true;
            else this.cb += String.fromCharCode(code);
          } else if (code >= 0x20 && code <= 0x2f) {
            if (this.ci.length < 3) this.ci += String.fromCharCode(code); else this.cbad = true;
          } else if (code >= 0x40 && code <= 0x7e) { this.st = 0; if (!this.cbad) this._csi(code); }
          else if (code === 0x1b) this.st = 1;
          else if (code < 0x20) this._ctl(code);
          else if (code !== 0x7f) this.st = 0;
          break;
        default: /* 4 = OSC, 5 = DCS/APC/PM/SOS */
          if (this.sesc) {
            this.sesc = false;
            if (code === 0x5c) { i++; this.st = 0; if (st === 4) this._osc(); }
            else this.st = 1;
            break;
          }
          i++;
          if (code === 0x1b) this.sesc = true;
          else if (code === 0x07 && st === 4) { this.st = 0; this._osc(); }
          else if (code >= 0x20) {
            if (++this.sn > 4096) { this.st = 0; this.sb = ""; }
            else if (st === 4) this.sb += str.charAt(i - 1);
          }
      }
    }
  };

  /* --- public state operations --- */

  Emulator.prototype.reset = function () {
    this.version++;
    this._init();
  };

  Emulator.prototype.resize = function (cols, rows) {
    cols = clampInt(cols, 1, 1000, this.cols);
    rows = clampInt(rows, 1, 1000, this.rows);
    if (cols === this.cols && rows === this.rows) return;
    this.version++;
    var oc = this.cols, or = this.rows, y, k;
    var mainG = this.alt ? this.other : this.grid, altG = this.alt ? this.grid : null;
    if (rows < or) {
      if (!this.alt) {
        var need = Math.max(0, this.cy - (rows - 1));
        for (k = 0; k < need; k++) {
          var gone = mainG.shift();
          if (this.sbMax > 0) this._pushSb(gone);
        }
        this.cy -= need;
      }
      mainG.length = rows;
      if (altG) altG.length = rows;
    } else if (rows > or) {
      for (y = or; y < rows; y++) {
        mainG.push(this._newRow(0));
        if (altG) altG.push(this._newRow(0));
      }
    }
    var v = this.version;
    function cut(g) {
      for (var j = 0; j < g.length; j++) {
        var r = g[j], c = r.c, s = r.s;
        if (cols < c.length) {
          if (c[cols] === "") c[cols - 1] = " ";
          c.length = cols; s.length = cols;
        } else {
          while (c.length < cols) { c.push(" "); s.push(0); }
        }
        r.v = v;
      }
    }
    if (cols !== oc) {
      cut(mainG);
      if (altG) cut(altG);
    }
    this.cols = cols;
    this.rows = rows;
    this._tabs(oc);
    this.top = 0; this.bot = rows - 1;
    this.cx = Math.min(this.cx, cols - 1);
    this.cy = Math.min(this.cy, rows - 1);
    this.wrapNext = false;
  };

  /* --- inspection --- */

  Emulator.prototype.lineText = function (y) {
    var row = this.grid[y];
    return row ? rowText(row) : "";
  };

  Emulator.prototype.screenText = function () {
    var out = [];
    for (var y = 0; y < this.rows; y++) out.push(rowText(this.grid[y]));
    return out;
  };

  Emulator.prototype.scrollbackText = function () {
    var out = [];
    for (var i = 0; i < this.scrollback.length; i++) out.push(rowText(this.scrollback[i]));
    return out;
  };

  Emulator.prototype.cellStyle = function (x, y) {
    var row = this.grid[y], id = row && row.s[x], st = id === undefined ? null : this.styles[id];
    return st ? { fg: st.fg, bg: st.bg, fl: st.fl } : { fg: -1, bg: -1, fl: 0 };
  };

  /* ---------- UTF-8 stream ---------- */

  function Utf8Stream() { this.reset(); }
  Utf8Stream.prototype.reset = function () {
    this.dec = new TextDecoder("utf-8", { ignoreBOM: true });
  };
  Utf8Stream.prototype.decodeBytes = function (u8) {
    return this.dec.decode(u8, { stream: true });
  };
  Utf8Stream.prototype.decode = function (b64) {
    var bin;
    try { bin = atob(b64); } catch (e) { return ""; }
    var n = bin.length, u8 = new Uint8Array(n);
    for (var i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
    return this.decodeBytes(u8);
  };

  /* ---------- keyboard helpers ---------- */

  var NAV = { ArrowUp: "A", ArrowDown: "B", ArrowRight: "C", ArrowLeft: "D", Home: "H", End: "F" };
  var TILDE = { Insert: 2, Delete: 3, PageUp: 5, PageDown: 6, F5: 15, F6: 17, F7: 18, F8: 19, F9: 20, F10: 21, F11: 23, F12: 24 };
  var FKEY = { F1: "P", F2: "Q", F3: "R", F4: "S" };
  var CTRL_PUNCT = { "@": 0, " ": 0, "[": 27, "\\": 28, "]": 29, "^": 30, "6": 30, "_": 31, "/": 31, "?": 127 };

  function ctrlChar(ch) {
    if (typeof ch !== "string" || ch.length !== 1) return null;
    var c = ch.charCodeAt(0);
    if (c >= 0x61 && c <= 0x7a) return String.fromCharCode(c - 0x60);
    if (c >= 0x41 && c <= 0x5a) return String.fromCharCode(c - 0x40);
    var p = CTRL_PUNCT[ch];
    return p === undefined ? null : String.fromCharCode(p);
  }

  function isSingle(k) {
    if (typeof k !== "string") return false;
    if (k.length === 1) return true;
    if (k.length === 2) { var c = k.charCodeAt(0); return c >= 0xd800 && c <= 0xdbff; }
    return false;
  }

  function keyBytes(ev, modes) {
    if (!ev || typeof ev.key !== "string") return null;
    var key = ev.key, ctrl = !!ev.ctrlKey, alt = !!ev.altKey, shift = !!ev.shiftKey;
    if (ev.metaKey) return null;
    if (ctrl && shift && key.length === 1 && "CVXAFRIJKTNW".indexOf(key.toUpperCase()) >= 0) return null;
    if (typeof ev.getModifierState === "function") {
      try { if (ev.getModifierState("AltGraph")) return null; } catch (e) { /* ignore */ }
    }
    var m = 1 + (shift ? 1 : 0) + (alt ? 2 : 0) + (ctrl ? 4 : 0);
    var app = !!(modes && modes.appCursor);
    var k = NAV[key];
    if (k) return m > 1 ? "\x1b[1;" + m + k : (app ? "\x1bO" : "\x1b[") + k;
    k = TILDE[key];
    if (k) return m > 1 ? "\x1b[" + k + ";" + m + "~" : "\x1b[" + k + "~";
    k = FKEY[key];
    if (k) return m > 1 ? "\x1b[1;" + m + k : "\x1bO" + k;
    var esc = alt ? "\x1b" : "";
    switch (key) {
      case "Enter": return esc + "\r";
      case "Backspace": return esc + (ctrl ? "\x08" : "\x7f");
      case "Tab": return shift ? "\x1b[Z" : esc + "\t";
      case "Escape": return esc + "\x1b";
    }
    if (!isSingle(key)) return null;
    if (ctrl) {
      var cc = ctrlChar(key);
      return cc === null ? null : esc + cc;
    }
    return alt ? "\x1b" + key : null;
  }

  function pasteText(text, bracketed) {
    var t = String(text).replace(/\r\n|\n/g, "\r").replace(/[\u0000-\u0008\u000a-\u000c\u000e-\u001f\u007f-\u009f]/g, "");
    return bracketed ? "\x1b[200~" + t + "\x1b[201~" : t;
  }

  /* ---------- DOM renderer ---------- */

  var DEF_STYLE = { fg: -1, bg: -1, fl: 0 };
  var LEVELS = [0, 95, 135, 175, 215, 255];

  function cssColor(v) {
    if (v >= 0x1000000) return "rgb(" + ((v >> 16) & 255) + "," + ((v >> 8) & 255) + "," + (v & 255) + ")";
    if (v < 232) {
      var n = v - 16;
      return "rgb(" + LEVELS[Math.floor(n / 36)] + "," + LEVELS[Math.floor(n / 6) % 6] + "," + LEVELS[n % 6] + ")";
    }
    var g = 8 + 10 * (v - 232);
    return "rgb(" + g + "," + g + "," + g + ")";
  }

  function Renderer(host, em, opt) {
    opt = opt || {};
    var self = this, doc = host.ownerDocument || document;
    this.host = host; this.em = em; this.opt = opt; this.doc = doc;
    this.fontSize = clampInt(opt.fontSize || 13, 8, 24, 13);
    this.pinned = true;
    this.destroyed = false;
    this.raf = 0;
    this.cw = 0; this.rh = 0;
    this.sc = []; this.scEpoch = -1;
    this.rowEls = []; this.lastRow = []; this.lastV = []; this.lastCur = [];
    this.hist = 0;
    this.epoch = em.epoch;
    this.seen = em.sbPushed - em.scrollback.length;

    var view = this.view = doc.createElement("div");
    view.className = "term-view";
    view.setAttribute("role", "log");
    view.setAttribute("aria-label", "Terminal output");
    view.setAttribute("aria-live", "off");
    view.setAttribute("tabindex", "-1");
    this.back = doc.createElement("div");
    this.back.className = "term-back";
    this.screen = doc.createElement("div");
    this.screen.className = "term-screen";
    view.appendChild(this.back);
    view.appendChild(this.screen);

    var probe = this.probe = doc.createElement("div");
    probe.className = "term-probe";
    probe.setAttribute("aria-hidden", "true");
    var span = doc.createElement("span");
    span.appendChild(doc.createTextNode(new Array(51).join("M")));
    probe.appendChild(span);

    host.appendChild(view);
    host.appendChild(probe);
    this._applyFont();
    this.measure();

    this._onScroll = function () { self._scrolled(); };
    view.addEventListener("scroll", this._onScroll, { passive: true });
    if (typeof ResizeObserver !== "undefined") {
      this.ro = new ResizeObserver(function () {
        if (self.destroyed) return;
        if (self.pinned) self._toBottom();
        if (self.opt.onResize) self.opt.onResize();
      });
      this.ro.observe(host);
    }
    this.render();
  }

  Renderer.prototype._applyFont = function () {
    var px = this.fontSize + "px";
    this.view.style.fontSize = px;
    this.probe.style.fontSize = px;
  };

  Renderer.prototype.measure = function () {
    var w = this.probe.firstChild.getBoundingClientRect().width / 50;
    var h = this.probe.getBoundingClientRect().height;
    if (w > 0 && h > 0) { this.cw = w; this.rh = h; }
    else if (!this.cw) { this.cw = this.fontSize * 0.6; this.rh = this.fontSize * 1.25; }
    return { cw: this.cw, rh: this.rh };
  };

  Renderer.prototype.fit = function () {
    var m = this.measure(), v = this.view;
    var cols = Math.floor((v.clientWidth - 8) / m.cw + 1e-6);
    var rows = Math.floor(v.clientHeight / m.rh + 1e-6);
    return { cols: clampInt(cols, 10, 300, 80), rows: clampInt(rows, 3, 120, 24) };
  };

  Renderer.prototype.setFontSize = function (px) {
    this.fontSize = clampInt(px, 8, 24, this.fontSize);
    this._applyFont();
    this.measure();
    this.render();
    if (this.opt.onResize) this.opt.onResize();
  };

  Renderer.prototype.setFocus = function (on) {
    this.host.classList.toggle("focus", !!on);
  };

  Renderer.prototype._toBottom = function () {
    this.view.scrollTop = this.view.scrollHeight;
  };

  Renderer.prototype._setPinned = function (p) {
    if (p === this.pinned) return;
    this.pinned = p;
    if (this.opt.onPinChange) this.opt.onPinChange(p);
  };

  Renderer.prototype._scrolled = function () {
    var v = this.view, at = v.scrollHeight - v.scrollTop - v.clientHeight <= 4;
    if (at !== this.pinned) {
      this._setPinned(at);
      this.schedule();
    }
  };

  Renderer.prototype.scrollToBottom = function () {
    this._setPinned(true);
    this.render();
    this._toBottom();
  };

  Renderer.prototype.schedule = function () {
    if (this.raf || this.destroyed) return;
    var self = this;
    this.raf = requestAnimationFrame(function () {
      self.raf = 0;
      self.render();
    });
  };

  Renderer.prototype._style = function (sid) {
    var r = this.sc[sid];
    if (r) return r;
    var st = this.em.styles[sid] || DEF_STYLE, f = st.fg, b = st.bg, fl = st.fl, cls = "", fg = "", bg = "";
    if (fl & 32) {
      var t = f; f = b; b = t;
      if (f === -1) f = -2;
      if (b === -1) b = -2;
    }
    if (f === -2) cls += " tfd";
    else if (f >= 0) { if (f < 16) cls += " tf" + f; else if (!(fl & 64)) fg = cssColor(f); }
    if (b === -2) cls += " tbd";
    else if (b >= 0) { if (b < 16) cls += " tb" + b; else bg = cssColor(b); }
    if (fl & 1) cls += " tB";
    if (fl & 2) cls += " tD";
    if (fl & 4) cls += " tI";
    if (fl & 8) cls += " tU";
    if (fl & 128) cls += " tS";
    if (fl & 64) cls += " tH";
    r = { cls: cls.slice(1), fg: fg, bg: bg, plain: cls === "" && fg === "" && bg === "" };
    this.sc[sid] = r;
    return r;
  };

  Renderer.prototype._invisible = function (sid) {
    var st = this.em.styles[sid] || DEF_STYLE;
    return st.bg === -1 && (st.fl & (8 | 32 | 128)) === 0;
  };

  Renderer.prototype._emit = function (el, sid, text, extra) {
    var st = this._style(sid);
    if (st.plain && !extra) { el.appendChild(this.doc.createTextNode(text)); return; }
    var sp = this.doc.createElement("span");
    sp.className = extra ? (st.cls ? st.cls + " " + extra : extra) : st.cls;
    if (st.fg) sp.style.color = st.fg;
    if (st.bg) sp.style.backgroundColor = st.bg;
    sp.appendChild(this.doc.createTextNode(text));
    el.appendChild(sp);
  };

  Renderer.prototype._fill = function (el, row, cur) {
    var c = row.c, s = row.s, n = c.length, end = n, x = 0;
    el.textContent = "";
    while (end > 0 && c[end - 1] === " " && this._invisible(s[end - 1])) end--;
    if (cur >= n) cur = -1;
    if (cur > 0 && c[cur] === "") cur--;
    if (cur >= 0 && cur + 1 > end) end = cur + 1;
    while (x < end) {
      var sid = s[x], ch = c[x];
      if (ch === "") { x++; continue; }
      var wide = c[x + 1] === "";
      if (x === cur || wide) {
        this._emit(el, sid, ch, (x === cur ? "tc" : "") + (x === cur && wide ? " " : "") + (wide ? "tw" : ""));
        x += wide ? 2 : 1;
        continue;
      }
      var txt = ch;
      x++;
      while (x < end && s[x] === sid && x !== cur && c[x] !== "" && c[x + 1] !== "") { txt += c[x]; x++; }
      this._emit(el, sid, txt, "");
    }
  };

  Renderer.prototype._clearHist = function () {
    this.back.textContent = "";
    this.hist = 0;
  };

  Renderer.prototype.render = function () {
    if (this.destroyed) return;
    var em = this.em, doc = this.doc, y, el;
    if (em.styleEpoch !== this.scEpoch) {
      this.sc = [];
      this.scEpoch = em.styleEpoch;
      for (y = 0; y < this.lastRow.length; y++) this.lastRow[y] = null;
    }
    if (em.epoch !== this.epoch) {
      this._clearHist();
      this.epoch = em.epoch;
      this.seen = em.sbPushed - em.scrollback.length;
    }
    var sb = em.scrollback, add = em.sbPushed - this.seen;
    if (add > 0) {
      if (add > sb.length) { this._clearHist(); add = sb.length; }
      var frag = doc.createDocumentFragment();
      for (var i = sb.length - add; i < sb.length; i++) {
        el = doc.createElement("div");
        el.className = "term-row";
        this._fill(el, sb[i], -1);
        frag.appendChild(el);
      }
      this.back.appendChild(frag);
      this.hist += add;
      this.seen = em.sbPushed;
    }
    var extra = this.hist - sb.length;
    if (extra > 0) {
      for (var k = 0; k < extra; k++) this.back.removeChild(this.back.firstChild);
      this.hist -= extra;
      if (!this.pinned) this.view.scrollTop = Math.max(0, this.view.scrollTop - extra * this.rh);
    }
    if (this.back.hidden !== !!em.alt) this.back.hidden = !!em.alt;

    while (this.rowEls.length < em.rows) {
      el = doc.createElement("div");
      el.className = "term-row";
      this.screen.appendChild(el);
      this.rowEls.push(el); this.lastRow.push(null); this.lastV.push(-1); this.lastCur.push(-2);
    }
    while (this.rowEls.length > em.rows) {
      this.screen.removeChild(this.rowEls.pop());
      this.lastRow.pop(); this.lastV.pop(); this.lastCur.pop();
    }
    var showCur = em.cursorVisible && this.pinned;
    for (y = 0; y < em.rows; y++) {
      var row = em.grid[y], cur = showCur && y === em.cy ? em.cx : -1;
      if (row !== this.lastRow[y] || row.v !== this.lastV[y] || cur !== this.lastCur[y]) {
        this._fill(this.rowEls[y], row, cur);
        this.lastRow[y] = row; this.lastV[y] = row.v; this.lastCur[y] = cur;
      }
    }
    if (this.pinned) this._toBottom();
  };

  Renderer.prototype.destroy = function () {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.raf && typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.ro) this.ro.disconnect();
    this.view.removeEventListener("scroll", this._onScroll);
    if (this.view.parentNode) this.view.parentNode.removeChild(this.view);
    if (this.probe.parentNode) this.probe.parentNode.removeChild(this.probe);
    this.host.classList.remove("focus");
  };

  root.HGTerm = {
    Emulator: Emulator,
    Renderer: Renderer,
    Utf8Stream: Utf8Stream,
    keyBytes: keyBytes,
    ctrlChar: ctrlChar,
    pasteText: pasteText,
    wcwidth: wcwidth,
    version: 1
  };
})(typeof window !== "undefined" ? window : globalThis);
"""

TERM_CSS = r"""
.term-view{position:absolute;top:0;right:0;bottom:0;left:0;box-sizing:border-box;padding:0 4px;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;overflow-anchor:none;touch-action:pan-y;-webkit-overflow-scrolling:touch;background:var(--base);color:var(--text);font:13px/1.25 var(--mono);font-variant-ligatures:none;-webkit-user-select:text;user-select:text;outline:none}
.term-probe{position:absolute;left:0;top:0;width:0;overflow:hidden;visibility:hidden;pointer-events:none;white-space:pre;font:13px/1.25 var(--mono);font-variant-ligatures:none}
.term-back[hidden]{display:none}
.term-row{height:1.25em;overflow:hidden;white-space:pre}
.term-row>span{display:inline-block;height:1.25em;vertical-align:top}
.tf0{color:var(--ansi0)}.tf1{color:var(--ansi1)}.tf2{color:var(--ansi2)}.tf3{color:var(--ansi3)}
.tf4{color:var(--ansi4)}.tf5{color:var(--ansi5)}.tf6{color:var(--ansi6)}.tf7{color:var(--ansi7)}
.tf8{color:var(--ansi8)}.tf9{color:var(--ansi9)}.tf10{color:var(--ansi10)}.tf11{color:var(--ansi11)}
.tf12{color:var(--ansi12)}.tf13{color:var(--ansi13)}.tf14{color:var(--ansi14)}.tf15{color:var(--ansi15)}
.tb0{background:var(--ansi0)}.tb1{background:var(--ansi1)}.tb2{background:var(--ansi2)}.tb3{background:var(--ansi3)}
.tb4{background:var(--ansi4)}.tb5{background:var(--ansi5)}.tb6{background:var(--ansi6)}.tb7{background:var(--ansi7)}
.tb8{background:var(--ansi8)}.tb9{background:var(--ansi9)}.tb10{background:var(--ansi10)}.tb11{background:var(--ansi11)}
.tb12{background:var(--ansi12)}.tb13{background:var(--ansi13)}.tb14{background:var(--ansi14)}.tb15{background:var(--ansi15)}
.tfd{color:var(--base)}
.tbd{background:var(--text)}
.tB{font-weight:700}
.tD{opacity:.6}
.tI{font-style:italic}
.tU{text-decoration:underline}
.tS{text-decoration:line-through}
.tU.tS{text-decoration:underline line-through}
.tH{color:transparent}
.tw{width:2ch;overflow:hidden}
.tc{outline:1px solid var(--accent);outline-offset:-1px}
.focus .tc{background:var(--accent)!important;color:var(--deep)!important;outline:none}
.term-page{position:fixed;left:0;right:0;top:0;height:100dvh;z-index:25;display:flex;flex-direction:column;background:var(--base);color:var(--text)}
.term-top{flex:none;display:flex;align-items:center;gap:2px;min-height:48px;padding:var(--sat) calc(4px + var(--sar)) 0 calc(4px + var(--sal));background:var(--deep);border-bottom:1px solid var(--rule)}
.term-title{font-weight:700;color:var(--bright);white-space:nowrap;padding:0 1ch 0 .5ch}
.term-title .accent{margin-right:.6ch}
.term-sub{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--dim);font-size:12px;padding:0}
.term-top .status-chip{flex:none;margin-right:4px}
.term-top .btn.icon{width:38px;min-width:38px}
.conn-end{color:var(--muted)}
.term-tabs{flex:none;display:flex;overflow-x:auto;padding:0 calc(8px + var(--sar)) 0 calc(8px + var(--sal));background:var(--base);border-bottom:1px solid var(--rule-base);scrollbar-width:none}
.term-tabs::-webkit-scrollbar{display:none}
.term-host{position:relative;flex:1;min-height:0;min-width:0;background:var(--base)}
.term-latest{position:absolute;right:12px;bottom:10px;z-index:2;min-height:36px;padding:2px 12px;background:var(--surface);border-color:var(--edge-hi);color:var(--bright)}
.term-note{flex:none;display:flex;align-items:center;gap:10px;padding:6px calc(12px + var(--sar)) 6px calc(12px + var(--sal));background:var(--surface);border-top:1px solid var(--rule-surface);color:var(--amber);font-size:12px}
.term-note .msg{flex:1;min-width:0;overflow-wrap:anywhere}
.term-keys{flex:none;display:flex;gap:4px;overflow-x:auto;padding:6px calc(8px + var(--sar)) calc(6px + var(--sab)) calc(8px + var(--sal));background:var(--surface);border-top:1px solid var(--rule-surface);scrollbar-width:none}
.term-keys::-webkit-scrollbar{display:none}
.term-keys .btn{flex:none;min-width:44px;padding:6px 10px}
.term-keys .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--deep);font-weight:700}
.term-in{position:absolute;left:0;bottom:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;resize:none;background:transparent;color:transparent;caret-color:transparent;pointer-events:none}
"""

TERM_VIEW_JS = r"""
(function (root) {
  "use strict";

  var KEY_ID = "hivegrid-remote-term";
  var KEY_FONT = "hivegrid-remote-term-font";
  var MAX_IN = 4000;
  var MAX_QUEUE = 65536;
  var HOLD_MS = 420;
  var REPEAT_MS = 70;
  var KEYS = [
    { l: "Esc", key: "Escape", a: "Escape" },
    { l: "Tab", key: "Tab", a: "Tab" },
    { l: "Ctrl", mod: "ctrl", a: "Control, applies to the next key" },
    { l: "Alt", mod: "alt", a: "Alt, applies to the next key" },
    { l: "\u2190", key: "ArrowLeft", rep: true, a: "Left" },
    { l: "\u2193", key: "ArrowDown", rep: true, a: "Down" },
    { l: "\u2191", key: "ArrowUp", rep: true, a: "Up" },
    { l: "\u2192", key: "ArrowRight", rep: true, a: "Right" },
    { l: "Home", key: "Home", a: "Home" },
    { l: "End", key: "End", a: "End" },
    { l: "PgUp", key: "PageUp", rep: true, a: "Page up" },
    { l: "PgDn", key: "PageDown", rep: true, a: "Page down" },
    { l: "^C", text: "\x03", a: "Control C, interrupt" },
    { l: "^D", text: "\x04", a: "Control D, end of input" },
    { l: "^Z", text: "\x1a", a: "Control Z, suspend" },
    { l: "|", text: "|", a: "Pipe" },
    { l: "~", text: "~", a: "Tilde" },
    { l: "/", text: "/", a: "Slash" },
    { l: "-", text: "-", a: "Hyphen" },
    { l: "paste", paste: true, a: "Paste from the clipboard" }
  ];

  function store(k, v) {
    try {
      if (v === undefined) { return root.localStorage.getItem(k); }
      if (v === null) { root.localStorage.removeItem(k); } else { root.localStorage.setItem(k, v); }
    } catch (e) { /* storage unavailable */ }
    return null;
  }

  function el(tag, cls, text, attrs) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    if (attrs) { Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); }); }
    return e;
  }

  function reason(r) {
    return r && r.data && r.data.message ? String(r.data.message) : "request failed (" + (r ? r.status : "?") + ")";
  }

  // ctx: { api(path, opt), toast(msg, isErr), ask(text, okLabel), close() }. Returns { destroy() }.
  function open(ctx) {
    var dead = false;
    var fontSize = parseInt(store(KEY_FONT), 10) || 13;
    var mods = { ctrl: false, alt: false };
    var sessions = [];
    var cur = null;
    var fitTimer = 0;
    var shadow = "";
    var modBtns = {};
    var app = document.getElementById("app");

    // ---- DOM
    var back = el("a", "btn ghost icon", "\u25c0", { href: "#/", "aria-label": "Back to agents" });
    var title = el("span", "term-title");
    title.appendChild(el("span", "accent", "\u2b21"));
    var titleText = document.createTextNode("Terminal");
    title.appendChild(titleText);
    var sub = el("span", "term-sub");
    var conn = el("span", "status-chip conn-wait", "wait", { role: "status" });
    var smaller = el("button", "btn ghost icon", "A\u2212", { type: "button", "aria-label": "Smaller text" });
    var larger = el("button", "btn ghost icon", "A+", { type: "button", "aria-label": "Larger text" });
    var plus = el("button", "btn ghost icon", "+", { type: "button", "aria-label": "New terminal" });
    var stop = el("button", "btn ghost icon", "\u2715", { type: "button", "aria-label": "End this shell" });
    var head = el("header", "term-top");
    [back, title, sub, conn, smaller, larger, plus, stop].forEach(function (n) { head.appendChild(n); });
    var tabs = el("div", "term-tabs", null, { role: "group", "aria-label": "Terminals" });
    tabs.hidden = true;
    var host = el("div", "term-host");
    var latest = el("button", "btn term-latest", "\u2193 latest", { type: "button" });
    latest.hidden = true;
    host.appendChild(latest);
    var noteMsg = el("span", "msg");
    var noteBtn = el("button", "btn", "", { type: "button" });
    var note = el("div", "term-note", null, { role: "status" });
    note.appendChild(noteMsg);
    note.appendChild(noteBtn);
    note.hidden = true;
    var keys = el("div", "term-keys", null, { role: "group", "aria-label": "Extra keys" });
    var inp = el("textarea", "term-in", null, { rows: "1", autocomplete: "off", autocorrect: "off", autocapitalize: "off", spellcheck: "false", "aria-label": "Terminal input", enterkeyhint: "enter" });
    var page = el("div", "term-page", null, { role: "region", "aria-label": "Terminal" });
    [head, tabs, host, note, keys, inp].forEach(function (n) { page.appendChild(n); });

    // ---- small helpers
    function call(path, body, timeout, holder) {
      var opt = { timeout: timeout || 12000 };
      if (body !== undefined) { opt.body = body; }
      if (holder) { opt.loop = holder; }
      return ctx.api(path, opt).then(null, function () { return { status: 0, data: null }; });
    }
    function setConn(kind) {
      conn.className = "status-chip conn-" + kind;
      conn.textContent = kind === "live" ? "live" : (kind === "wait" ? "retry" : (kind === "end" ? "ended" : "offline"));
    }
    function showNote(text, label, fn) {
      noteMsg.textContent = text;
      noteBtn.textContent = label || "";
      noteBtn.hidden = !label;
      noteBtn.onclick = fn || null;
      note.hidden = false;
    }
    function hideNote() { note.hidden = true; }
    function refused(r) {
      if (r.status === 403) { ctx.toast(reason(r), true); ctx.close(); return true; }
      return false;
    }
    function setMods(ctrl, alt) {
      mods.ctrl = ctrl;
      mods.alt = alt;
      Object.keys(modBtns).forEach(function (k) { modBtns[k].setAttribute("aria-pressed", mods[k] ? "true" : "false"); });
    }
    function focusInput() { try { inp.focus({ preventScroll: true }); } catch (e) { inp.focus(); } }

    // ---- sessions
    function drawTabs() {
      while (tabs.firstChild) { tabs.removeChild(tabs.firstChild); }
      tabs.hidden = sessions.length < 2;
      sessions.forEach(function (s) {
        var on = cur && cur.id === s.id;
        var b = el("button", "chip", null, { type: "button", "aria-pressed": on ? "true" : "false" });
        b.appendChild(el("span", "pill", String(s.n)));
        b.addEventListener("click", function () { if (!on) { attach(s); } });
        tabs.appendChild(b);
      });
    }
    function forget(id) {
      sessions = sessions.filter(function (s) { return s.id !== id; });
      if (store(KEY_ID) === id) { store(KEY_ID, null); }
    }
    function best() {
      var alive = sessions.filter(function (s) { return s.alive; });
      var pool = alive.length ? alive : sessions;
      var want = store(KEY_ID);
      var hit = pool.filter(function (s) { return s.id === want; })[0];
      return hit || pool[pool.length - 1] || null;
    }
    function measure() {
      var r = new root.HGTerm.Renderer(host, new root.HGTerm.Emulator(80, 24), { fontSize: fontSize });
      var f = r.fit();
      r.destroy();
      return f;
    }
    function load() {
      return call("/api/terminals").then(function (r) {
        if (dead) { return false; }
        if (r.status === 200 && r.data) { sessions = r.data.sessions || []; return true; }
        if (refused(r)) { return false; }
        if (r.status !== 401) { showNote("The terminals could not be listed: " + reason(r), "Retry", begin); }
        return false;
      });
    }
    function begin() {
      hideNote();
      load().then(function (ok) {
        if (!ok || dead) { return; }
        var s = best();
        if (s) { attach(s); } else { create(); }
      });
    }
    function create() {
      var f = measure();
      call("/api/terminals", { cols: f.cols, rows: f.rows }, 15000).then(function (r) {
        if (dead) { return; }
        if (r.status === 200 && r.data && r.data.id) {
          sessions.push(r.data);
          attach(r.data);
          return;
        }
        if (refused(r) || r.status === 401) { return; }
        ctx.toast(reason(r), true);
        if (!cur) { showNote(reason(r), "Try again", create); }
      });
    }
    function detach() {
      var s = cur;
      cur = null;
      if (!s) { return; }
      s.dead = true;
      root.clearTimeout(s.timer);
      root.clearTimeout(s.ftimer);
      if (s.req.ctrl) { try { s.req.ctrl.abort(); } catch (e) { /* done already */ } }
      s.rend.destroy();
      latest.hidden = true;
      sub.textContent = "";
    }
    function attach(info) {
      detach();
      store(KEY_ID, info.id);
      var s = { id: info.id, n: info.n, pos: 0, catching: true, fitted: false, seq: info["in"] || 0, inq: "", inflight: false,
        fails: 0, ifails: 0, ended: false, dead: false, pid: 0, req: { ctrl: null }, timer: 0, ftimer: 0, utf8: new root.HGTerm.Utf8Stream() };
      s.em = new root.HGTerm.Emulator(info.cols, info.rows, {
        onReply: function (text) { type(s, text); },
        onTitle: function (t) { if (cur === s) { sub.textContent = String(t).replace(/[\u0000-\u001f\u007f-\u009f]/g, "").slice(0, 80); } }
      });
      s.em.quiet = true;
      s.rend = new root.HGTerm.Renderer(host, s.em, {
        fontSize: fontSize,
        onResize: scheduleFit,
        onPinChange: function (p) { if (cur === s) { latest.hidden = p; } }
      });
      s.rend.setFocus(document.activeElement === inp);
      cur = s;
      titleText.nodeValue = "Terminal " + info.n;
      hideNote();
      setConn("wait");
      drawTabs();
      pump(s);
      if (!root.matchMedia || !root.matchMedia("(pointer: coarse)").matches) { focusInput(); }
    }

    // ---- output: held polls from the position the screen has reached
    function pump(s) {
      if (s.dead || s.ended) { return; }
      var id = ++s.pid;
      call("/api/terminals/" + s.id + "/out?from=" + s.pos + "&wait=" + (s.catching ? 0 : 20), undefined, 32000, s.req).then(function (r) {
        if (s.dead || id !== s.pid) { return; }
        if (r.status === 200 && r.data) { s.fails = 0; got(s, r.data); return; }
        if (r.status === 404) { gone(s); return; }
        if (refused(r) || r.status === 401) { return; }
        s.fails += 1;
        setConn(s.fails > 2 ? "down" : "wait");
        var d = r.status === 429 && r.retry > 0 ? r.retry * 1000 : Math.min(5000, 400 * Math.pow(2, s.fails));
        s.timer = root.setTimeout(function () { pump(s); }, d);
      });
    }
    function got(s, d) {
      if (d.reset) { s.em.reset(); s.utf8.reset(); s.em.quiet = true; s.catching = true; s.fitted = false; }
      var text = s.utf8.decode(d.data || "");
      if (text) { s.em.write(text); }
      s.pos = d.next;
      if (!s.inflight && !s.inq && typeof d["in"] === "number") { s.seq = d["in"]; }
      s.rend.schedule();
      var caught = d.next >= d.end;
      if (s.catching && caught) {
        s.catching = false;
        s.em.quiet = false;
        if (!s.fitted) { sync(s, true); }
      }
      if (!d.alive && caught) { finished(s, d.exit); return; }
      setConn("live");
      s.timer = root.setTimeout(function () { pump(s); }, 0);
    }
    function finished(s, code) {
      s.ended = true;
      setConn("end");
      showNote(typeof code === "number" ? "The shell ended (status " + code + ")." : "The shell ended.", "New terminal", create);
    }
    function gone(s) {
      s.ended = true;
      forget(s.id);
      setConn("end");
      drawTabs();
      showNote("This terminal was closed on the machine.", "New terminal", create);
    }

    // ---- size
    function sync(s, redraw) {
      if (s.dead) { return; }
      var f = s.rend.fit();
      var changed = f.cols !== s.em.cols || f.rows !== s.em.rows;
      if (changed) { s.em.resize(f.cols, f.rows); s.rend.render(); }
      s.fitted = true;
      if (changed || redraw) { call("/api/terminals/" + s.id + "/resize", { cols: f.cols, rows: f.rows, redraw: !!redraw }); }
    }
    function scheduleFit() {
      var s = cur;
      if (!s) { return; }
      root.clearTimeout(s.ftimer);
      s.ftimer = root.setTimeout(function () { if (cur === s && !s.catching) { sync(s, false); } }, 150);
    }

    // ---- input: numbered requests, retried with the same number until the server has them
    function type(s, text) {
      if (!s || s.dead || s.ended || !text) { return; }
      if (s.inq.length < MAX_QUEUE) { s.inq += text; }
      flush(s);
    }
    function flush(s) {
      if (s.dead || s.ended || s.inflight || !s.inq) { return; }
      var n = Math.min(s.inq.length, MAX_IN);
      var c = s.inq.charCodeAt(n - 1);
      if (n < s.inq.length && c >= 0xd800 && c <= 0xdbff) { n -= 1; }
      var data = s.inq.slice(0, n);
      s.inflight = true;
      call("/api/terminals/" + s.id + "/in", { seq: s.seq, data: data }, 10000).then(function (r) {
        s.inflight = false;
        if (s.dead) { return; }
        if (r.status === 200 && r.data) {
          s.seq = typeof r.data["in"] === "number" ? r.data["in"] : s.seq + 1;
          s.inq = s.inq.slice(data.length);
          s.ifails = 0;
          flush(s);
          return;
        }
        if (r.status === 409 && r.data && typeof r.data["in"] === "number") { s.seq = r.data["in"]; flush(s); return; }
        if (r.status === 404) { gone(s); return; }
        if (refused(r) || r.status === 401) { return; }
        if (r.status === 400) { s.inq = s.inq.slice(data.length); flush(s); return; }
        s.ifails += 1;
        var d = r.status === 429 && r.retry > 0 ? r.retry * 1000 : Math.min(3000, 200 * s.ifails);
        s.ftimer2 = root.setTimeout(function () { flush(s); }, d);
      });
    }
    function typed(text) {
      var s = cur;
      if (!s || !text) { return; }
      if (mods.ctrl || mods.alt) {
        if (mods.ctrl) {
          var cc = root.HGTerm.ctrlChar(text.length === 1 ? text : "");
          if (cc !== null) { text = cc; }
        }
        if (mods.alt) { text = "\x1b" + text; }
        setMods(false, false);
      }
      type(s, text);
    }
    function pressKey(name) {
      var s = cur;
      if (!s) { return; }
      var bytes = root.HGTerm.keyBytes({ key: name, ctrlKey: mods.ctrl, altKey: mods.alt, shiftKey: false }, s.em.modes);
      setMods(false, false);
      if (bytes !== null) { type(s, bytes); }
    }
    function pasteNow() {
      var s = cur;
      if (!s) { return; }
      if (!root.navigator.clipboard || !root.navigator.clipboard.readText) { ctx.toast("The browser does not allow reading the clipboard: long-press the terminal and paste.", true); return; }
      root.navigator.clipboard.readText().then(function (t) {
        if (cur === s && t) { type(s, root.HGTerm.pasteText(t, s.em.modes.bracketedPaste)); }
      }, function () { ctx.toast("The clipboard could not be read.", true); });
    }

    // ---- keyboard: hardware keys, the soft keyboard (text arrives as input diffs), paste
    inp.addEventListener("keydown", function (ev) {
      if (ev.isComposing || ev.keyCode === 229 || !cur) { return; }
      var b = root.HGTerm.keyBytes({ key: ev.key, ctrlKey: ev.ctrlKey || mods.ctrl, altKey: ev.altKey || mods.alt, shiftKey: ev.shiftKey,
        metaKey: ev.metaKey, getModifierState: function (m) { return ev.getModifierState(m); } }, cur.em.modes);
      if (b === null) { return; }
      ev.preventDefault();
      setMods(false, false);
      type(cur, b);
    });
    inp.addEventListener("input", function (ev) {
      var v = inp.value;
      var n = Math.min(v.length, shadow.length);
      var i = 0;
      while (i < n && v.charCodeAt(i) === shadow.charCodeAt(i)) { i += 1; }
      var del = Array.from(shadow.slice(i)).length;
      var add = v.slice(i);
      if (del && cur) { type(cur, new Array(del + 1).join("\x7f")); }
      if (add) { typed(add.replace(/\r\n|\n/g, "\r")); }
      shadow = v;
      if (!ev.isComposing) { inp.value = ""; shadow = ""; }
    });
    inp.addEventListener("compositionend", function () { inp.value = ""; shadow = ""; });
    inp.addEventListener("paste", function (ev) {
      ev.preventDefault();
      var t = ev.clipboardData ? ev.clipboardData.getData("text") : "";
      if (cur && t) { type(cur, root.HGTerm.pasteText(t, cur.em.modes.bracketedPaste)); }
    });
    inp.addEventListener("focus", function () { if (cur) { cur.rend.setFocus(true); } });
    inp.addEventListener("blur", function () { if (cur) { cur.rend.setFocus(false); } });
    host.addEventListener("click", function (ev) {
      if (ev.target === latest) { return; }
      var sel = root.getSelection ? String(root.getSelection()) : "";
      if (!sel) { focusInput(); }
    });
    latest.addEventListener("click", function () { if (cur) { cur.rend.scrollToBottom(); } focusInput(); });

    // ---- extra keys: act on tap (a scroll of the row types nothing); a held arrow repeats
    KEYS.forEach(function (k) {
      var b = el("button", "btn key", k.l, { type: "button", "aria-label": k.a });
      if (k.mod) { b.setAttribute("aria-pressed", "false"); modBtns[k.mod] = b; }
      var hold = 0;
      var rep = 0;
      var held = false;
      function act() {
        if (k.mod) { setMods(k.mod === "ctrl" ? !mods.ctrl : mods.ctrl, k.mod === "alt" ? !mods.alt : mods.alt); }
        else if (k.paste) { pasteNow(); }
        else if (k.text) { typed(k.text); }
        else { pressKey(k.key); }
      }
      function stopRepeat() { root.clearTimeout(hold); root.clearInterval(rep); hold = 0; rep = 0; }
      b.addEventListener("pointerdown", function (ev) {
        ev.preventDefault();
        held = false;
        if (!k.rep) { return; }
        hold = root.setTimeout(function () { held = true; act(); rep = root.setInterval(act, REPEAT_MS); }, HOLD_MS);
      });
      b.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (n) { b.addEventListener(n, stopRepeat); });
      b.addEventListener("click", function () {
        if (held) { held = false; return; }
        act();
        if (k.paste) { return; }
        if (!root.matchMedia || !root.matchMedia("(pointer: coarse)").matches) { focusInput(); }
      });
      keys.appendChild(b);
    });

    // ---- header actions
    smaller.addEventListener("click", function () { zoom(-1); });
    larger.addEventListener("click", function () { zoom(1); });
    function zoom(d) {
      fontSize = Math.max(9, Math.min(20, fontSize + d));
      store(KEY_FONT, String(fontSize));
      if (cur) { cur.rend.setFontSize(fontSize); }
    }
    plus.addEventListener("click", function () { create(); });
    stop.addEventListener("click", function () {
      var s = cur;
      if (!s) { return; }
      ctx.ask(s.ended ? "Remove this terminal?" : "End this shell? Whatever runs in it stops.", s.ended ? "Remove" : "End shell").then(function (ok) {
        if (!ok || dead || cur !== s) { return; }
        call("/api/terminals/" + s.id + "/close", {}).then(function (r) {
          if (dead || (r.status !== 200 && r.status !== 404)) { if (!dead && r.status !== 401) { ctx.toast(reason(r), true); } return; }
          detach();
          forget(s.id);
          var next = best();
          if (next) { attach(next); } else { ctx.close(); }
        });
      });
    });

    // ---- phone keyboard and lock screen: the page follows the visible area, a returning page asks again at once
    function fitVisible() {
      var vv = root.visualViewport;
      if (!vv) { return; }
      page.style.top = vv.offsetTop + "px";
      page.style.height = vv.height + "px";
    }
    function wake() {
      var s = cur;
      if (document.hidden || !s || s.ended) { return; }
      root.clearTimeout(s.timer);
      if (s.req.ctrl) { try { s.req.ctrl.abort(); } catch (e) { /* done already */ } }
      s.fails = 0;
      pump(s);
      flush(s);
    }
    document.addEventListener("visibilitychange", wake);
    root.addEventListener("online", wake);
    if (root.visualViewport) {
      root.visualViewport.addEventListener("resize", fitVisible);
      root.visualViewport.addEventListener("scroll", fitVisible);
    }

    document.body.appendChild(page);
    if ("inert" in app) { app.inert = true; }
    fitVisible();
    begin();

    return {
      destroy: function () {
        if (dead) { return; }
        dead = true;
        detach();
        document.removeEventListener("visibilitychange", wake);
        root.removeEventListener("online", wake);
        if (root.visualViewport) {
          root.visualViewport.removeEventListener("resize", fitVisible);
          root.visualViewport.removeEventListener("scroll", fitVisible);
        }
        if (page.parentNode) { page.parentNode.removeChild(page); }
        if ("inert" in app) { app.inert = false; }
      }
    };
  }

  root.HGTermView = { open: open, version: 1 };
})(typeof window !== "undefined" ? window : globalThis);
"""
