"""The mobile web UI of hivegrid-remote, kept as string constants so install.sh ships it flat with bin/.

Pure data, no logic. hivegrid_remote serves INDEX_HTML at /, APP_CSS at /app.css, APP_JS at /app.js,
MANIFEST at /manifest.webmanifest, LOGO_SVG at /logo.svg and the PNG in ICON_PNG_B64 at /icon.png;
/theme.css (the palette as CSS custom properties) and /fonts/*.ttf (the terminal's own font, found on the
server) come from the server. The CSP forbids inline script and style, and the CSS names colours only
through var(--...), so the palette stays in one place (the user's settings). The JS builds all DOM with
textContent, never innerHTML.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<meta name="theme-color" content="#070a10">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Hivegrid</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.png">
<link rel="stylesheet" href="/theme.css">
<link rel="stylesheet" href="/app.css">
<script src="/app.js" defer></script>
</head>
<body>
<main id="app"><noscript><p class="noscript">Hivegrid remote needs JavaScript.</p></noscript></main>
<dialog id="confirm" aria-labelledby="confirm-title" aria-describedby="confirm-text">
<h2 id="confirm-title" class="dlg-title"><span class="accent">&#x2b21;</span> Confirm</h2>
<p id="confirm-text"></p>
<div class="dialog-actions">
<button type="button" id="confirm-cancel" class="btn">Cancel</button>
<button type="button" id="confirm-ok" class="btn danger">Send</button>
</div>
</dialog>
<div id="toasts" class="toasts" role="status" aria-live="polite" aria-atomic="false"></div>
</body>
</html>
"""

APP_CSS = r"""/* Colours come only from /theme.css custom properties (the Hivegrid palette); see design.md.
   Everything is monospace, like the terminal; glyphs are geometric Unicode, never emoji or icon fonts. */
@font-face {
  font-family: "HG Mono";
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url("/fonts/mono.ttf") format("truetype"), local("DejaVuSansM Nerd Font Mono"), local("DejaVu Sans Mono");
}
@font-face {
  font-family: "HG Mono";
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url("/fonts/mono-bold.ttf") format("truetype"), local("DejaVuSansM Nerd Font Mono Bold"), local("DejaVu Sans Mono Bold");
}
:root {
  --mono: "HG Mono", ui-monospace, "SF Mono", "DejaVu Sans Mono", Menlo, Consolas, monospace;
  /* hivegrid_ui.rule_color: mix(bg, edge_hi, .45) */
  --rule: var(--edge-hi);
  --rule-base: var(--edge-hi);
  --rule-surface: var(--edge-hi);
  --hover: var(--raised);
  --tint-accent: var(--pill);
  --tint-violet: var(--pill);
  --tint-red: var(--pill);
  --tint-amber: var(--pill);
  --sat: env(safe-area-inset-top, 0px);
  --sab: env(safe-area-inset-bottom, 0px);
  --sal: env(safe-area-inset-left, 0px);
  --sar: env(safe-area-inset-right, 0px);
}
@supports (color: color-mix(in srgb, red, blue)) {
  :root {
    --rule: color-mix(in srgb, var(--deep) 55%, var(--edge-hi) 45%);
    --rule-base: color-mix(in srgb, var(--base) 55%, var(--edge-hi) 45%);
    --rule-surface: color-mix(in srgb, var(--surface) 55%, var(--edge-hi) 45%);
    --hover: color-mix(in srgb, var(--base) 55%, var(--raised) 45%);
    --tint-accent: color-mix(in srgb, var(--base) 88%, var(--accent) 12%);
    --tint-violet: color-mix(in srgb, var(--surface) 90%, var(--violet) 10%);
    --tint-red: color-mix(in srgb, var(--surface) 90%, var(--red) 10%);
    --tint-amber: color-mix(in srgb, var(--surface) 90%, var(--amber) 10%);
  }
}
*, *::before, *::after { box-sizing: border-box; }
html, body {
  margin: 0;
  height: 100%;
  overflow: hidden;
  background: var(--base);
  color: var(--text);
  color-scheme: dark;
  font: 14px/1.5 var(--mono);
  font-variant-ligatures: none;
  -webkit-text-size-adjust: 100%;
  -webkit-tap-highlight-color: transparent;
  overscroll-behavior: none;
}
[hidden] { display: none !important; }
button, input, textarea { font: inherit; color: inherit; }
a { color: var(--accent); text-decoration: none; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
::selection { background: var(--raised); color: var(--bright); }
.noscript { padding: 16px; color: var(--amber); }
.accent { color: var(--accent); }
.muted { color: var(--muted); }
.dim { color: var(--dim); }
.bold { font-weight: 700; }
.cyan { color: var(--cyan); }

#app { height: 100dvh; display: flex; flex-direction: column; min-width: 0; min-height: 0; }

/* State colours (hivegrid_ui.STATES): glyph and word carry the state, colour only backs it up. */
.s-attention { --sc: var(--violet); }
.s-limit { --sc: var(--red); }
.s-bell { --sc: var(--red); }
.s-done { --sc: var(--green); }
.s-busy { --sc: var(--amber); }
.s-working { --sc: var(--accent); }
.s-idle { --sc: var(--grey); }
.s-exited { --sc: var(--muted); }
.s-web { --sc: var(--cyan); }
.s-shell, .s-other { --sc: var(--dim); }
.st { color: var(--sc, var(--dim)); }
.glyph { display: inline-block; width: 2ch; text-align: center; font-weight: 700; color: var(--sc, var(--dim)); flex: none; }

/* ---------------------------------------------------------------- login */
.login {
  margin: auto;
  width: min(100%, 420px);
  padding: calc(24px + var(--sat)) calc(16px + var(--sar)) calc(24px + var(--sab)) calc(16px + var(--sal));
}
.login-card {
  background: var(--surface);
  border: 1px solid var(--edge-hi);
  border-radius: 6px;
  overflow: hidden;
}
.login-logo { display: block; width: 96px; height: 96px; margin: 28px auto 8px; }
.login-title { margin: 0; text-align: center; font-size: 20px; font-weight: 700; color: var(--accent); }
.login-sub { margin: 4px 20px 18px; text-align: center; color: var(--muted); font-size: 13px; }
.login form { display: flex; flex-direction: column; gap: 10px; padding: 0 16px 16px; }
.login-error { color: var(--red); min-height: 0; margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.login-foot { padding: 8px 16px; border-top: 1px solid var(--rule-surface); color: var(--muted); font-size: 12px; text-align: center; }

/* ---------------------------------------------------------------- controls */
.field {
  width: 100%;
  min-height: 44px;
  padding: 8px 10px;
  font-size: 16px;
  background: var(--deep);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--bright);
}
.field::placeholder { color: var(--dim); }
.field:focus { border-color: var(--accent); outline: none; }
.btn {
  min-height: 44px;
  min-width: 44px;
  padding: 6px 14px;
  background: var(--raised);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  cursor: pointer;
  touch-action: manipulation;
  white-space: nowrap;
}
.btn:active { background: var(--hover); }
.btn:disabled { color: var(--dim); background: var(--pill); cursor: default; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--deep); font-weight: 700; }
.btn.primary:disabled { background: var(--pill); border-color: var(--border); color: var(--dim); }
.btn.danger { background: var(--red); border-color: var(--red); color: var(--deep); font-weight: 700; }
.btn.ghost { background: transparent; border-color: transparent; color: var(--muted); }
.btn.ok { border-color: var(--green); color: var(--green); }
.btn.bad { border-color: var(--red); color: var(--red); }
.btn.key { min-width: 0; }
.btn.key kbd { color: var(--bright); font-weight: 700; margin-right: 0.6ch; }
.btn.icon { padding: 0; width: 44px; font-size: 18px; line-height: 1; display: inline-flex; align-items: center; justify-content: center; }

/* chip: tab strips and buttons. Active: raised + bright + bold; inactive: muted. 44px hit area, 30px pill. */
.chip {
  flex: none;
  min-height: 44px;
  padding: 0 4px;
  background: none;
  border: 0;
  color: var(--muted);
  cursor: pointer;
  touch-action: manipulation;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
}
.chip > span.pill {
  display: inline-flex;
  align-items: center;
  gap: 0.7ch;
  min-height: 30px;
  padding: 0 1.4ch;
  border-radius: 15px;
}
.chip[aria-pressed="true"], .chip[aria-selected="true"], .chip.on { color: var(--bright); font-weight: 700; }
.chip[aria-pressed="true"] > span.pill, .chip[aria-selected="true"] > span.pill, .chip.on > span.pill { background: var(--raised); }
.chip:active > span.pill { background: var(--hover); }
.chip .n { color: var(--muted); font-weight: 400; }
.chip[aria-pressed="true"] .n, .chip[aria-selected="true"] .n { color: var(--bright); }
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.6ch;
  min-height: 24px;
  padding: 0 1.2ch;
  border-radius: 12px;
  background: var(--pill);
  font-size: 12px;
  white-space: nowrap;
}
.status-chip::before { content: "\25CF"; font-size: 9px; }
.conn-live { color: var(--green); }
.conn-wait { color: var(--amber); }
.conn-down { color: var(--red); }
.ro-chip { color: var(--amber); }
.ro-chip::before { content: "\25D0"; font-size: 12px; }

/* ---------------------------------------------------------------- title row */
.top {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: var(--sat) calc(4px + var(--sar)) 0 calc(12px + var(--sal));
  background: var(--deep);
  border-bottom: 1px solid var(--rule);
}
.top-logo { width: 26px; height: 26px; flex: none; display: block; }
.brand { font-weight: 700; color: var(--accent); white-space: nowrap; }
.count { color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.top .spacer { flex: 1; min-width: 0; }
.top-chips { display: flex; align-items: center; gap: 6px; flex: none; }

/* ---------------------------------------------------------------- layout */
.split { flex: 1; min-height: 0; display: flex; min-width: 0; position: relative; }
.list-pane, .detail-pane { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.detail-pane { background: var(--base); }
#app.has-detail .list-pane { display: none; }
#app.has-detail > .top { display: none; }
#app:not(.has-detail) .detail-pane { display: none; }

/* ---------------------------------------------------------------- filter chips */
.chips {
  flex: none;
  display: flex;
  gap: 0;
  overflow-x: auto;
  padding: 0 calc(8px + var(--sar)) 0 calc(8px + var(--sal));
  background: var(--base);
  border-bottom: 1px solid var(--rule-base);
  scrollbar-width: none;
}
.chips::-webkit-scrollbar { display: none; }

/* ---------------------------------------------------------------- list_row */
.list { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain; }
.row {
  position: relative;
  display: grid;
  grid-template-columns: 1.5ch 2ch var(--numw, 2ch) minmax(0, 1fr) auto;
  column-gap: 1ch;
  align-items: baseline;
  padding: 9px calc(12px + var(--sar)) 9px calc(8px + var(--sal));
  color: var(--text);
  min-height: 56px;
  border-bottom: 1px solid var(--rule-base);
  background: var(--base);
}
.rw:nth-child(even) > .row { background: color-mix(in srgb, var(--base) 90%, var(--raised) 10%); }
.rw > .row:active { background: var(--hover); }
.row .mark { color: var(--accent); font-weight: 700; visibility: hidden; }
.rw > .row.sel { background: var(--raised); color: var(--bright); }
.row.sel .mark { visibility: visible; }
.row .num { color: var(--cyan); font-weight: 700; text-align: right; white-space: nowrap; }
.row.sel .num { color: var(--accent); }
.row .name { color: var(--text); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.row.sel .name { color: var(--bright); }
.row .meta { color: var(--dim); font-size: 12px; white-space: nowrap; }
.row .sub { grid-column: 4 / -1; color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.row .sub .st { font-weight: 700; }
.row .pend { grid-column: 4 / -1; color: var(--violet); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* swipe a row to the left to quit its session: the row slides over a red Quit button */
.rw { position: relative; overflow: hidden; background: var(--red); }
.rw > .row { position: relative; z-index: 1; touch-action: pan-y; transition: transform .16s ease-out; }
.rw.drag > .row { transition: none; }
.rw.open > .row { transform: translateX(-96px); }
.rw > .rquit { position: absolute; top: 0; right: 0; bottom: 0; width: 96px; border: 0; background: var(--red); color: var(--deep); font: inherit; font-weight: 700; cursor: pointer; touch-action: manipulation; }
@media (prefers-reduced-motion: reduce) { .rw > .row { transition: none; } }
/* history: past sessions */
.hrow { padding: 10px calc(12px + var(--sar)) 10px calc(12px + var(--sal)); border-bottom: 1px solid var(--rule-base); }
.hrow .ht { color: var(--bright); font-weight: 700; overflow-wrap: anywhere; }
.hrow .hp { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hrow .hm { color: var(--dim); font-size: 12px; overflow-wrap: anywhere; }
.hrow .hb { margin-top: 6px; display: flex; align-items: center; gap: 1.5ch; flex-wrap: wrap; }
.hrow .hl { color: var(--green); font-size: 12px; }
.hrow .why { color: var(--dim); font-size: 12px; }
.empty { padding: 28px 20px; color: var(--muted); text-align: center; }
.empty strong { display: block; color: var(--text); font-weight: 700; margin-bottom: 4px; }

/* footer: keys in text, labels in muted */
.foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 2ch;
  min-height: 44px;
  padding: 0 calc(10px + var(--sar)) var(--sab) calc(10px + var(--sal));
  background: var(--surface);
  border-top: 1px solid var(--rule-surface);
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
}
.foot .k { color: var(--text); font-weight: 700; }
.foot .spacer { flex: 1; }
.foot button { background: none; border: 0; padding: 0; color: inherit; cursor: pointer; min-height: 44px; display: inline-flex; align-items: center; gap: 1ch; }

/* ---------------------------------------------------------------- chat header */
.chead {
  flex: none;
  background: var(--deep);
  border-bottom: 1px solid var(--rule);
  padding: var(--sat) calc(2px + var(--sar)) 0 calc(2px + var(--sal));
}
.chead-row { display: flex; align-items: center; gap: 4px; min-height: 44px; }
.chead .glyph { font-size: 16px; }
.chead .label { color: var(--cyan); font-weight: 700; flex: none; }
.chead .title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; color: var(--bright); padding-left: 1ch; }
.cinfo {
  display: flex;
  gap: 1ch;
  padding: 0 calc(10px + var(--sar)) 6px calc(10px + var(--sal));
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.cinfo .st { font-weight: 700; }
.cinfo .path { color: var(--dim); overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.cctx {
  flex: none;
  display: flex;
  align-items: center;
  gap: 1.5ch;
  padding: 5px calc(10px + var(--sar)) 5px calc(10px + var(--sal));
  background: var(--surface);
  border-bottom: 1px solid var(--rule-surface);
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  white-space: nowrap;
}
.cctx .model { color: var(--text); font-weight: 700; flex: 0 0 auto; }
.cctx .think { color: var(--cyan); flex: none; }
.cctx .pct { color: var(--text); flex: none; }
.cctx .usage { color: var(--dim); overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 1 1 0; }

/* gauges: green < 70, amber < 90, red above */
.gauge { display: inline-block; vertical-align: middle; position: relative; width: 9ch; height: 8px; background: var(--track); border-radius: 2px; overflow: hidden; flex: none; }
.gauge > i { display: block; height: 100%; background: var(--g, var(--green)); }
.g-ok { --g: var(--green); }
.g-warn { --g: var(--amber); }
.g-err { --g: var(--red); }
.gauge.wide { display: block; width: auto; height: 10px; flex: 1; min-width: 0; }

/* ---------------------------------------------------------------- transcript */
.chatbox { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.chat {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
  overflow-anchor: none;
  padding: 10px calc(12px + var(--sar)) 14px calc(12px + var(--sal));
  -webkit-overflow-scrolling: touch;
}
.chat-inner { max-width: 100ch; margin: 0 auto; }
.older { display: flex; justify-content: center; padding: 4px 0 10px; color: var(--dim); font-size: 12px; }
.older .btn { min-height: 36px; font-size: 12px; }
.it { margin: 0 0 10px; min-width: 0; }
.it-user { display: flex; flex-direction: column; align-items: flex-end; margin-top: 14px; }
.it-user:first-child { margin-top: 0; }
.bubble {
  max-width: 88%;
  padding: 7px 10px;
  background: var(--tint-accent);
  border: 1px solid var(--accent);
  border-radius: 8px 8px 2px 8px;
  color: var(--bright);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ts { margin-top: 2px; color: var(--dim); font-size: 11px; }
.it-queued .bubble { background: transparent; border: 1px dashed var(--dim); color: var(--muted); }
.it-queued .ts { color: var(--muted); font-style: italic; }
.it-queued + .it-queued { margin-top: 0; }
.it-queued.failed .bubble { border-color: var(--red); }
.it-queued.failed .ts { color: var(--red); font-style: normal; }
.it-asst { padding-left: 1.2ch; border-left: 2px solid var(--border); color: var(--text); overflow-wrap: anywhere; }
.it-tool { margin-bottom: 2px; }
.it-think, .it-tool, .it-image { padding-left: 1.2ch; border-left: 2px solid transparent; }
.it-note { text-align: center; color: var(--dim); font-size: 12px; margin: 6px 0 12px; }
.it-note span::before { content: "\00B7  "; }
.it-note span::after { content: "  \00B7"; }
.it-limit { text-align: center; color: var(--red); font-size: 12px; margin: 10px 0 12px; }
.it-image { color: var(--dim); }
.it-image::before { content: "\25A3  "; }
.files { margin: 10px 0 4px; display: flex; flex-direction: column; gap: 8px; }
.files:empty { display: none; }
.fcard { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 8px 10px; max-width: 100%; }
.fcard .fhead { display: flex; align-items: baseline; gap: 1ch; font-size: 13px; min-width: 0; }
.fcard .fname { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--bright); }
.fcard .fmeta { flex: none; color: var(--dim); font-size: 12px; }
.fcard .fnote { margin-top: 4px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.fcard .fimg { display: block; margin-top: 8px; max-width: 100%; max-height: 60vh; border-radius: 6px; cursor: zoom-in; background: var(--deep); }
.fcard .fbtns { margin-top: 8px; display: flex; gap: 8px; }
details > summary { list-style: none; cursor: pointer; touch-action: manipulation; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "\25B8"; display: inline-block; width: 2ch; color: var(--dim); }
details[open] > summary::before { content: "\25BE"; }
.think > summary { color: var(--dim); font-size: 12px; min-height: 28px; display: flex; align-items: center; }
.think-body { color: var(--muted); font-size: 12.5px; white-space: pre-wrap; overflow-wrap: anywhere; padding: 2px 0 6px 2ch; max-height: 50vh; overflow-y: auto; }
.tool > summary { display: flex; align-items: baseline; gap: 1ch; min-height: 30px; color: var(--muted); font-size: 13px; }
.tool > summary .tname { color: var(--bright); font-weight: 700; flex: none; }
.tool > summary .tsum { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.tool > summary .tst { flex: none; font-weight: 700; width: 2ch; text-align: center; }
.tool.t-ok .tst { color: var(--green); }
.tool.t-error .tst { color: var(--red); }
.tool.t-error .tname { color: var(--red); }
.tool.t-running .tst { color: var(--accent); }
.out {
  margin: 2px 0 6px 2ch;
  padding: 6px 8px;
  background: var(--deep);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-size: 12px;
  line-height: 1.4;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 40vh;
  overflow-y: auto;
}
.out.empty-out { color: var(--dim); }
.working { display: flex; align-items: center; gap: 1ch; color: var(--accent); font-size: 13px; padding: 2px 0 2px 1.2ch; }
.working .why { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.latest {
  position: absolute;
  right: calc(14px + var(--sar));
  bottom: 12px;
  min-height: 36px;
  padding: 0 14px;
  border-radius: 18px;
  background: var(--accent);
  border: 0;
  color: var(--deep);
  font-weight: 700;
  cursor: pointer;
  z-index: 2;
}
.tailbox { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; background: var(--deep); margin: 0; }
.tail {
  margin: 0;
  padding: 8px calc(10px + var(--sar)) 8px calc(10px + var(--sal));
  font: 12px/1.35 var(--mono);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  user-select: text;
  -webkit-user-select: text;
}
.screen-note { padding: 6px calc(10px + var(--sar)) 6px calc(10px + var(--sal)); font-size: 12px; color: var(--dim); background: var(--surface); border-bottom: 1px solid var(--rule-surface); }

/* markdown subset */
.md > * + * { margin-top: 0.55em; }
.md p { margin: 0; white-space: pre-wrap; }
.md h1, .md h2, .md h3, .md h4, .md h5, .md h6 { margin: 0.8em 0 0.2em; font-size: 14px; color: var(--bright); font-weight: 700; }
.md h1, .md h2 { color: var(--accent); }
.md h1::before { content: "# "; color: var(--dim); }
.md h2::before { content: "## "; color: var(--dim); }
.md h3::before { content: "### "; color: var(--dim); }
.md code { background: var(--pill); color: var(--cyan); padding: 0 0.5ch; border-radius: 3px; font-size: 0.95em; }
.md pre { margin: 0; padding: 8px 10px; background: var(--deep); border: 1px solid var(--border); border-radius: 4px; overflow-x: auto; font-size: 12px; line-height: 1.4; color: var(--text); }
.md pre code { background: none; color: inherit; padding: 0; font-size: inherit; white-space: pre; }
.md .lang { display: block; font-size: 11px; color: var(--dim); margin: 0 0 2px; }
.md .li { display: flex; gap: 1ch; white-space: pre-wrap; }
.md .li .b { flex: none; color: var(--accent); min-width: 2ch; text-align: right; }
.md .li > div { min-width: 0; }
.md .li.d1 { margin-left: 2ch; }
.md .li.d2 { margin-left: 4ch; }
.md .li.d3 { margin-left: 6ch; }
.md .li.d4 { margin-left: 8ch; }
.md .list-md > * + * { margin-top: 0.15em; }
.md blockquote { margin: 0; padding-left: 1.4ch; border-left: 2px solid var(--edge-hi); color: var(--muted); white-space: pre-wrap; }
.md hr { border: 0; border-top: 1px solid var(--rule-base); margin: 0.8em 0; }
.md .tbl { margin: 0; padding: 6px 8px; overflow-x: auto; font-size: 12px; line-height: 1.4; background: var(--deep); border: 1px solid var(--border); border-radius: 4px; white-space: pre; }
.md strong { color: var(--bright); }
.md .lnk { color: var(--accent); }

/* ---------------------------------------------------------------- pending card + composer */
.pcard {
  flex: none;
  max-height: 44vh;
  overflow-y: auto;
  margin: 0;
  padding: 10px calc(12px + var(--sar)) 10px calc(12px + var(--sal));
  background: var(--tint-violet);
  border-top: 1px solid var(--violet);
}
.pcard h3 { margin: 0 0 6px; font-size: 13px; font-weight: 700; color: var(--violet); }
.pcard h3::before { content: "? "; }
.pcard .what { margin: 0 0 8px; color: var(--bright); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 9em; overflow-y: auto; }
.pcard .what b { color: var(--cyan); }
.pcard .hint { margin: 8px 0 0; color: var(--muted); font-size: 12px; }
.pbtns { display: flex; flex-wrap: wrap; gap: 8px; }
.pbtns .btn { flex: 1 1 auto; }
.pbtns .btn.opt { flex: 1 1 100%; text-align: left; overflow: hidden; text-overflow: ellipsis; }
.refine { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.strip {
  flex: none;
  display: flex;
  align-items: center;
  gap: 1ch;
  padding: 2px calc(8px + var(--sar)) 2px calc(12px + var(--sal));
  background: var(--surface);
  border-top: 1px solid var(--rule-surface);
  color: var(--muted);
  font-size: 12px;
}
.strip .grow { flex: 1; }
.strip .btn { min-height: 36px; }
.lcard {
  flex: none;
  max-height: 36vh;
  overflow-y: auto;
  margin: 0;
  padding: 10px calc(12px + var(--sar)) 10px calc(12px + var(--sal));
  background: var(--tint-red);
  border-top: 1px solid var(--red);
}
.lcard h3 { margin: 0 0 4px; font-size: 13px; font-weight: 700; color: var(--red); overflow-wrap: anywhere; }
.lcard p { margin: 0 0 8px; color: var(--text); overflow-wrap: anywhere; }
.lcard p:last-child { margin-bottom: 0; }
.lcard.lc-run { background: var(--tint-accent); border-top-color: var(--accent); }
.lcard.lc-run h3 { color: var(--accent); }
.lcard.lc-done { background: var(--tint-accent); border-top-color: var(--green); }
.lcard.lc-done h3 { color: var(--green); }
.lcard .btn { min-height: 36px; }
.compose {
  flex: none;
  display: flex;
  gap: 6px;
  align-items: flex-end;
  flex-wrap: wrap;
  padding: 8px calc(8px + var(--sar)) calc(8px + var(--sab)) calc(8px + var(--sal));
  background: var(--deep);
  border-top: 1px solid var(--rule);
}
.compose textarea { flex: 1; min-width: 0; resize: none; line-height: 1.4; max-height: 9.5em; min-height: 44px; }
.compose .btn.send { min-width: 6ch; }
.compose .note { flex: 1 1 100%; margin: 0 0 2px 4px; font-size: 12px; color: var(--amber); }
.compose-wrap { flex: none; }
.ro .write { display: none !important; }
.d-body { display: contents; }
kbd { font: inherit; }
.d-empty { margin: auto; padding: 24px; color: var(--muted); text-align: center; }
.d-empty .big { display: block; width: 72px; height: 72px; margin: 0 auto 12px; opacity: 0.85; }

/* ---------------------------------------------------------------- sheets */
.overlay { position: fixed; inset: 0; z-index: 30; display: flex; flex-direction: column; justify-content: flex-end; }
.scrim { position: absolute; inset: 0; background: var(--deep); opacity: 0.72; }
.sheet {
  position: relative;
  display: flex;
  flex-direction: column;
  max-height: 86dvh;
  background: var(--surface);
  border: 1px solid var(--edge-hi);
  border-bottom: 0;
  border-radius: 10px 10px 0 0;
  animation: hg-up 0.18s ease-out;
  width: 100%;
  max-width: 640px;
  margin: 0 auto;
}
@keyframes hg-up { from { transform: translateY(24px); opacity: 0; } to { transform: none; opacity: 1; } }
.shead { flex: none; display: flex; align-items: center; gap: 6px; min-height: 48px; padding: 0 4px 0 12px; border-bottom: 1px solid var(--rule-surface); }
.shead .ttl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.shead .ttl b { color: var(--accent); }
.sbody { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding-bottom: calc(8px + var(--sab)); }
.arow {
  display: grid;
  grid-template-columns: 1.5ch 2ch minmax(0, 1fr) auto;
  column-gap: 1ch;
  align-items: baseline;
  align-content: center;
  width: 100%;
  min-height: 52px;
  padding: 8px calc(12px + var(--sar)) 8px calc(8px + var(--sal));
  background: none;
  border: 0;
  border-bottom: 1px solid var(--rule-surface);
  text-align: left;
  color: var(--text);
  cursor: pointer;
  touch-action: manipulation;
}
.arow:active:not(:disabled) { background: var(--raised); }
.arow .mark { color: var(--accent); font-weight: 700; visibility: hidden; }
.arow.cur .mark { visibility: visible; }
.arow .g { color: var(--accent); text-align: center; font-weight: 700; }
.arow .t { font-weight: 700; color: var(--bright); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.arow .m { color: var(--dim); font-size: 12px; white-space: nowrap; }
.arow .h { grid-column: 3 / -1; color: var(--muted); font-size: 12px; }
.arow:disabled { cursor: default; }
.arow:disabled .t, .arow:disabled .g { color: var(--dim); }
.arow.danger .g, .arow.danger .t { color: var(--red); }
.arow.danger:disabled .g, .arow.danger:disabled .t { color: var(--dim); }
.arow.cur { background: var(--raised); }
.arow.cur .t { color: var(--bright); }
.group-h { padding: 12px calc(12px + var(--sar)) 4px calc(12px + var(--sal)); color: var(--muted); font-size: 12px; font-weight: 700; border-bottom: 1px solid var(--rule-surface); }
.seg-wrap { padding: 8px calc(12px + var(--sar)) 12px calc(12px + var(--sal)); border-bottom: 1px solid var(--rule-surface); }
.seg-wrap .lbl { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.seg { display: flex; background: var(--deep); border: 1px solid var(--border); border-radius: 6px; padding: 2px; gap: 2px; }
.seg button { flex: 1; min-height: 40px; padding: 0 2px; background: none; border: 0; border-radius: 4px; color: var(--muted); font-size: 12px; cursor: pointer; touch-action: manipulation; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.seg button[aria-pressed="true"] { background: var(--raised); color: var(--bright); font-weight: 700; }
.seg button:disabled { color: var(--dim); cursor: default; }
.sfilter { padding: 8px calc(12px + var(--sar)) 8px calc(12px + var(--sal)); border-bottom: 1px solid var(--rule-surface); }

/* ---------------------------------------------------------------- menu */
.menu { position: fixed; inset: 0; z-index: 20; display: flex; justify-content: flex-end; }
.menu .scrim { display: none; }
.menu-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--base);
  animation: hg-in 0.18s ease-out;
}
@keyframes hg-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
.menu-head { flex: none; display: flex; align-items: center; gap: 8px; min-height: 48px; padding: var(--sat) calc(4px + var(--sar)) 0 calc(12px + var(--sal)); background: var(--deep); border-bottom: 1px solid var(--rule); }
.menu-head .ttl { flex: 1; min-width: 0; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.menu-head .ttl b { color: var(--accent); }
.menu-tabs { flex: none; display: flex; overflow-x: auto; padding: 0 calc(8px + var(--sar)) 0 calc(8px + var(--sal)); background: var(--base); border-bottom: 1px solid var(--rule-base); scrollbar-width: none; }
.menu-tabs::-webkit-scrollbar { display: none; }
.menu-body { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 4px 0 calc(16px + var(--sab)); }
.sec-h { display: flex; align-items: baseline; gap: 1ch; padding: 14px calc(12px + var(--sar)) 6px calc(12px + var(--sal)); color: var(--muted); font-size: 12px; }
.sec-h::after { content: ""; flex: 1; border-top: 1px solid var(--rule-base); transform: translateY(-3px); }
.acct { position: relative; padding: 10px calc(12px + var(--sar)) 12px calc(12px + var(--sal)); border-bottom: 1px solid var(--rule-base); }
.acct-top { display: flex; align-items: baseline; gap: 1ch; }
.acct-top .mark { color: var(--accent); font-weight: 700; width: 1.5ch; }
.acct-top .nm { font-weight: 700; color: var(--bright); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.acct-top .as { font-size: 12px; font-weight: 700; }
.as-ok { color: var(--green); }
.as-expired, .as-error { color: var(--red); }
.as-login { color: var(--amber); }
.as-unknown { color: var(--dim); }
.qrow { display: grid; grid-template-columns: 3ch minmax(0, 1fr) 5ch; column-gap: 1ch; align-items: center; margin-top: 6px; font-size: 12px; padding-left: 2.5ch; }
.qrow .lab { color: var(--muted); }
.qrow .pc { text-align: right; color: var(--text); font-weight: 700; }
.qrow .rs { grid-column: 2 / -1; color: var(--dim); font-size: 11px; margin-top: -2px; }
.acct .use { margin-top: 6px; padding-left: 2.5ch; color: var(--muted); font-size: 12px; }
.acct .use b { color: var(--cyan); font-weight: 700; }
.acct .none { margin-top: 6px; padding-left: 2.5ch; color: var(--dim); font-size: 12px; }
.tot { padding: 10px calc(12px + var(--sar)) 4px calc(12px + var(--sal)); color: var(--muted); font-size: 12px; }
.tot b { color: var(--text); }
.note-line { padding: 8px calc(12px + var(--sar)) 8px calc(12px + var(--sal)); color: var(--dim); font-size: 12px; }
.note-line.err { color: var(--amber); }
.opts { display: flex; flex-wrap: wrap; padding: 0 calc(8px + var(--sar)) 0 calc(8px + var(--sal)); }
.dirs { border-top: 1px solid var(--rule-base); }
.drow { display: grid; grid-template-columns: 1.5ch minmax(0, 1fr); column-gap: 1ch; align-items: baseline; width: 100%; min-height: 48px; padding: 6px calc(12px + var(--sar)) 6px calc(8px + var(--sal)); background: none; border: 0; border-bottom: 1px solid var(--rule-base); text-align: left; color: var(--text); cursor: pointer; touch-action: manipulation; }
.drow .mark { color: var(--accent); font-weight: 700; visibility: hidden; }
.drow .nm { font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drow .pth { grid-column: 2; color: var(--dim); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drow.cur { background: var(--raised); }
.drow.cur .mark { visibility: visible; }
.drow.cur .nm { color: var(--bright); }
.startbar { padding: 12px calc(12px + var(--sar)) 4px calc(12px + var(--sal)); display: flex; flex-direction: column; gap: 8px; }
.startbar .why { color: var(--muted); font-size: 12px; }
.sysrow { padding: 10px calc(12px + var(--sar)) 6px calc(12px + var(--sal)); }
.sysrow .hd { display: flex; align-items: baseline; gap: 1ch; font-size: 13px; }
.sysrow .hd .lab { font-weight: 700; color: var(--bright); width: 5ch; }
.sysrow .hd .det { color: var(--dim); font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right; }
.sysrow .hd .pc { font-weight: 700; width: 5ch; text-align: right; }
.sysrow .bar { display: flex; margin-top: 6px; }
.spark { display: block; width: 100%; height: 30px; margin-top: 6px; color: var(--accent); }
.spark path.area { fill: var(--g, var(--accent)); opacity: 0.16; stroke: none; }
.spark polyline { fill: none; stroke: var(--g, var(--accent)); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
.kv { display: grid; grid-template-columns: 14ch minmax(0, 1fr); gap: 4px 1ch; padding: 6px calc(12px + var(--sar)) 6px calc(12px + var(--sal)); font-size: 13px; }
.kv .k { color: var(--muted); }
.kv .v { color: var(--text); overflow-wrap: anywhere; }
.about-act { padding: 12px calc(12px + var(--sar)) 4px calc(12px + var(--sal)); display: flex; flex-direction: column; gap: 8px; }

/* ---------------------------------------------------------------- dialog + toasts */
dialog {
  width: min(92vw, 400px);
  padding: 0;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--edge-hi);
  border-radius: 8px;
  font: inherit;
}
dialog::backdrop { background: var(--deep); opacity: 0.78; }
.dlg-title { margin: 0; padding: 10px 14px; font-size: 14px; font-weight: 700; color: var(--bright); border-bottom: 1px solid var(--rule-surface); }
dialog p { margin: 0; padding: 14px; overflow-wrap: anywhere; color: var(--text); }
.dialog-actions { display: flex; justify-content: flex-end; gap: 10px; padding: 0 14px 14px; }
.toasts {
  position: fixed;
  left: 0;
  right: 0;
  bottom: calc(76px + var(--sab));
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  pointer-events: none;
  z-index: 60;
}
.toast {
  max-width: min(100%, 460px);
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--edge-hi);
  border-radius: 6px;
  color: var(--bright);
  overflow-wrap: anywhere;
  font-size: 13px;
  animation: hg-up 0.15s ease-out;
}
.toast .tt { color: var(--accent); font-weight: 700; margin-right: 1ch; }
.toast.err { border-color: var(--red); }
.toast.err .msg { color: var(--red); }

/* ---------------------------------------------------------------- wide screens: list and chat side by side */
@media (min-width: 900px) {
  #app.has-detail > .top { display: flex; }
  #app:not(.has-detail) .detail-pane { display: flex; }
  #app.has-detail .list-pane { display: flex; }
  .list-pane { flex: none; width: 400px; border-right: 1px solid var(--rule-base); }
  .row.sel { box-shadow: inset 0 0 0 0 transparent; }
  .back { display: none !important; }
  .chead { padding-top: 0; }
  .chead-row { padding-left: 10px; }
  .menu { background: transparent; }
  .menu .scrim { display: block; }
  .menu-panel { width: 480px; border-left: 1px solid var(--edge-hi); }
  .overlay { justify-content: center; align-items: center; }
  .sheet { max-height: 80dvh; border-radius: 10px; border-bottom: 1px solid var(--edge-hi); width: 520px; margin: 0; }
  .toasts { bottom: 24px; }
  .chead { padding-top: 0; }
}
@media (max-width: 899px) {
  .toasts { bottom: calc(130px + var(--sab)); }
  .top-logo { width: 24px; height: 24px; }
}
@media (max-width: 470px) {
  .top .wide-only { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; animation: none !important; transition: none !important; }
}
"""

APP_JS = r"""(function () {
  "use strict";

  var TOKEN_KEY = "hivegrid-remote-token";
  var STATE_ORDER = ["attention", "limit", "working", "busy", "done", "idle", "exited"];
  var STATE_DEFAULTS = {
    attention: { glyph: "?", label: "needs you" },
    limit: { glyph: "\u2298", label: "limit" },
    working: { glyph: "\u21bb", label: "working" },
    busy: { glyph: "\u25d4", label: "busy" },
    done: { glyph: "\u2713", label: "done" },
    idle: { glyph: "\u00b7", label: "idle" },
    exited: { glyph: "\u25cb", label: "exited" },
    bell: { glyph: "!", label: "bell" },
    web: { glyph: "\u25cd", label: "web" },
    shell: { glyph: "\u203a", label: "shell" }
  };
  var DEFAULT_LIMITS = { prompt: 8000, key_text: 256, feedback: 4000 };
  var DEFAULT_LEVELS = ["off", "minimal", "low", "medium", "high"];
  var SPIN = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f";
  var WAIT = 20;
  var CHAT_LIMIT = 60;
  var TAIL_LINES = 200;
  var SECTIONS = [["usage", "Usage"], ["new", "New session"], ["history", "History"], ["system", "System"], ["about", "About"]];
  var REDUCED = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ---------------------------------------------------------------- DOM helpers: text only, never markup
  function append(el, c) {
    if (c === null || c === undefined || c === false) { return; }
    if (Array.isArray(c)) { for (var i = 0; i < c.length; i++) { append(el, c[i]); } return; }
    if (c.nodeType) { el.appendChild(c); return; }
    el.appendChild(document.createTextNode(String(c)));
  }
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) { return; }
        if (k === "class") { el.className = v; }
        else if (k === "text") { el.textContent = v; }
        else if (k.slice(0, 2) === "on" && typeof v === "function") { el.addEventListener(k.slice(2), v); }
        else if (v === true) { el.setAttribute(k, ""); }
        else { el.setAttribute(k, String(v)); }
      });
    }
    for (var i = 2; i < arguments.length; i++) { append(el, arguments[i]); }
    return el;
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs) {
    var el = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, String(attrs[k])); });
    return el;
  }
  function clear(el) { while (el.firstChild) { el.removeChild(el.firstChild); } }
  function stClass(state) { return /^[a-z]+$/.test(String(state)) ? "s-" + state : "s-other"; }
  function spinChar() { return SPIN.charAt(Math.floor(Date.now() / 125) % SPIN.length); }
  function logo(cls, size) { return h("img", { class: cls, src: "/logo.svg", alt: "", width: size, height: size, "aria-hidden": "true" }); }
  function chip(label, pressed, onclick, extra) {
    return h("button", { class: "chip" + (extra ? " " + extra : ""), type: "button", "aria-pressed": pressed ? "true" : "false", onclick: onclick },
      h("span", { class: "pill" }, label));
  }

  // ---------------------------------------------------------------- state
  var token = null;
  try { token = window.localStorage.getItem(TOKEN_KEY); } catch (e) { token = null; }
  var tokenSaved = token !== null;
  var authFailed = false;
  var inApp = false;
  var gen = 0;
  var clockOffset = 0;
  var metaTimer = null;
  var metaFails = 0;
  var metaBusy = false;
  var meta = null;
  var metaRev = null;
  var agents = null;
  var listEtag = null;
  var filter = null;
  var selId = null;
  var menuSec = null;
  var menuPushed = false;
  var drafts = {};
  var feedbackDrafts = {};
  var pinned = true;
  var pendKey = null;
  var limKey = null;
  var refineOpen = false;
  var lastOk = 0;
  var D = {};
  var U = {};
  var M = {};
  var C = null;
  var sheet = null;
  var starting = null;
  var swipeOpen = null;       // the id of the agent whose row is swiped open
  var swipeDrag = false;      // a finger is dragging a row: the list is not rebuilt meanwhile
  var swipeStale = false;     // ... and a rebuild is due once it lets go
  var quitting = {};
  var reviving = false;
  var ns = { loaded: false, profiles: [], dirs: [], profile: null, dir: null, filter: "", query: "", busy: false, err: null, found: [], searching: false, timer: null };
  var modelsCache = {};

  // ---------------------------------------------------------------- small helpers
  function limitOf(name) {
    var v = meta && meta.limits && meta.limits[name];
    return typeof v === "number" && v > 0 ? v : DEFAULT_LIMITS[name];
  }
  function readStored() {
    try { return window.localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }
  function saveToken() {
    if (tokenSaved || !token) { return; }
    tokenSaved = true;
    try { window.localStorage.setItem(TOKEN_KEY, token); } catch (e) { /* private mode: in memory only */ }
  }
  function nowServer() { return Date.now() / 1000 + clockOffset; }
  function fmtAge(sec) {
    var s = Math.max(Math.floor(sec), 0);
    if (s < 60) { return s + "s"; }
    if (s < 3600) { return Math.floor(s / 60) + "m"; }
    if (s < 86400) { return Math.floor(s / 3600) + "h" + ("0" + Math.floor((s % 3600) / 60)).slice(-2) + "m"; }
    return Math.floor(s / 86400) + "d";
  }
  function fmtDur(sec) {
    var s = Math.floor(sec);
    if (s < 60) { return "<1m"; }
    if (s < 3600) { return Math.floor(s / 60) + "m"; }
    if (s < 86400) { return Math.floor(s / 3600) + "h" + ("0" + Math.floor((s % 3600) / 60)).slice(-2) + "m"; }
    return Math.floor(s / 86400) + "d" + ("0" + Math.floor((s % 86400) / 3600)).slice(-2) + "h";
  }
  function fmtSize(n) {
    if (typeof n !== "number") { return ""; }
    if (n < 1024) { return n + " B"; }
    if (n < 1048576) { return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB"; }
    return (n / 1048576).toFixed(1) + " MB";
  }
  function fmtTok(n) {
    if (typeof n !== "number") { return ""; }
    if (n < 1000) { return String(n); }
    if (n < 1e6) { return Math.round(n / 1000) + "k"; }
    return (n / 1e6).toFixed(n < 1e7 ? 2 : 1).replace(/\.?0+$/, "") + "M";
  }
  function fmtGiB(b) { return (b / 1073741824).toFixed(b < 1.07e10 ? 1 : 0) + " GiB"; }
  function fmtTime(ts) {
    if (typeof ts !== "number") { return ""; }
    var d = new Date(ts * 1000);
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function resetText(ts) {
    var left = ts - nowServer();
    return left > 0 ? "resets in " + fmtDur(left) : "reset due";
  }
  // "5-hour limit · resets in 2h05m": the countdown is kept current by refreshAges
  function limitLine(lim) {
    var out = [];
    var win = lim.window === "five_hour" ? "5-hour" : (lim.window === "seven_day" ? "weekly" : "");
    if (win) { out.push(win); }
    if (typeof lim.reset_at === "number") { out.push(win ? " \u00b7 " : "", h("span", { "data-reset": String(lim.reset_at) }, resetText(lim.reset_at))); }
    return out;
  }
  function ageNode(a) {
    if (typeof a.since === "number") { return h("span", { "data-since": String(a.since) }, fmtAge(nowServer() - a.since)); }
    return a.age ? h("span", null, a.age) : null;
  }
  function refreshAges() {
    if (document.hidden || !inApp) { return; }
    var els = document.querySelectorAll("[data-since]");
    var now = nowServer();
    for (var i = 0; i < els.length; i++) {
      var s = parseFloat(els[i].getAttribute("data-since"));
      if (isFinite(s)) {
        var t = fmtAge(now - s);
        if (els[i].textContent !== t) { els[i].textContent = t; }
      }
    }
    var rs = document.querySelectorAll("[data-reset]");
    for (var j = 0; j < rs.length; j++) {
      var r = parseFloat(rs[j].getAttribute("data-reset"));
      if (isFinite(r)) {
        var rt = resetText(r);
        if (rs[j].textContent !== rt) { rs[j].textContent = rt; }
      }
    }
  }
  function composeMode(a) {
    var can = (a && a.can) || {};
    return can.prompt ? "prompt" : (can.keys ? "answer" : "none");
  }
  function levelClass(pct) { return pct >= 90 ? "g-err" : (pct >= 70 ? "g-warn" : "g-ok"); }
  function gauge(pct, wide, label) {
    var p = typeof pct === "number" && isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
    var fill = h("i");
    fill.style.width = p + "%";
    return h("span", { class: "gauge " + levelClass(p) + (wide ? " wide" : ""), role: "progressbar", "aria-label": label || null, "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(p)) }, fill);
  }
  function stateInfo(state) {
    var s = (meta && meta.states && meta.states[state]) || STATE_DEFAULTS[state] || { glyph: "\u203a", label: String(state) };
    return { glyph: s.glyph || "\u203a", label: s.label || String(state) };
  }
  function setGlyph(el, state) {
    var si = stateInfo(state);
    var spin = state === "working" && !REDUCED;
    el.className = "glyph " + stClass(state) + (spin ? " spin" : "");
    el.textContent = spin ? spinChar() : si.glyph;
  }
  function glyphEl(state) {
    var el = h("span", { "aria-hidden": "true" });
    setGlyph(el, state);
    return el;
  }
  function findAgent(id) {
    if (!agents || id === null) { return null; }
    for (var i = 0; i < agents.length; i++) { if (String(agents[i].id) === String(id)) { return agents[i]; } }
    return null;
  }
  function pendingSummary(p) {
    if (p.kind === "approval") { return "approval: " + (p.tool || "") + (p.summary ? " \u2014 " + p.summary : ""); }
    if (p.kind === "ask") { return "question: " + (p.question || ""); }
    if (p.kind === "plan_review") { return "plan review" + (p.title ? ": " + p.title : ""); }
    return "needs you";
  }
  function isBusy(a) { return !!a && (a.state === "working" || a.state === "busy" || a.state === "attention"); }

  // ---------------------------------------------------------------- toasts and the confirm dialog
  function toast(msg, isErr) {
    var host = document.getElementById("toasts");
    var t = h("div", { class: "toast" + (isErr ? " err" : "") }, h("span", { class: "tt" }, "\u2b21 Hivegrid"), h("span", { class: "msg" }, msg));
    host.appendChild(t);
    window.setTimeout(function () { if (t.parentNode) { t.parentNode.removeChild(t); } }, isErr ? 5000 : 3200);
  }
  var confirmDone = null;
  function ask(text, okLabel) {
    return new Promise(function (resolve) {
      var dlg = document.getElementById("confirm");
      if (typeof dlg.showModal !== "function") { resolve(window.confirm(text)); return; }
      document.getElementById("confirm-text").textContent = text;
      document.getElementById("confirm-ok").textContent = okLabel || "Send";
      confirmDone = resolve;
      dlg.showModal();
    });
  }
  function answerConfirm(ok) {
    var dlg = document.getElementById("confirm");
    var f = confirmDone;
    confirmDone = null;
    if (dlg.open) { dlg.close(); }
    if (f) { f(ok); }
  }

  // ---------------------------------------------------------------- API: every request has a timeout
  function api(path, opt) {
    opt = opt || {};
    var g = gen;
    var headers = { "Authorization": "Bearer " + token };
    var init = { method: "GET", headers: headers, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" };
    if (opt.etag) { headers["If-None-Match"] = opt.etag; }
    if (opt.body !== undefined) {
      init.method = "POST";
      headers["Content-Type"] = "application/json";
      headers["X-Hivegrid-Remote"] = "1";
      init.body = JSON.stringify(opt.body);
    }
    var ctrl = new AbortController();
    init.signal = ctrl.signal;
    if (opt.loop) { opt.loop.ctrl = ctrl; }
    var timer = window.setTimeout(function () { ctrl.abort(); }, opt.timeout || 12000);
    var p = fetch(path, init).then(function (r) {
      if (r.status === 401) {
        if (g === gen) { rejected("The token was rejected. Enter a new one or retry."); }
        return { status: 401, data: null, etag: null };
      }
      if (r.status === 304 || (r.status >= 200 && r.status < 300)) { saveToken(); }
      var retry = parseFloat(r.headers.get("Retry-After"));
      if (r.status === 304) { return { status: 304, data: null, etag: opt.etag, retry: retry }; }
      return r.text().then(function (t) {
        var d = null;
        try { d = t ? JSON.parse(t) : null; } catch (e) { d = null; }
        return { status: r.status, data: d, etag: r.headers.get("ETag"), retry: retry };
      });
    });
    p.then(function () { window.clearTimeout(timer); }, function () { window.clearTimeout(timer); });
    return p;
  }
  function errText(r) {
    if (r && r.data && r.data.message) { return String(r.data.message); }
    return "request failed (" + (r ? r.status : "?") + ")";
  }
  // A write: resolves to the response when it worked, else null (the reason is toasted). A good write refreshes at once.
  function post(path, body, okMsg) {
    return api(path, { body: body, timeout: 15000 }).then(function (r) {
      if (r.status === 401) { return null; }
      if (r.status >= 200 && r.status < 300) { if (okMsg) { toast(okMsg); } refreshNow(); return r; }
      toast(errText(r), true);
      return null;
    }, function () { toast("no connection", true); return null; });
  }

  // ---------------------------------------------------------------- live loops: long-poll, abort timeouts, backoff, watchdog
  var loops = [];
  function Loop(step) {
    var L = { wanted: false, run: 0, fails: 0, timer: null, ctrl: null, lastDone: Date.now(), step: step };
    function halt() {
      L.run += 1;
      window.clearTimeout(L.timer);
      if (L.ctrl) { try { L.ctrl.abort(); } catch (e) { /* already done */ } L.ctrl = null; }
    }
    function cycle(run) {
      if (!L.wanted || run !== L.run || document.hidden) { return; }
      var t0 = Date.now();
      var result;
      try { result = L.step(L, run); } catch (e) { result = Promise.reject(e); }
      Promise.resolve(result).then(function (r) { return r || {}; }, function () { return { fail: true }; }).then(function (r) {
        if (run !== L.run) { return; }
        L.lastDone = Date.now();
        L.fails = r.fail ? L.fails + 1 : 0;
        var delay = 0;
        if (r.fail) { delay = Math.min(5000, 500 * Math.pow(2, L.fails - 1)); }
        else if (r.quick && Date.now() - t0 < 400) { delay = 1000; }
        if (r.retry && r.retry > 0) { delay = Math.max(delay, Math.min(r.retry, 30) * 1000); }
        updateConn();
        L.timer = window.setTimeout(function () { cycle(run); }, delay);
      });
    }
    L.go = function () {
      halt();
      if (!L.wanted || document.hidden) { return; }
      L.lastDone = Date.now();
      cycle(L.run);
    };
    L.start = function () { L.wanted = true; L.fails = 0; L.go(); };
    L.stop = function () { L.wanted = false; halt(); };
    L.pause = function () { halt(); };
    loops.push(L);
    return L;
  }
  var listLoop = Loop(listStep);
  var chatLoop = Loop(chatStep);
  var lastKick = 0;
  function kickAll() {
    if (!inApp || document.hidden) { return; }
    var t = Date.now();
    if (t - lastKick < 1500) { return; }
    lastKick = t;
    loops.forEach(function (L) { if (L.wanted) { L.go(); } });
    if (menuSec) { menuTick(true); }
  }
  function refreshNow() {
    lastKick = Date.now();
    if (listLoop.wanted) { listLoop.go(); }
    if (chatLoop.wanted) { chatLoop.go(); }
  }
  window.setInterval(function () {
    if (document.hidden || !inApp) { return; }
    var t = Date.now();
    loops.forEach(function (L) { if (L.wanted && t - L.lastDone > 40000) { L.go(); } });
    updateConn();
  }, 5000);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { loops.forEach(function (L) { L.pause(); }); return; }
    lastKick = 0;
    refreshAges();
    kickAll();
  });
  window.addEventListener("pageshow", function () { lastKick = 0; kickAll(); });
  window.addEventListener("online", function () { lastKick = 0; kickAll(); });
  window.addEventListener("focus", kickAll);

  function updateConn() {
    if (!U.conn) { return; }
    var stale = Date.now() - listLoop.lastDone > (WAIT + 4) * 1000;
    var bad = stale || listLoop.fails > 0 || (chatLoop.wanted && chatLoop.fails > 0);
    if (nomux) { setConn("nomux"); return; }
    setConn(bad || !lastOk ? "wait" : "live");
  }
  var nomux = false;
  function setConn(kind) {
    var map = { live: ["conn-live", "live"], wait: ["conn-wait", "reconnecting\u2026"], nomux: ["conn-down", "no window"] };
    var m = map[kind] || map.wait;
    U.conn.className = "status-chip " + m[0];
    U.conn.textContent = m[1];
    if (D.conn) { D.conn.className = "status-chip " + m[0]; D.conn.textContent = m[1]; D.conn.hidden = kind === "live"; }
    if (M.conn) { M.conn.textContent = kind === "live" ? "live" : m[1]; }
  }

  function listStep(L, run) {
    return api("/api/agents?wait=" + WAIT, { etag: listEtag, timeout: (WAIT + 8) * 1000, loop: L }).then(function (r) {
      if (run !== L.run || r.status === 401) { return {}; }
      if (r.status === 304) { lastOk = Date.now(); nomux = false; return { quick: true, retry: r.retry }; }
      if (r.status === 200 && r.data) {
        lastOk = Date.now();
        nomux = false;
        listEtag = r.etag || (r.data.rev ? '"' + r.data.rev + '"' : null);
        if (r.data.meta_rev && r.data.meta_rev !== metaRev) { loadMeta(); }
        applyAgents(r.data);
        return {};
      }
      if (r.status === 503) { nomux = true; return { fail: true, retry: r.retry }; }
      return { fail: true, retry: r.retry };
    });
  }

  function loadMeta() {
    if (metaBusy || !token || authFailed) { return Promise.resolve(); }
    metaBusy = true;
    window.clearTimeout(metaTimer);
    return api("/api/meta").then(function (r) {
      metaBusy = false;
      if (r.status === 200 && r.data) {
        meta = r.data;
        metaRev = r.data.meta_rev || null;
        metaFails = 0;
        applyMeta();
      } else if (r.status !== 401) {
        retryMeta();
      }
    }, function () { metaBusy = false; retryMeta(); });
  }
  function retryMeta() {
    if (authFailed || !token) { return; }
    metaFails += 1;
    window.clearTimeout(metaTimer);
    metaTimer = window.setTimeout(loadMeta, Math.min(5000, 1000 * Math.pow(2, metaFails)));
  }
  function applyMeta() {
    var app = document.getElementById("app");
    app.classList.toggle("ro", !!meta.readonly);
    if (U.ro) { U.ro.hidden = !meta.readonly; }
    if (U.termBtn) { U.termBtn.hidden = !meta.terminal; }
    routeTerm();
    if (agents) { renderChips(); renderList(); updateDetail(); }
    if (menuSec === "about") { renderMenu(); }
  }

  // ---------------------------------------------------------------- login
  function showLogin(msg, prefill) {
    inApp = false;
    var app = document.getElementById("app");
    clear(app);
    app.className = "";
    app.style.height = "";
    document.title = "Hivegrid";
    var input = h("input", { class: "field", type: "password", autocomplete: "current-password", placeholder: "access token", "aria-label": "Access token", autocapitalize: "off", autocorrect: "off", spellcheck: "false" });
    input.value = prefill || "";
    var err = h("p", { class: "login-error", role: "alert" }, msg || "");
    var form = h("form", { onsubmit: function (ev) {
      ev.preventDefault();
      var v = input.value.trim();
      if (!v) { return; }
      token = v;
      tokenSaved = false;
      start();
    } }, input, h("button", { class: "btn primary", type: "submit" }, "Connect"),
      readStored() !== null ? h("button", { class: "btn", type: "button", onclick: clearToken }, "Clear stored token") : null, err);
    app.appendChild(h("div", { class: "login" },
      h("div", { class: "login-card" },
        logo("login-logo", 96),
        h("h1", { class: "login-title" }, "\u2b21 Hivegrid"),
        h("p", { class: "login-sub" }, "Remote control for your agents. Enter the access token (hivegrid-remote token)."),
        form,
        h("div", { class: "login-foot" }, "the token stays on this device"))));
    input.focus();
  }
  // A rejected token is kept (prefilled, stored) until a new one is accepted or the user clears it.
  function rejected(msg) {
    if (authFailed) { return; }
    authFailed = true;
    gen += 1;
    stopAll();
    showLogin(msg, token);
  }
  function stopAll() {
    loops.forEach(function (L) { L.stop(); });
    window.clearTimeout(metaTimer);
    window.clearTimeout(menuTimer);
    closeTerm();
    inApp = false;
  }
  function clearToken() {
    gen += 1;
    stopAll();
    token = null;
    tokenSaved = false;
    try { window.localStorage.removeItem(TOKEN_KEY); } catch (e) { /* nothing stored */ }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    showLogin("Token cleared.", "");
  }

  // ---------------------------------------------------------------- shell
  function buildApp() {
    var app = document.getElementById("app");
    clear(app);
    app.className = "";
    inApp = true;
    U.count = h("span", { class: "count" });
    U.conn = h("span", { class: "status-chip conn-wait", role: "status" }, "reconnecting\u2026");
    U.ro = h("span", { class: "status-chip ro-chip", hidden: true }, "read-only");
    U.menuBtn = h("button", { class: "btn ghost icon", type: "button", "aria-label": "Menu", "aria-haspopup": "dialog", onclick: function () { openMenu("usage"); } }, "\u2261");
    U.termBtn = h("a", { class: "btn ghost icon", href: "#/term", "aria-label": "Terminal", hidden: !(meta && meta.terminal) }, ">_");
    var top = h("header", { class: "top" }, logo("top-logo", 26), h("span", { class: "brand" }, "Hivegrid"), U.count,
      h("span", { class: "spacer" }), h("span", { class: "top-chips" }, U.ro, U.conn), U.termBtn, U.menuBtn);

    U.chips = h("div", { class: "chips", role: "group", "aria-label": "Filter by state" });
    U.list = h("div", { class: "list" });
    U.foot = h("div", { class: "foot" });
    var listPane = h("section", { class: "list-pane", "aria-label": "Agents" }, U.chips, U.list, U.foot);

    // chat header
    D.back = h("a", { class: "btn ghost icon back", href: "#/", "aria-label": "Back to agents" }, "\u25c0");
    D.glyph = h("span", { class: "glyph", "aria-hidden": "true" });
    D.label = h("span", { class: "label" });
    D.title = h("span", { class: "title" });
    D.conn = h("span", { class: "status-chip conn-wait", role: "status", hidden: true }, "reconnecting\u2026");
    D.more = h("button", { class: "btn ghost icon write", type: "button", "aria-label": "Actions", "aria-haspopup": "dialog", onclick: openActions }, "\u22ef");
    D.info = h("div", { class: "cinfo" });
    D.head = h("header", { class: "chead" }, h("div", { class: "chead-row" }, D.back, D.glyph, D.label, D.title, D.conn, D.more), D.info);
    D.ctx = h("div", { class: "cctx", hidden: true });

    // transcript
    D.older = h("div", { class: "older", hidden: true });
    D.items = h("div", { class: "items" });
    D.working = h("div", { class: "working", hidden: true, role: "status" });
    D.files = h("div", { class: "files", "aria-label": "Files sent by this agent" });
    D.inner = h("div", { class: "chat-inner" }, D.older, D.items, D.files, D.working);
    D.chat = h("div", { class: "chat", tabindex: "0", role: "log", "aria-label": "Conversation", "aria-live": "off", onscroll: onChatScroll }, D.inner);
    D.tail = h("pre", { class: "tail", tabindex: "0", "aria-label": "Agent screen" });
    D.tailbox = h("div", { class: "tailbox", hidden: true, onscroll: onChatScroll }, D.tail);
    D.screenNote = h("div", { class: "screen-note", hidden: true }, "No session file for this agent: showing its screen.");
    D.latest = h("button", { class: "latest", type: "button", hidden: true, onclick: function () { scrollBottom(); } }, "\u2193 latest");
    D.chatbox = h("div", { class: "chatbox" }, D.screenNote, D.chat, D.tailbox, D.latest);

    // pending card, strip, composer
    D.pend = h("div", { class: "pcard write", hidden: true, role: "group", "aria-label": "Needs your answer" });
    D.ackBtn = h("button", { class: "btn", type: "button", onclick: sendAck }, "\u2713 acknowledge");
    D.strip = h("div", { class: "strip write", hidden: true }, h("span", { class: "grow" }, "finished"), D.ackBtn);
    D.lcard = h("div", { class: "lcard", hidden: true, role: "group", "aria-label": "Usage limit and account" });
    D.ta = h("textarea", { class: "field", rows: "1", placeholder: "Message", "aria-label": "Message", enterkeyhint: "send", autocapitalize: "sentences", oninput: onPromptInput, onkeydown: function (ev) {
      var fine = window.matchMedia && window.matchMedia("(pointer: fine)").matches;
      if (ev.key === "Enter" && !ev.isComposing && ((fine && !ev.shiftKey) || ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submit(); }
    } });
    D.send = h("button", { class: "btn primary send", type: "button", onclick: submit }, "send");
    D.plus = h("button", { class: "btn icon", type: "button", "aria-label": "Actions", "aria-haspopup": "dialog", onclick: openActions }, "+");
    D.note = h("p", { class: "note", hidden: true });
    D.compose = h("div", { class: "compose write" }, D.note, D.plus, D.ta, D.send);
    D.body = h("div", { class: "d-body", hidden: true });
    D.body.appendChild(D.ctx);
    D.body.appendChild(D.chatbox);
    D.body.appendChild(D.lcard);
    D.body.appendChild(D.pend);
    D.body.appendChild(D.strip);
    D.body.appendChild(D.compose);
    D.empty = h("div", { class: "d-empty" });
    var detailPane = h("section", { class: "detail-pane", "aria-label": "Agent chat" }, D.head, D.body, D.empty);

    app.appendChild(top);
    app.appendChild(h("div", { class: "split" }, listPane, detailPane));
    buildMenu();
    app.appendChild(M.root);
    app.classList.toggle("ro", !!(meta && meta.readonly));
    U.ro.hidden = !(meta && meta.readonly);
  }

  // ---------------------------------------------------------------- agent list
  function applyAgents(data) {
    agents = (data.agents || []).slice().sort(function (a, b) {
      return (a.rank - b.rank) || ((a.tab || 0) - (b.tab || 0)) || ((a.num || 0) - (b.num || 0));
    });
    if (typeof data.now === "number") { clockOffset = data.now - Date.now() / 1000; }
    var needs = agents.filter(function (a) { return a.state === "attention" || a.state === "limit"; }).length;
    document.title = (needs > 0 ? "(" + needs + ") " : "") + "Hivegrid";
    clear(U.count);
    U.count.appendChild(document.createTextNode("\u00b7 " + agents.length));
    U.count.appendChild(h("span", { class: "wide-only" }, agents.length === 1 ? " agent" : " agents"));
    renderChips();
    renderList();
    updateDetail();
    if (sheet && sheet.refresh) { sheet.refresh(); }
    if (starting && findAgent(starting.id)) {
      var id = starting.id;
      starting = null;
      ns.busy = false;
      setHash(buildHash(id, null), false);
    }
  }
  function renderChips() {
    var by = {};
    agents.forEach(function (a) { by[a.state] = (by[a.state] || 0) + 1; });
    var order = STATE_ORDER.filter(function (s) { return by[s]; });
    Object.keys(by).forEach(function (s) { if (order.indexOf(s) < 0) { order.push(s); } });
    if (filter && !by[filter]) { filter = null; }
    clear(U.chips);
    U.chips.appendChild(chip(["all ", h("span", { class: "n" }, agents.length)], filter === null, function () { filter = null; renderChips(); renderList(); }));
    order.forEach(function (s) {
      var si = stateInfo(s);
      U.chips.appendChild(chip([h("span", { class: "st " + stClass(s), "aria-hidden": "true" }, si.glyph), si.label, h("span", { class: "n" }, by[s])], filter === s, function () {
        filter = filter === s ? null : s;
        renderChips();
        renderList();
      }));
    });
    U.chips.hidden = agents.length === 0;
  }
  function renderList() {
    if (swipeDrag) { swipeStale = true; return; }
    var keep = U.list.scrollTop;
    clear(U.list);
    var shown = agents.filter(function (a) { return !filter || a.state === filter; });
    var numw = 2;
    agents.forEach(function (a) { numw = Math.max(numw, String(a.label).length); });
    U.list.style.setProperty("--numw", numw + "ch");
    if (shown.length === 0) {
      U.list.appendChild(h("div", { class: "empty" },
        h("strong", null, agents.length === 0 ? "No agents running" : "Nothing in this state"),
        agents.length === 0 ? "Start omp in a Hivegrid pane, or open a new session from the menu." : "Tap the all chip to see every agent."));
    }
    shown.forEach(function (a) {
      var si = stateInfo(a.state);
      var pend = a.pending ? pendingSummary(a.pending) : "";
      var sub = [h("span", { class: "st" }, si.label)];
      if (a.state === "limit" && a.limit) { var ll = limitLine(a.limit); if (ll.length) { sub.push(" \u00b7 ", ll); } }
      if (a.switch && a.switch.state === "running") { sub.push(" \u00b7 moving to ", a.switch.to || "another account"); }
      [a.cwd, a.state === "limit" && a.limit ? "" : a.reason].forEach(function (x) { if (x) { sub.push(" \u00b7 ", x); } });
      var sel = String(a.id) === String(selId);
      var row = h("a", { class: "row " + stClass(a.state) + (sel ? " sel" : ""), href: "#/a/" + encodeURIComponent(a.id), "aria-current": sel ? "true" : null },
        h("span", { class: "mark", "aria-hidden": "true" }, "\u258e"),
        glyphEl(a.state),
        h("span", { class: "num" }, a.label),
        h("span", { class: "name" }, a.title || "(untitled)"),
        h("span", { class: "meta" }, ageNode(a)),
        h("span", { class: "sub" }, sub),
        pend ? h("span", { class: "pend" }, pend) : null);
      U.list.appendChild(swipeable(a, row));
    });
    U.list.scrollTop = keep;
    clear(U.foot);
    var needs = agents.filter(function (a) { return a.state === "attention"; }).length;
    var limited = agents.filter(function (a) { return a.state === "limit"; }).length;
    U.foot.appendChild(h("button", { class: "write", type: "button", onclick: function () { openMenu("new"); } }, h("span", { class: "k" }, "+"), "new session"));
    U.foot.appendChild(h("span", { class: "spacer" }));
    if (needs > 0) { U.foot.appendChild(h("span", { class: "st s-attention" }, needs + " needs you")); }
    if (limited > 0) { U.foot.appendChild(h("span", { class: "st s-limit" }, (needs > 0 ? "\u00b7 " : "") + limited + " limit")); }
    if (needs + limited === 0) { U.foot.appendChild(h("span", null, "tap an agent to open")); }
  }

  // ---------------------------------------------------------------- routing
  function takeFragment() {
    var m = /^#t=(.+)$/.exec(window.location.hash);
    if (!m) { return false; }
    try { token = decodeURIComponent(m[1]); } catch (e) { token = m[1]; }
    tokenSaved = false;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return true;
  }
  function parseHash() {
    var m = /^#\/(?:a\/([^\/?#]+))?(?:\/?menu(?:\/([a-z]+))?)?$/.exec(window.location.hash);
    var id = null;
    var sec = null;
    if (m) {
      if (m[1]) { try { id = decodeURIComponent(m[1]); } catch (e) { id = null; } }
      if (/^#\/(?:a\/[^\/?#]+\/?)?menu/.test(window.location.hash)) {
        sec = m[2] && SECTIONS.some(function (s) { return s[0] === m[2]; }) ? m[2] : "usage";
      }
    }
    return { id: id, sec: sec };
  }
  function buildHash(id, sec) {
    return "#/" + (id !== null && id !== undefined ? "a/" + encodeURIComponent(id) : "") + (sec ? (id !== null && id !== undefined ? "/" : "") + "menu/" + sec : "");
  }
  function setHash(hash, replace) {
    if (replace) { window.history.replaceState(null, "", hash); route(); }
    else if (window.location.hash === hash) { route(); }
    else { window.location.hash = hash; }
  }
  function openMenu(sec) {
    if (menuSec) { setHash(buildHash(selId, sec), true); return; }
    menuPushed = true;
    setHash(buildHash(selId, sec), false);
  }
  function closeMenu() {
    if (!menuSec) { return; }
    if (menuPushed) { menuPushed = false; window.history.back(); }
    else { setHash(buildHash(selId, null), true); }
  }
  function route() {
    if (!D.ta || !inApp) { return; }
    var r = parseHash();
    var changed = r.id !== selId;
    if (changed) {
      if (selId !== null) { drafts[selId] = D.ta.value; }
      selId = r.id;
      pendKey = null;
      refineOpen = false;
      closeSheet(true);
      D.ta.value = selId !== null ? (drafts[selId] || "") : "";
      growPrompt();
      document.getElementById("app").classList.toggle("has-detail", selId !== null);
      openChat(selId);
    }
    if (r.sec !== menuSec) {
      menuSec = r.sec;
      M.root.hidden = !menuSec;
      if (!menuSec) { menuPushed = false; window.clearTimeout(menuTimer); if (U.menuBtn) { U.menuBtn.focus({ preventScroll: true }); } }
      else { renderMenu(); menuTick(true); }
    }
    if (changed && agents) { renderList(); updateDetail(); }
    routeTerm();
  }

  // ---------------------------------------------------------------- terminal: its script and styles load when it is first opened
  var termView = null;
  var termOpening = false;
  var termAssets = null;
  function loadTermAssets() {
    if (window.HGTermView) { return Promise.resolve(); }
    if (termAssets) { return termAssets; }
    termAssets = new Promise(function (resolve, reject) {
      if (!document.getElementById("term-css")) { document.head.appendChild(h("link", { id: "term-css", rel: "stylesheet", href: "/term.css" })); }
      var left = 2;
      ["/term.js", "/term-view.js"].forEach(function (src) {
        var s = document.createElement("script");
        s.src = src;
        s.async = false;
        s.onload = function () { left -= 1; if (left === 0) { resolve(); } };
        s.onerror = function () { reject(new Error(src)); };
        document.head.appendChild(s);
      });
    });
    termAssets.catch(function () { termAssets = null; });
    return termAssets;
  }
  function termWanted() { return inApp && window.location.hash === "#/term" && !!(meta && meta.terminal); }
  function leaveTerm() { setHash("#/", true); }
  function openTerm() {
    termOpening = true;
    loadTermAssets().then(function () {
      termOpening = false;
      if (!termWanted() || termView) { return; }
      termView = window.HGTermView.open({ api: api, toast: toast, ask: ask, close: leaveTerm });
    }, function () {
      termOpening = false;
      toast("The terminal could not be loaded.", true);
      if (window.location.hash === "#/term") { leaveTerm(); }
    });
  }
  function closeTerm() {
    if (!termView) { return; }
    termView.destroy();
    termView = null;
    if (U.termBtn && inApp) { U.termBtn.focus({ preventScroll: true }); }
  }
  function routeTerm() {
    if (!inApp) { return; }
    if (termWanted()) { if (!termView && !termOpening) { openTerm(); } }
    else if (termView) { closeTerm(); }
    if (meta && !meta.terminal && window.location.hash === "#/term") { leaveTerm(); }
  }

  // ---------------------------------------------------------------- chat state
  function newChat(id) {
    return { id: id, map: {}, rev: null, source: null, mode: "chat", hasOlder: false, olderLoaded: false, loadingOlder: false,
      state: null, reason: null, nodes: {}, tailEtag: null, tailN: 0, loaded: false, gone: false, empty: null, blobs: {}, fileSig: "", fileNodes: {} };
  }
  function openChat(id) {
    chatLoop.stop();
    var C0 = C;
    C = newChat(id);
    pinned = true;
    if (C0 && C0.blobs) { Object.keys(C0.blobs).forEach(function (k) { if (C0.blobs[k].url) { URL.revokeObjectURL(C0.blobs[k].url); } }); }
    clear(D.items);
    clear(D.files);
    D.tail.textContent = "";
    D.older.hidden = true;
    D.latest.hidden = true;
    D.chat.hidden = false;
    D.tailbox.hidden = true;
    D.screenNote.hidden = true;
    if (id !== null) { chatLoop.start(); }
  }
  function chatStep(L, run) {
    var c = C;
    if (!c || c.id === null) { return Promise.resolve({}); }
    var id = c.id;
    var url = "/api/agents/" + encodeURIComponent(id);
    var probe = c.mode === "tail" && c.tailN % 8 === 7;
    if (c.mode === "tail" && !probe) {
      c.tailN += 1;
      return api(url + "/tail?lines=" + TAIL_LINES + "&wait=" + WAIT, { etag: c.tailEtag, timeout: (WAIT + 8) * 1000, loop: L }).then(function (r) {
        if (run !== L.run || c !== C || r.status === 401) { return {}; }
        if (r.status === 304) { return { quick: true, retry: r.retry }; }
        if (r.status === 200 && r.data && Array.isArray(r.data.lines)) {
          c.tailEtag = r.etag || (r.data.rev ? '"' + r.data.rev + '"' : null);
          setTail(r.data.lines);
          return {};
        }
        if (r.status === 404) { c.gone = true; updateDetail(); }
        return { fail: true, retry: r.retry };
      });
    }
    var long = !!c.rev && c.mode === "chat";
    var q = "?limit=" + CHAT_LIMIT + (long ? "&rev=" + encodeURIComponent(c.rev) + "&wait=" + WAIT : "");
    if (probe) { c.tailN += 1; }
    return api(url + "/chat" + q, { timeout: long ? (WAIT + 8) * 1000 : 12000, loop: L }).then(function (r) {
      if (run !== L.run || c !== C || r.status === 401) { return {}; }
      if (r.status === 200 && r.data) {
        if (r.data.unchanged) { return { quick: true, retry: r.retry }; }
        if (Array.isArray(r.data.items)) { applyChat(c, r.data); return {}; }
      }
      if (r.status === 404) { c.gone = true; updateDetail(); }
      return { fail: true, retry: r.retry };
    });
  }
  // ---------------------------------------------------------------- files sent with hivegrid-send
  // The bytes are fetched with the token in a header (never in a URL) and shown from a blob: URL; names and notes are text only.
  function fileBlob(c, f) {
    var have = c.blobs[f.id];
    if (have) { return have.promise; }
    var ent = { url: null, promise: null };
    ent.promise = fetch("/api/agents/" + encodeURIComponent(c.id) + "/files/" + encodeURIComponent(f.id),
      { headers: { "Authorization": "Bearer " + token }, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" }).then(function (r) {
      if (!r.ok) { throw new Error("status " + r.status); }
      return r.blob();
    }).then(function (b) {
      ent.url = URL.createObjectURL(b);
      return ent.url;
    });
    ent.promise.catch(function () { delete c.blobs[f.id]; });
    c.blobs[f.id] = ent;
    return ent.promise;
  }
  function saveFile(c, f) {
    fileBlob(c, f).then(function (url) {
      var a = h("a", { href: url, download: f.name || "file" });
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }, function () { toast("could not load " + (f.name || "the file"), true); });
  }
  function buildFile(c, f) {
    var card = h("div", { class: "fcard" },
      h("div", { class: "fhead" }, h("span", { class: "fname" }, (f.image ? "\u25a3 " : "\u2193 ") + (f.name || "file")),
        h("span", { class: "fmeta" }, fmtSize(f.size) + (f.ts ? " \u00b7 " + fmtTime(f.ts) : ""))));
    if (f.note) { card.appendChild(h("div", { class: "fnote" }, f.note)); }
    if (f.image) {
      var img = h("img", { class: "fimg", alt: f.name || "image sent by the agent", decoding: "async" });
      img.onload = function () { if (pinned && C === c) { scrollBottom(); } };
      fileBlob(c, f).then(function (url) {
        img.src = url;
        img.onclick = function () { window.open(url, "_blank", "noopener"); };
      }, function () { img.replaceWith(h("div", { class: "fnote" }, "the image could not be loaded")); });
      card.appendChild(img);
    }
    card.appendChild(h("div", { class: "fbtns" }, h("button", { class: "btn", type: "button", onclick: function () { saveFile(c, f); } }, "save")));
    return card;
  }
  function renderFiles(c, files) {
    if (c !== C) { return; }
    var sig = JSON.stringify(files.map(function (f) { return [f.id, f.name, f.note]; }));
    if (sig === c.fileSig) { return; }
    c.fileSig = sig;
    var seen = {};
    var prev = null;
    files.slice().reverse().forEach(function (f) {      // oldest first, like the chat
      seen[f.id] = true;
      var node = c.fileNodes[f.id] || (c.fileNodes[f.id] = buildFile(c, f));
      var expected = prev ? prev.nextSibling : D.files.firstChild;
      if (node !== expected) { D.files.insertBefore(node, expected); }
      prev = node;
    });
    Object.keys(c.fileNodes).forEach(function (k) {
      if (!seen[k]) { var n = c.fileNodes[k]; if (n.parentNode) { n.parentNode.removeChild(n); } delete c.fileNodes[k]; }
    });
    if (pinned) { scrollBottom(); }
  }
  function applyChat(c, data) {
    c.loaded = true;
    renderFiles(c, Array.isArray(data.files) ? data.files : []);
    c.gone = false;
    c.state = data.state;
    c.reason = data.reason;
    if (data.source === "screen") {
      if (c.mode !== "tail") { c.mode = "tail"; c.tailN = 0; c.tailEtag = null; c.rev = null; }
      showMode(c);
      updateDetail();
      return;
    }
    if (c.mode === "tail") { c.mode = "chat"; showMode(c); }
    c.rev = data.rev;
    if (c.gen !== data.gen) {      // another parse of the file (it was replaced or rewritten): item numbers no longer match
      c.gen = data.gen;
      c.map = {};
      Object.keys(c.nodes).forEach(function (k) { var n = c.nodes[k].node; if (n.parentNode) { n.parentNode.removeChild(n); } });
      c.nodes = {};
      c.olderLoaded = false;
    }
    var items = data.items;
    var keep = {};
    if (items.length) {
      var min = items[0].n;
      Object.keys(c.map).forEach(function (k) { if (c.map[k].n < min) { keep[k] = c.map[k]; } });
    }
    c.map = keep;
    items.forEach(function (it) { c.map[it.n] = it; });
    if (!c.olderLoaded) { c.hasOlder = !!data.has_older; }
    c.queued = Array.isArray(data.pending_prompts) ? data.pending_prompts : [];
    renderTranscript(true);
    updateDetail();
  }
  function showMode(c) {
    var tail = c.mode === "tail";
    D.chat.hidden = tail;
    D.tailbox.hidden = !tail;
    D.screenNote.hidden = !tail;
  }
  function loadOlder() {
    var c = C;
    if (!c || c.loadingOlder || !c.hasOlder) { return; }
    var ns2 = Object.keys(c.map).map(function (k) { return c.map[k].n; });
    if (!ns2.length) { return; }
    var oldest = Math.min.apply(null, ns2);
    c.loadingOlder = true;
    renderOlder();
    api("/api/agents/" + encodeURIComponent(c.id) + "/chat?limit=" + CHAT_LIMIT + "&before=" + oldest, { timeout: 12000 }).then(function (r) {
      c.loadingOlder = false;
      if (c !== C) { return; }
      if (r.status === 200 && r.data && Array.isArray(r.data.items)) {
        c.olderLoaded = true;
        c.hasOlder = !!r.data.has_older;
        r.data.items.forEach(function (it) { c.map[it.n] = it; });
        renderTranscript(false);
      } else if (r.status !== 401) {
        toast("could not load older messages", true);
      }
      renderOlder();
    }, function () { c.loadingOlder = false; renderOlder(); toast("could not load older messages", true); });
  }
  function renderOlder() {
    var c = C;
    D.older.hidden = !(c && (c.hasOlder || c.loadingOlder));
    clear(D.older);
    if (c && c.loadingOlder) { D.older.appendChild(h("span", null, "loading older\u2026")); }
    else if (c && c.hasOlder) { D.older.appendChild(h("button", { class: "btn ghost", type: "button", onclick: loadOlder }, "\u2191 older messages")); }
  }

  // ---------------------------------------------------------------- transcript
  function itemSig(it) { return JSON.stringify([it.role, it.kind, it.text, it.tool, it.queued || null, it.failed || null]); }
  function renderTranscript(live) {
    var c = C;
    if (!c) { return; }
    var wasPinned = pinned;
    var oldH = D.chat.scrollHeight;
    var oldTop = D.chat.scrollTop;
    var list = Object.keys(c.map).map(function (k) { return c.map[k]; }).sort(function (a, b) { return a.n - b.n; });
    // prompts sent from the phone that omp has not written to the session yet: greyed bubbles after the last real item, in send order
    (c.queued || []).forEach(function (p, i) { list.push({ n: "q" + i, role: "user", kind: "text", text: p.text, ts: p.ts, tool: null, queued: true, failed: !!p.failed }); });
    var seen = {};
    var prev = null;
    list.forEach(function (it) {
      var key = String(it.n);
      var sig = itemSig(it);
      var ent = c.nodes[key];
      if (!ent || ent.sig !== sig) {
        var node = buildItem(it, ent ? ent.node : null);
        if (ent && ent.node.parentNode === D.items) { D.items.replaceChild(node, ent.node); }
        c.nodes[key] = ent = { sig: sig, node: node };
      }
      seen[key] = true;
      var expected = prev ? prev.nextSibling : D.items.firstChild;
      if (ent.node !== expected) { D.items.insertBefore(ent.node, expected); }
      prev = ent.node;
    });
    Object.keys(c.nodes).forEach(function (k) {
      if (!seen[k]) { var n = c.nodes[k].node; if (n.parentNode) { n.parentNode.removeChild(n); } delete c.nodes[k]; }
    });
    if (list.length === 0) {
      if (!c.empty) { c.empty = h("div", { class: "it it-note" }, h("span", null, "no messages yet")); }
      if (!c.empty.parentNode) { D.items.appendChild(c.empty); }
    } else if (c.empty && c.empty.parentNode) { c.empty.parentNode.removeChild(c.empty); }
    renderOlder();
    if (live === false && !wasPinned) {
      // history added above: keep what the reader looks at in place
      D.chat.scrollTop = oldTop + (D.chat.scrollHeight - oldH);
    } else if (wasPinned) {
      scrollBottom();
    } else {
      D.latest.hidden = false;
    }
  }
  function scrollBottom() {
    D.chat.scrollTop = D.chat.scrollHeight;
    D.tailbox.scrollTop = D.tailbox.scrollHeight;
    pinned = true;
    D.latest.hidden = true;
  }
  function onChatScroll(ev) {
    var box = ev.currentTarget;
    pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
    D.latest.hidden = pinned;
    if (box === D.chat && box.scrollTop < 120 && C && C.hasOlder && !C.loadingOlder && C.loaded) { loadOlder(); }
  }
  function setTail(lines) {
    D.tail.textContent = lines.join("\n");
    if (pinned) { scrollBottom(); }
  }
  function buildItem(it, old) {
    var oldDet = old && old.querySelector ? old.querySelector("details") : null;
    var wasOpen = oldDet ? oldDet.open : false;
    var el;
    if (it.kind === "limit") {
      var lr = it.limit && typeof it.limit.reset_at === "number" ? " \u00b7 resets " + fmtTime(it.limit.reset_at) : "";
      el = h("div", { class: "it it-limit" }, "\u2298 usage limit reached" + lr);
    } else if (it.role === "note" || it.kind === "note") {
      el = h("div", { class: "it it-note" }, h("span", null, it.text || ""));
    } else if (it.kind === "image") {
      el = h("div", { class: "it it-image" }, it.text || "[image]");
    } else if (it.kind === "tool" && it.tool) {
      var t = it.tool;
      var st = t.status === "error" ? "error" : (t.status === "running" ? "running" : "ok");
      var out = t.output ? t.output : (st === "running" ? "running\u2026" : "(no output)");
      el = h("div", { class: "it it-tool" },
        h("details", { class: "tool t-" + st },
          h("summary", null,
            h("span", { class: "tname" }, t.name || "tool"),
            h("span", { class: "tsum" }, t.summary || ""),
            h("span", { class: "tst" + (st === "running" && !REDUCED ? " spin" : "") }, st === "ok" ? "\u2713" : (st === "error" ? "\u2717" : (REDUCED ? "\u21bb" : spinChar())))),
          h("pre", { class: "out" + (t.output ? "" : " empty-out") }, out)));
    } else if (it.kind === "thinking") {
      el = h("div", { class: "it it-think" },
        h("details", { class: "think" }, h("summary", null, "thinking"), h("div", { class: "think-body" }, it.text || "")));
    } else if (it.role === "user" && it.queued) {
      var qnote = (it.failed ? "\u2717 not delivered" : "\u29d6 queued, waiting for the agent") + (it.ts ? " \u00b7 " + fmtTime(it.ts) : "");
      el = h("div", { class: "it it-user it-queued" + (it.failed ? " failed" : "") }, h("div", { class: "bubble" }, it.text || ""), h("div", { class: "ts" }, qnote));
    } else if (it.role === "user") {
      el = h("div", { class: "it it-user" }, h("div", { class: "bubble" }, it.text || ""), it.ts ? h("div", { class: "ts" }, fmtTime(it.ts)) : null);
    } else {
      el = h("div", { class: "it it-asst" }, renderMd(it.text || ""));
    }
    if (wasOpen) { var d = el.querySelector("details"); if (d) { d.open = true; } }
    return el;
  }

  // ---------------------------------------------------------------- safe markdown subset (DOM nodes only)
  function mdInline(text, parent) {
    // every run is bounded, so a long run of "[" or "`" cannot make the scan quadratic
    var re = /`([^`\n]{1,1000})`|\*\*([^*\n]{1,500}?)\*\*|\[([^\]\n]{1,300})\]\(([^)\s]{1,500})\)/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) { parent.appendChild(document.createTextNode(text.slice(last, m.index))); }
      if (m[1] !== undefined) { parent.appendChild(h("code", null, m[1])); }
      else if (m[2] !== undefined) { parent.appendChild(h("strong", null, m[2])); }
      else { parent.appendChild(h("span", { class: "lnk" }, m[3])); parent.appendChild(h("span", { class: "dim" }, " (" + m[4] + ")")); }
      last = re.lastIndex;
    }
    if (last < text.length) { parent.appendChild(document.createTextNode(text.slice(last))); }
    return parent;
  }
  var RE_FENCE = /^\s*(```|~~~)\s*([A-Za-z0-9_+#.\-]*)\s*$/;
  var RE_HEAD = /^(#{1,6})\s+(.*)$/;
  var RE_LI = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  var RE_HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
  function startsBlock(l) { return RE_FENCE.test(l) || RE_HEAD.test(l) || RE_LI.test(l) || RE_HR.test(l) || /^\s*>/.test(l) || /^\s*\|/.test(l); }
  function renderMd(text) {
    var root = h("div", { class: "md" });
    var lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    var i = 0;
    while (i < lines.length) {
      var ln = lines[i];
      var m;
      if (!ln.trim()) { i += 1; continue; }
      if ((m = RE_FENCE.exec(ln))) {
        var fence = m[1];
        var buf = [];
        i += 1;
        while (i < lines.length && lines[i].trim().indexOf(fence) !== 0) { buf.push(lines[i]); i += 1; }
        i += 1;
        root.appendChild(h("div", { class: "codeblock" }, m[2] ? h("span", { class: "lang" }, m[2]) : null, h("pre", null, h("code", null, buf.join("\n")))));
      } else if ((m = RE_HEAD.exec(ln))) {
        root.appendChild(mdInline(m[2], h("h" + m[1].length)));
        i += 1;
      } else if (RE_HR.test(ln)) {
        root.appendChild(h("hr"));
        i += 1;
      } else if (/^\s*>/.test(ln)) {
        var q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
        root.appendChild(mdInline(q.join("\n"), h("blockquote")));
      } else if (/^\s*\|/.test(ln)) {
        var tb = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { tb.push(lines[i]); i += 1; }
        root.appendChild(h("pre", { class: "tbl" }, tb.join("\n")));
      } else if ((m = RE_LI.exec(ln))) {
        var wrap = h("div", { class: "list-md" });
        while (i < lines.length && (m = RE_LI.exec(lines[i]))) {
          var depth = Math.min(Math.floor(m[1].replace(/\t/g, "  ").length / 2), 4);
          var ordered = /\d/.test(m[2]);
          wrap.appendChild(h("div", { class: "li d" + depth }, h("span", { class: "b" }, ordered ? m[2] : "\u2022"), mdInline(m[3], h("div"))));
          i += 1;
        }
        root.appendChild(wrap);
      } else {
        var para = [];
        while (i < lines.length && lines[i].trim() && (para.length === 0 || !startsBlock(lines[i]))) { para.push(lines[i]); i += 1; }
        root.appendChild(mdInline(para.join("\n"), h("p")));
      }
    }
    return root;
  }

  // ---------------------------------------------------------------- chat header, context bar, composer state
  function updateDetail() {
    var app = document.getElementById("app");
    var a = findAgent(selId);
    var live = selId !== null && !!a;
    D.head.hidden = selId === null;
    D.body.hidden = !live;
    D.empty.hidden = selId !== null && !!a ? true : false;
    if (selId === null) {
      clear(D.empty);
      D.empty.appendChild(logo("big", 72));
      D.empty.appendChild(h("div", null, "Select an agent to open its chat."));
      D.head.hidden = true;
      return;
    }
    if (!a) {
      D.head.hidden = false;
      setGlyph(D.glyph, "idle");
      D.glyph.textContent = "";
      D.label.textContent = "";
      D.title.textContent = "";
      clear(D.info);
      clear(D.empty);
      D.empty.appendChild(h("div", null, agents ? "This agent is gone." : "Loading\u2026"));
      if (agents) { D.empty.appendChild(h("p", null, h("a", { class: "btn", href: "#/" }, "\u25c0 Back to agents"))); }
      if (agents && chatLoop.wanted) { chatLoop.stop(); }
      return;
    }
    if (agents && !chatLoop.wanted && C && C.id === selId) { chatLoop.start(); }
    app.classList.toggle("has-detail", true);
    D.head.className = "chead " + stClass(a.state);
    setGlyph(D.glyph, a.state);
    D.label.textContent = a.label;
    D.title.textContent = a.title || "(untitled)";
    var si = stateInfo(a.state);
    clear(D.info);
    append(D.info, [h("span", { class: "st" }, si.label), ageNode(a), a.cwd ? h("span", { class: "path" }, a.cwd) : null]);
    renderCtx(a);
    renderPending(a);
    renderLimit(a);
    var can = a.can || {};
    var mode = composeMode(a);
    D.ta.disabled = mode === "none";
    D.ta.placeholder = mode === "answer" ? "Answer" : (mode === "none" ? "Agent exited" : "Message");
    D.ta.setAttribute("aria-label", mode === "answer" ? "Answer" : "Message");
    D.ta.setAttribute("maxlength", String(limitOf(mode === "answer" ? "key_text" : "prompt")));
    D.send.textContent = mode === "answer" ? "answer" : "send";
    D.send.disabled = mode === "none" || !D.ta.value.trim();
    D.plus.disabled = false;
    D.strip.hidden = !can.ack;
    var note = "";
    if (mode === "answer" && a.pending && a.pending.kind !== "plan_review") { note = "Tap above, or type an answer: it is pasted, then Enter."; }
    else if (a.state === "limit") { note = "The account's limit is reached: switch account, or wait for the reset."; }
    D.note.hidden = !note;
    D.note.textContent = note;
    // live indicator
    var working = a.state === "working" || a.state === "busy";
    D.working.hidden = !working || !(C && C.mode === "chat");
    if (working) {
      clear(D.working);
      var g = h("span", { class: "glyph spin", "aria-hidden": "true" }, REDUCED ? "\u21bb" : spinChar());
      g.style.color = "var(--accent)";
      append(D.working, [g, a.state === "busy" ? "busy\u2026" : "working\u2026", a.reason ? h("span", { class: "why" }, a.reason) : null]);
    }
    if (pinned) { D.chat.scrollTop = D.chat.scrollHeight; D.tailbox.scrollTop = D.tailbox.scrollHeight; }
  }
  function renderCtx(a) {
    clear(D.ctx);
    var bits = [];
    if (a.model && a.model.name) { bits.push(h("span", { class: "model" }, a.model.name)); }
    if (a.thinking) { bits.push(h("span", { class: "think" }, "think " + a.thinking)); }
    if (a.context && typeof a.context.pct === "number") {
      bits.push(gauge(a.context.pct, false, "Context used"));
      bits.push(h("span", { class: "pct" }, Math.round(a.context.pct) + "%" + (a.context.window ? " " + fmtTok(a.context.tokens) + "/" + fmtTok(a.context.window) : "")));
    }
    if (a.usage && a.usage.text) { bits.push(h("span", { class: "usage" }, a.usage.text)); }
    append(D.ctx, bits);
    D.ctx.hidden = bits.length === 0;
  }
  // The card above the composer: the limit an agent stopped on (with the account switch), or how a switch is going.
  function renderLimit(a) {
    var lim = a.state === "limit" ? a.limit || { label: "usage limit" } : null;
    var sw = a.switch;
    var can = a.can || {};
    var key = JSON.stringify([a.id, a.state, lim, sw, !!can.account, a.profile]);
    if (key === limKey) { return; }
    limKey = key;
    clear(D.lcard);
    if (!lim && !sw) { D.lcard.hidden = true; return; }
    D.lcard.hidden = false;
    var kind = sw && sw.state === "running" ? "run" : (lim || (sw && sw.state === "failed") ? "limit" : "done");
    D.lcard.className = "lcard lc-" + kind;
    if (sw && sw.state === "running") {
      D.lcard.appendChild(h("h3", null, "\u21c4 Moving the chat to " + (sw.to || "another account")));
      D.lcard.appendChild(h("p", null, "omp quits, the chat is copied to the new account and omp resumes it in this pane. This takes a few seconds."));
      return;
    }
    if (sw && sw.state === "failed") {
      D.lcard.appendChild(h("h3", null, "Account switch failed"));
      D.lcard.appendChild(h("p", null, sw.message || "the chat was not moved"));
    } else if (sw && !lim) {
      D.lcard.appendChild(h("h3", null, "Chat moved to " + (sw.to || "the new account")));
      D.lcard.appendChild(h("p", null, sw.message || "omp resumed the chat"));
    }
    if (lim) {
      D.lcard.appendChild(h("h3", null, "\u2298 " + (lim.label || "usage limit") + " reached" + (lim.account ? " on " + lim.account : "")));
      var when = typeof lim.reset_at === "number" ? ["The limit ", h("span", { "data-reset": String(lim.reset_at) }, resetText(lim.reset_at)), " (" + fmtTime(lim.reset_at) + ")."] : ["The reset time is not known."];
      D.lcard.appendChild(h("p", null, ["The agent stopped. "].concat(when)));
      D.lcard.appendChild(h("div", { class: "pbtns write" },
        h("button", { class: "btn primary", type: "button", disabled: !can.account, onclick: function () { openAccounts(a.id); } }, "\u21c4 Switch account\u2026")));
    }
  }

  // ---------------------------------------------------------------- pending approval / question / plan review
  function agentUrl(id, suffix) { return "/api/agents/" + encodeURIComponent(id) + "/" + suffix; }
  function sendKeys(id, keys, confirm, okMsg) {
    var body = { keys: keys };
    if (confirm) { body.confirm = true; }
    return post(agentUrl(id, "keys"), body, okMsg || null);
  }
  function keyBtn(a, label, key, cls, hint) {
    return h("button", { class: "btn key " + (cls || ""), type: "button", disabled: !(a.can && a.can.keys), onclick: function () { sendKeys(a.id, [key], false, null); } },
      hint ? h("kbd", null, hint) : null, label);
  }
  function renderPending(a) {
    var p = a.pending;
    var can = a.can || {};
    var key = JSON.stringify([a.id, p, !!can.plan, !!can.keys, limitOf("feedback")]);
    if (key === pendKey) { return; }
    pendKey = key;
    refineOpen = false;
    clear(D.pend);
    if (!p) { D.pend.hidden = true; return; }
    D.pend.hidden = false;
    if (p.kind === "plan_review") {
      D.pend.appendChild(h("h3", null, "Plan review"));
      if (p.title) { D.pend.appendChild(h("p", { class: "what" }, p.title)); }
      var fb = h("textarea", { class: "field", rows: "3", placeholder: "What should change?", "aria-label": "Refine feedback", maxlength: String(limitOf("feedback")), oninput: function () { feedbackDrafts[a.id] = fb.value; } });
      fb.value = feedbackDrafts[a.id] || "";
      var refine = h("div", { class: "refine", hidden: true }, fb,
        h("button", { class: "btn primary", type: "button", onclick: function () {
          var text = fb.value.trim();
          if (!text) { toast("Write feedback first.", true); return; }
          post(agentUrl(a.id, "plan"), { choice: "refine", feedback: text }, "Feedback sent").then(function (r) { if (r) { delete feedbackDrafts[a.id]; } });
        } }, "send feedback"));
      var row = h("div", { class: "pbtns" });
      (p.choices || []).forEach(function (c) {
        row.appendChild(h("button", { class: "btn" + (c.id === "execute" ? " primary" : (c.id === "reject" ? " bad" : "")), type: "button", disabled: !can.plan, onclick: function () {
          if (c.id === "refine") { refineOpen = !refineOpen; refine.hidden = !refineOpen; if (refineOpen) { fb.focus(); } return; }
          post(agentUrl(a.id, "plan"), { choice: c.id }, "Sent: " + (c.label || c.id));
        } }, c.label || c.id));
      });
      D.pend.appendChild(row);
      D.pend.appendChild(refine);
    } else if (p.kind === "approval") {
      D.pend.appendChild(h("h3", null, "Approval needed"));
      D.pend.appendChild(h("p", { class: "what" }, h("b", null, p.tool || "tool"), p.summary ? "  " + p.summary : ""));
      D.pend.appendChild(h("div", { class: "pbtns" },
        keyBtn(a, "yes", "y", "ok", "y"), keyBtn(a, "no", "n", "bad", "n"),
        keyBtn(a, "enter", "enter", "", "\u21b5"), keyBtn(a, "esc", "esc", "", null)));
    } else if (p.kind === "ask") {
      D.pend.appendChild(h("h3", null, "Question"));
      D.pend.appendChild(h("p", { class: "what" }, p.question || ""));
      var box = h("div", { class: "pbtns" });
      (p.options || []).forEach(function (o, i) {
        if (i > 8) { return; }
        box.appendChild(h("button", { class: "btn key opt", type: "button", disabled: !can.keys, onclick: function () { sendKeys(a.id, [String(i + 1)], false, null); } }, h("kbd", null, String(i + 1)), o));
      });
      box.appendChild(keyBtn(a, "enter", "enter", "", "\u21b5"));
      box.appendChild(keyBtn(a, "esc", "esc", "", null));
      D.pend.appendChild(box);
    } else {
      D.pend.appendChild(h("h3", null, "Waiting for you"));
    }
  }

  // ---------------------------------------------------------------- composer
  function onPromptInput() {
    if (selId !== null) { drafts[selId] = D.ta.value; }
    D.send.disabled = composeMode(findAgent(selId)) === "none" || !D.ta.value.trim();
    growPrompt();
  }
  function growPrompt() {
    D.ta.style.height = "auto";
    D.ta.style.height = Math.min(D.ta.scrollHeight + 2, 160) + "px";
  }
  // Prompt mode posts /prompt; answer mode (a pending approval or question) pastes the text and presses Enter via /keys.
  function submit() {
    if (D.send.disabled) { return; }
    var mode = composeMode(findAgent(selId));
    if (mode === "none") { return; }
    var text = D.ta.value.replace(/\r\n?/g, "\n");
    if (!text.trim()) { return; }
    var max = limitOf(mode === "answer" ? "key_text" : "prompt");
    if (text.length > max) { toast("Too long: at most " + max + " characters.", true); return; }
    var id = selId;
    D.send.disabled = true;
    pinned = true;
    var req = mode === "answer"
      ? post(agentUrl(id, "keys"), { text: text, keys: ["enter"] }, null)
      : post(agentUrl(id, "prompt"), { text: text }, null);
    req.then(function (r) {
      D.send.disabled = false;
      if (r && id === selId && D.ta.value.replace(/\r\n?/g, "\n") === text) { D.ta.value = ""; delete drafts[id]; growPrompt(); }
      if (id === selId) { onPromptInput(); }
    });
  }
  function sendAck() {
    if (selId === null) { return; }
    post(agentUrl(selId, "ack"), {}, "Acknowledged");
  }

  // ---------------------------------------------------------------- sheets (actions, models)
  function closeSheet(quiet) {
    if (!sheet) { return; }
    var s = sheet;
    sheet = null;
    if (s.el.parentNode) { s.el.parentNode.removeChild(s.el); }
    if (!quiet && s.opener && s.opener.focus) { try { s.opener.focus({ preventScroll: true }); } catch (e) { /* gone */ } }
  }
  function openSheet(opt) {
    var opener = sheet ? sheet.opener : document.activeElement;
    closeSheet(true);
    var body = h("div", { class: "sbody" });
    var head = h("div", { class: "shead" },
      opt.back ? h("button", { class: "btn ghost icon", type: "button", "aria-label": "Back", onclick: opt.back }, "\u25c0") : null,
      h("div", { class: "ttl" }, h("b", null, "\u2b21 " + opt.title), opt.sub ? " \u00b7 " + opt.sub : ""),
      h("button", { class: "btn ghost icon", type: "button", "aria-label": "Close", onclick: function () { closeSheet(); } }, "\u2715"));
    var el = h("div", { class: "overlay" },
      h("div", { class: "scrim", onclick: function () { closeSheet(); } }),
      h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": opt.title }, head, body));
    document.body.appendChild(el);
    sheet = { el: el, body: body, opener: opener, refresh: null };
    opt.render(sheet);
    var first = body.querySelector("button:not(:disabled), input");
    if (first && first.tagName === "INPUT") { first.focus(); }
  }
  function aRow(o) {
    return h("button", { class: "arow" + (o.cur ? " cur" : "") + (o.danger ? " danger" : ""), type: "button", disabled: !!o.disabled, onclick: o.onclick },
      h("span", { class: "mark", "aria-hidden": "true" }, "\u258e"),
      h("span", { class: "g", "aria-hidden": "true" }, o.glyph || ""),
      h("span", { class: "t" }, o.title),
      h("span", { class: "m" }, o.meta || ""),
      o.hint ? h("span", { class: "h" }, o.hint) : null);
  }
  function agentLine(a) { return a.label + " " + (a.title || ""); }
  function openActions() {
    var a = findAgent(selId);
    if (!a) { return; }
    var id = a.id;
    openSheet({ title: "Actions", sub: agentLine(a), render: function (s) {
      function draw() {
        var cur = findAgent(id);
        if (!cur) { closeSheet(); return; }
        var can = cur.can || {};
        var busy = isBusy(cur);
        clear(s.body);
        var modelHint = can.model ? (busy ? "wait until the agent is idle" : null) : (cur.state === "exited" ? "the agent has exited" : "restart omp to enable");
        s.body.appendChild(aRow({ glyph: "\u25c7", title: "Change model", meta: cur.model && cur.model.name ? cur.model.name : "", hint: modelHint, disabled: !can.model || busy, onclick: function () { openModels(id); } }));
        s.body.appendChild(thinkingBlock(cur, can, busy, modelHint));
        s.body.appendChild(aRow({ glyph: "\u238b", title: "Interrupt", meta: "Esc", hint: can.keys ? null : "the agent has exited", disabled: !can.keys, onclick: function () { closeSheet(); sendKeys(id, ["esc"], false, "Interrupt sent"); } }));
        s.body.appendChild(aRow({ glyph: "\u25a4", title: "Compact", meta: "/compact", hint: can.prompt ? null : "not while the agent waits for an answer", disabled: !can.prompt, onclick: function () { closeSheet(); post(agentUrl(id, "prompt"), { text: "/compact" }, "Compact sent"); } }));
        s.body.appendChild(aRow({ glyph: "\u2713", title: "Acknowledge", hint: can.ack ? null : "only when the agent is done or idle, or its limit has reset", disabled: !can.ack, onclick: function () { closeSheet(); post(agentUrl(id, "ack"), {}, "Acknowledged"); } }));
        s.body.appendChild(aRow({ glyph: "\u21c4", title: "Switch account", meta: cur.profile || "", hint: can.account ? null : "only while the agent is idle or stopped on its limit, on an omp profile", disabled: !can.account, onclick: function () { openAccounts(id); } }));
        s.body.appendChild(aRow({ glyph: "\u25cb", title: "Quit session", meta: "Ctrl-D", danger: true, hint: can.quit ? "it leaves the list; History can start it again" : (cur.state === "exited" ? "the agent has exited" : "only when the agent is idle, done or stopped on its limit"), disabled: !can.quit, onclick: function () {
          ask("Quit \u201c" + agentLine(cur) + "\u201d? omp gets Ctrl-D and the session leaves the list. You can start it again from History.", "Quit session").then(function (ok) { if (ok) { closeSheet(); quitSession(id); } });
        } }));
        s.body.appendChild(aRow({ glyph: "\u2718", title: "Ctrl-C", meta: "kill", danger: true, hint: can.keys ? null : "the agent has exited", disabled: !can.keys, onclick: function () {
          ask("Send Ctrl-C to \u201c" + agentLine(cur) + "\u201d?", "Send Ctrl-C").then(function (ok) { if (ok) { closeSheet(); sendKeys(id, ["ctrl-c"], true, "Ctrl-C sent"); } });
        } }));
      }
      s.refresh = draw;
      draw();
      if (!modelsCache[id]) { fetchModels(id).then(function () { if (sheet === s) { draw(); } }); }
    } });
  }
  function fetchModels(id) {
    return api(agentUrl(id, "models"), { timeout: 10000 }).then(function (r) {
      if (r.status === 200 && r.data) { modelsCache[id] = r.data; }
      return modelsCache[id] || null;
    }, function () { return modelsCache[id] || null; });
  }
  function thinkingBlock(a, can, busy, hint) {
    var info = modelsCache[a.id];
    var levels = info && Array.isArray(info.levels) && info.levels.length ? info.levels : DEFAULT_LEVELS;
    var curLevel = a.thinking || (info && info.thinking) || null;
    var off = !can.model || busy || !info;
    var seg = h("div", { class: "seg", role: "group", "aria-label": "Thinking level" });
    levels.forEach(function (lv) {
      seg.appendChild(h("button", { type: "button", "aria-pressed": curLevel === lv ? "true" : "false", disabled: off, onclick: function () {
        closeSheet();
        post(agentUrl(a.id, "thinking"), { level: lv }, "Thinking: " + lv);
      } }, lv));
    });
    var why = !can.model ? hint : (busy ? "wait until the agent is idle" : (!info ? "loading\u2026" : null));
    return h("div", { class: "seg-wrap" },
      h("div", { class: "lbl" }, h("span", null, "\u25d0 Thinking level"), h("span", null, why || (curLevel || ""))), seg);
  }
  function openModels(id) {
    var a = findAgent(id);
    if (!a) { return; }
    var q = "";
    openSheet({ title: "Model", sub: agentLine(a), back: function () { openActions(); }, render: function (s) {
      var list = h("div");
      var filt = h("div", { class: "sfilter", hidden: true }, h("input", { class: "field", type: "search", placeholder: "filter models", "aria-label": "Filter models", autocapitalize: "off", autocorrect: "off", spellcheck: "false", oninput: function (ev) { q = ev.target.value.toLowerCase(); draw(); } }));
      s.body.appendChild(filt);
      s.body.appendChild(list);
      var busyNow = false;
      function draw() {
        var info = modelsCache[id];
        clear(list);
        if (!info) { list.appendChild(h("div", { class: "note-line" }, "loading models\u2026")); return; }
        var cur = findAgent(id);
        var models = (info.models || []).filter(function (m) { return !q || (m.id + " " + m.name).toLowerCase().indexOf(q) >= 0; });
        filt.hidden = (info.models || []).length <= 10;
        var current = (cur && cur.model && cur.model.id) || info.current;
        var providers = [];
        var groups = {};
        models.forEach(function (m) {
          var p = m.provider || (m.id.indexOf("/") > 0 ? m.id.split("/")[0] : "other");
          if (!groups[p]) { groups[p] = []; providers.push(p); }
          groups[p].push(m);
        });
        if (!models.length) { list.appendChild(h("div", { class: "note-line" }, "no models")); }
        providers.forEach(function (p) {
          list.appendChild(h("div", { class: "group-h" }, p));
          groups[p].forEach(function (m) {
            list.appendChild(aRow({ cur: m.id === current, glyph: m.id === current ? "\u25cf" : "\u25cb", title: m.name || m.id, meta: m.reasoning ? "reasoning" : "", hint: m.id, disabled: busyNow || isBusy(cur), onclick: function () {
              busyNow = true;
              draw();
              post(agentUrl(id, "model"), { model: m.id }, null).then(function (r) {
                busyNow = false;
                if (r) {
                  toast(r.data && r.data.confirmed === false ? "Model change sent, not confirmed yet: " + (m.name || m.id) : "Model: " + (m.name || m.id));
                  closeSheet();
                } else if (sheet === s) { draw(); }
              });
            } }));
          });
        });
        if (isBusy(cur)) { list.appendChild(h("div", { class: "note-line err" }, "wait until the agent is idle to change the model")); }
      }
      s.refresh = draw;
      draw();
      fetchModels(id).then(function (info) {
        if (sheet !== s) { return; }
        if (!info) { clear(list); list.appendChild(h("div", { class: "note-line err" }, "the model list is not available for this agent")); return; }
        draw();
      });
    } });
  }
  function quotaText(ac) {
    var bits = [];
    [["five_hour", "5h"], ["seven_day", "7d"]].forEach(function (q) {
      var v = ac[q[0]];
      if (v && typeof v.pct === "number") { bits.push(q[1] + " " + Math.round(v.pct) + "%"); }
    });
    return bits.length ? bits.join(" \u00b7 ") : (ac.state === "ok" || !ac.state ? "no quota data" : ac.state);
  }
  function openAccounts(id) {
    var a = findAgent(id);
    if (!a) { return; }
    var info = null;
    var failed = null;
    var busyNow = false;
    openSheet({ title: "Switch account", sub: agentLine(a), back: function () { openActions(); }, render: function (s) {
      function pick(ac) {
        var cur = findAgent(id);
        if (!cur || busyNow) { return; }
        ask("Move \u201c" + agentLine(cur) + "\u201d to account " + ac.label + "? omp quits, the chat is copied there and omp resumes it in this pane.", "Switch account").then(function (ok) {
          if (!ok) { return; }
          busyNow = true;
          draw();
          post(agentUrl(id, "account"), { profile: ac.profile, confirm: true }, null).then(function (r) {
            busyNow = false;
            if (r) { toast("Moving the chat to " + ac.label + "\u2026"); closeSheet(); } else if (sheet === s) { draw(); }
          });
        });
      }
      function draw() {
        clear(s.body);
        if (failed) { s.body.appendChild(h("div", { class: "note-line err" }, failed)); return; }
        if (!info) { s.body.appendChild(h("div", { class: "note-line" }, "loading accounts\u2026")); return; }
        var now = info.current && (info.current.label || info.current.profile);
        s.body.appendChild(h("div", { class: "note-line" }, (now ? "Now on " + now + ". " : "") + "omp quits, the chat is copied to the account you pick and omp resumes it in this pane."));
        if (!info.can && info.why) { s.body.appendChild(h("div", { class: "note-line err" }, info.why)); }
        (info.accounts || []).forEach(function (ac) {
          s.body.appendChild(aRow({ glyph: "\u25cb", title: ac.label, meta: quotaText(ac), disabled: !info.can || busyNow, onclick: function () { pick(ac); } }));
        });
        if (!(info.accounts || []).length && info.can !== false) { s.body.appendChild(h("div", { class: "note-line" }, "no other account is paired with an omp profile")); }
      }
      s.refresh = draw;
      draw();
      api(agentUrl(id, "accounts"), { timeout: 10000 }).then(function (r) {
        if (sheet !== s) { return; }
        if (r.status === 200 && r.data) { info = r.data; } else { failed = errText(r); }
        draw();
      }, function () { if (sheet === s) { failed = "no connection"; draw(); } });
    } });
  }


  // ---------------------------------------------------------------- menu: usage, new session, system, about
  var menuTimer = null;
  function buildMenu() {
    M.tabs = h("div", { class: "menu-tabs", role: "tablist", "aria-label": "Menu sections" });
    M.body = h("div", { class: "menu-body", role: "tabpanel" });
    M.conn = null;
    M.panel = h("div", { class: "menu-panel", role: "dialog", "aria-modal": "true", "aria-label": "Menu" },
      h("div", { class: "menu-head" }, logo("top-logo", 26), h("div", { class: "ttl" }, h("b", null, "Hivegrid"), " \u00b7 menu"),
        h("button", { class: "btn ghost icon", type: "button", "aria-label": "Close menu", onclick: closeMenu }, "\u2715")),
      M.tabs, M.body);
    M.root = h("div", { class: "menu", hidden: true }, h("div", { class: "scrim", onclick: closeMenu }), M.panel);
    M.data = { usage: null, system: null, history: null };
  }
  function renderMenu() {
    if (!menuSec) { return; }
    clear(M.tabs);
    SECTIONS.forEach(function (s) {
      var b = h("button", { class: "chip", type: "button", role: "tab", "aria-selected": menuSec === s[0] ? "true" : "false", onclick: function () { openMenu(s[0]); } }, h("span", { class: "pill" }, s[1]));
      M.tabs.appendChild(b);
    });
    var keep = M.body.scrollTop;
    clear(M.body);
    M.conn = null;
    if (menuSec === "usage") { drawUsage(); }
    else if (menuSec === "new") { drawNew(); }
    else if (menuSec === "history") { drawHistory(); }
    else if (menuSec === "system") { drawSystem(); }
    else { drawAbout(); }
    M.body.scrollTop = keep;
  }
  function menuTick(now) {
    window.clearTimeout(menuTimer);
    if (!menuSec) { return; }
    var sec = menuSec;
    var every = sec === "system" ? 3000 : (sec === "usage" ? 15000 : 0);
    function again() { if (every && menuSec === sec) { menuTimer = window.setTimeout(function () { menuTick(true); }, every); } }
    if (document.hidden || !now) { again(); return; }
    if (sec === "usage") {
      api("/api/accounts", { timeout: 10000 }).then(function (r) {
        if (r.status === 200 && r.data) { M.data.usage = r.data; M.data.usageErr = null; if (typeof r.data.now === "number") { clockOffset = r.data.now - Date.now() / 1000; } }
        else if (r.status !== 401) { M.data.usageErr = errText(r); }
        if (menuSec === sec) { renderMenu(); }
      }, function () { M.data.usageErr = "no connection"; if (menuSec === sec) { renderMenu(); } }).then(again);
    } else if (sec === "system") {
      api("/api/system", { timeout: 10000 }).then(function (r) {
        if (r.status === 200 && r.data) { M.data.system = r.data; M.data.systemErr = null; }
        else if (r.status !== 401) { M.data.systemErr = errText(r); }
        if (menuSec === sec) { renderMenu(); }
      }, function () { M.data.systemErr = "no connection"; if (menuSec === sec) { renderMenu(); } }).then(again);
    } else if (sec === "new") {
      loadOptions();
    } else if (sec === "history") {
      loadHistory(true);
    } else {
      again();
    }
  }

  // usage
  function drawUsage() {
    var d = M.data.usage;
    if (M.data.usageErr) { M.body.appendChild(h("div", { class: "note-line err" }, "usage unavailable: " + M.data.usageErr)); }
    if (!d) { if (!M.data.usageErr) { M.body.appendChild(h("div", { class: "note-line" }, "loading usage\u2026")); } return; }
    M.body.appendChild(h("div", { class: "sec-h" }, "accounts \u00b7 " + (d.accounts || []).length));
    if (!(d.accounts || []).length) { M.body.appendChild(h("div", { class: "note-line" }, "no accounts registered")); }
    (d.accounts || []).forEach(function (ac) {
      var stl = { ok: "ok", expired: "expired", login: "login needed", error: "error", unknown: "no data" }[ac.state] || String(ac.state);
      var card = h("div", { class: "acct" },
        h("div", { class: "acct-top" }, h("span", { class: "mark", "aria-hidden": "true" }, "\u258e"), h("span", { class: "nm" }, ac.label), h("span", { class: "as as-" + (/^[a-z]+$/.test(String(ac.state)) ? ac.state : "unknown") }, stl)));
      [["five_hour", "5h"], ["seven_day", "7d"]].forEach(function (q) {
        var v = ac[q[0]];
        if (!v || typeof v.pct !== "number") { return; }
        card.appendChild(h("div", { class: "qrow" }, h("span", { class: "lab" }, q[1]), gauge(v.pct, true, q[1] + " quota used"), h("span", { class: "pc" }, Math.round(v.pct) + "%"),
          typeof v.reset_at === "number" ? h("span", { class: "rs" }, v.reset_at > nowServer() ? "resets in " + fmtDur(v.reset_at - nowServer()) : "reset due") : null));
      });
      if (!ac.five_hour && !ac.seven_day) { card.appendChild(h("div", { class: "none" }, "no quota data")); }
      var use = [];
      if (ac.agents > 0) { use.push(h("b", null, ac.agents + (ac.agents === 1 ? " agent" : " agents"))); } else { use.push("no running agents"); }
      if (typeof ac.tokens === "number") { use.push(fmtTok(ac.tokens) + " tok"); }
      if (typeof ac.cost === "number") { use.push("$" + ac.cost.toFixed(2) + (ac.cost_kind ? " " + (ac.cost_kind === "estimated" ? "est" : ac.cost_kind) : "")); }
      if (typeof ac.updated_at === "number") { use.push("updated " + fmtAge(nowServer() - ac.updated_at) + " ago"); }
      var useEl = h("div", { class: "use" });
      use.forEach(function (x, i) { if (i) { useEl.appendChild(document.createTextNode(" \u00b7 ")); } append(useEl, x); });
      card.appendChild(useEl);
      M.body.appendChild(card);
    });
    if (d.totals && d.totals.text) { M.body.appendChild(h("div", { class: "tot" }, "running now: ", h("b", null, d.totals.text))); }
  }

  // new session
  function loadOptions() {
    api("/api/sessions/options", { timeout: 10000 }).then(function (r) {
      if (r.status === 200 && r.data) {
        ns.profiles = r.data.profiles || [];
        ns.dirs = r.data.dirs || [];
        ns.loaded = true;
        ns.err = null;
        if (ns.profile === null || !ns.profiles.some(function (p) { return p.label === ns.profile; })) { ns.profile = ns.profiles.length ? ns.profiles[0].label : null; }
        if (ns.dir === null || !ns.dirs.some(function (x) { return x.path === ns.dir; })) {
          var a = findAgent(selId);
          var match = a && ns.dirs.filter(function (x) { return x.path === a.cwd; })[0];
          ns.dir = match ? match.path : null;
        }
      } else if (r.status !== 401) { ns.err = errText(r); }
      if (menuSec === "new") { renderMenu(); }
    }, function () { ns.err = "no connection"; if (menuSec === "new") { renderMenu(); } });
  }
  function drawNew() {
    if (ns.err) { M.body.appendChild(h("div", { class: "note-line err" }, "options unavailable: " + ns.err)); }
    if (!ns.loaded) { if (!ns.err) { M.body.appendChild(h("div", { class: "note-line" }, "loading\u2026")); } return; }
    M.body.appendChild(h("div", { class: "sec-h" }, "account"));
    var opts = h("div", { class: "opts", role: "group", "aria-label": "Account" });
    ns.profiles.forEach(function (p) {
      opts.appendChild(chip(p.label, ns.profile === p.label, function () { ns.profile = p.label; renderMenu(); }));
    });
    M.body.appendChild(opts);
    M.body.appendChild(h("div", { class: "sec-h" }, "directory"));
    var inp = h("input", { class: "field", type: "search", placeholder: "search folders or type a path (~/work/x)", "aria-label": "Search folders", autocapitalize: "off", autocorrect: "off", spellcheck: "false", oninput: function (ev) { ns.query = ev.target.value.trim(); ns.filter = ns.query.toLowerCase(); drawDirs(); searchDirs(); } });
    inp.value = ns.query;
    M.body.appendChild(h("div", { class: "sfilter" }, inp));
    M.dirs = h("div", { class: "dirs", role: "listbox", "aria-label": "Directory" });
    M.body.appendChild(M.dirs);
    drawDirs();
    var ready = ns.dir !== null && ns.profile !== undefined && !ns.busy;
    var go = h("button", { class: "btn primary write", type: "button", disabled: !ready, onclick: startSession }, ns.busy ? "starting\u2026" : "Start session");
    M.body.appendChild(h("div", { class: "startbar write" }, go,
      h("div", { class: "why" }, ns.dir === null ? "choose a directory" : "omp starts in a new pane of the Hivegrid window, then its chat opens here")));
  }
  // The folders of the search box beyond the offered ones: the server looks below the home directory (debounced, newest answer wins).
  function searchDirs() {
    window.clearTimeout(ns.timer);
    var q = ns.query;
    if (q.length < 2) { ns.found = []; ns.searching = false; drawDirs(); return; }
    ns.searching = true;
    ns.timer = window.setTimeout(function () {
      api("/api/sessions/dirs?q=" + encodeURIComponent(q), { timeout: 10000 }).then(function (r) {
        if (q !== ns.query) { return; }
        ns.searching = false;
        ns.found = r.status === 200 && r.data && Array.isArray(r.data.dirs) ? r.data.dirs : [];
        drawDirs();
      }, function () { if (q === ns.query) { ns.searching = false; ns.found = []; drawDirs(); } });
    }, 250);
  }
  function drawDirs() {
    if (!M.dirs) { return; }
    clear(M.dirs);
    var shown = ns.dirs.filter(function (x) { return !ns.filter || (x.label + " " + x.path).toLowerCase().indexOf(ns.filter) >= 0; });
    var have = {};
    shown.forEach(function (x) { have[x.path] = true; });
    (ns.filter ? ns.found : []).forEach(function (x) { if (!have[x.path]) { have[x.path] = true; shown.push(x); } });
    if (ns.dir !== null && !have[ns.dir]) { shown.unshift({ label: ns.dir.split("/").filter(Boolean).pop() || ns.dir, path: ns.dir }); }
    if (!shown.length) { M.dirs.appendChild(h("div", { class: "note-line" }, ns.searching ? "searching\u2026" : "no directories")); }
    shown.forEach(function (x) {
      M.dirs.appendChild(h("button", { class: "drow" + (ns.dir === x.path ? " cur" : ""), type: "button", role: "option", "aria-selected": ns.dir === x.path ? "true" : "false", onclick: function () { ns.dir = x.path; renderMenu(); } },
        h("span", { class: "mark", "aria-hidden": "true" }, "\u258e"), h("span", { class: "nm" }, x.label), h("span", { class: "pth" }, x.path)));
    });
    if (ns.searching && shown.length) { M.dirs.appendChild(h("div", { class: "note-line" }, "searching\u2026")); }
  }
  function startSession() {
    var p = ns.profiles.filter(function (x) { return x.label === ns.profile; })[0];
    if (!p || ns.dir === null || ns.busy) { return; }
    ns.busy = true;
    renderMenu();
    api("/api/sessions", { body: { profile: p.profile, dir: ns.dir }, timeout: 20000 }).then(function (r) {
      if (r.status >= 200 && r.status < 300 && r.data && r.data.ok) {
        toast("Starting a session\u2026");
        starting = { id: r.data.id, t: Date.now() };
        window.setTimeout(function () {
          if (starting && starting.id === r.data.id) { starting = null; ns.busy = false; toast("The new agent has not appeared yet.", true); if (menuSec === "new") { renderMenu(); } }
        }, 30000);
        refreshNow();
        if (findAgent(r.data.id)) { applyAgents({ agents: agents, now: nowServer() }); }
      } else if (r.status !== 401) {
        ns.busy = false;
        toast(errText(r), true);
        renderMenu();
      } else { ns.busy = false; }
    }, function () { ns.busy = false; toast("no connection", true); renderMenu(); });
  }

  // ---------------------------------------------------------------- swipe a list row to quit its session
  var SWIPE_W = 96;
  function setRowOpen(wrap, open) {
    wrap.classList.toggle("open", open);
    var b = wrap.querySelector(".rquit");
    if (b) { b.setAttribute("aria-hidden", open ? "false" : "true"); b.tabIndex = open ? 0 : -1; }
  }
  // A row that can be quit slides left under a finger (touch or pen) and rests open, showing a red Quit button; Quit asks
  // before it does anything. The Actions sheet offers the same for everyone else (mouse, keyboard, screen readers).
  function swipeable(a, row) {
    if (!(a.can && a.can.quit)) { return h("div", { class: "rw" }, row); }
    var id = a.id;
    var open = swipeOpen === id;
    var btn = h("button", { class: "rquit", type: "button", "aria-hidden": open ? "false" : "true", tabindex: open ? "0" : "-1",
      "aria-label": "Quit session " + agentLine(a), onclick: function () { confirmQuit(id); } }, "Quit");
    var wrap = h("div", { class: "rw" + (open ? " open" : "") }, row, btn);
    var sx = 0;
    var sy = 0;
    var base = 0;
    var dx = 0;
    var mode = null;
    var pid = null;
    var blockClick = 0;
    row.addEventListener("pointerdown", function (ev) {
      if ((ev.pointerType !== "touch" && ev.pointerType !== "pen") || mode !== null) { return; }
      sx = ev.clientX;
      sy = ev.clientY;
      base = wrap.classList.contains("open") ? -SWIPE_W : 0;
      dx = base;
      mode = "wait";
      pid = ev.pointerId;
    });
    row.addEventListener("pointermove", function (ev) {
      if (mode === null || ev.pointerId !== pid) { return; }
      var mx = ev.clientX - sx;
      var my = ev.clientY - sy;
      if (mode === "wait") {
        if (Math.abs(my) > 10 && Math.abs(my) >= Math.abs(mx)) { mode = null; return; }      // the list is being scrolled
        if (Math.abs(mx) < 10 || Math.abs(mx) < 1.5 * Math.abs(my)) { return; }              // not clearly sideways yet
        mode = "drag";
        swipeDrag = true;
        wrap.classList.add("drag");
        try { row.setPointerCapture(pid); } catch (e) { /* the pointer is gone */ }
      }
      dx = Math.max(-SWIPE_W - 24, Math.min(0, base + mx));
      row.style.transform = "translateX(" + dx + "px)";
    });
    function end(ev, cancelled) {
      if (mode === null || ev.pointerId !== pid) { return; }
      var was = mode;
      mode = null;
      if (was !== "drag") { return; }
      swipeDrag = false;
      wrap.classList.remove("drag");
      row.style.transform = "";
      blockClick = Date.now() + 400;                       // the click that ends a drag must not open the chat
      var nowOpen = !cancelled && dx < -SWIPE_W / 2;
      setRowOpen(wrap, nowOpen);
      swipeOpen = nowOpen ? id : (swipeOpen === id ? null : swipeOpen);
      if (nowOpen) {
        Array.prototype.forEach.call(U.list.querySelectorAll(".rw.open"), function (w) { if (w !== wrap) { setRowOpen(w, false); } });
      }
      if (swipeStale) { swipeStale = false; renderList(); }
    }
    row.addEventListener("pointerup", function (ev) { end(ev, false); });
    row.addEventListener("pointercancel", function (ev) { end(ev, true); });
    row.addEventListener("click", function (ev) {
      if (Date.now() < blockClick) { ev.preventDefault(); return; }
      if (wrap.classList.contains("open")) { ev.preventDefault(); setRowOpen(wrap, false); swipeOpen = null; }   // a tap closes an open row
    });
    return wrap;
  }
  function confirmQuit(id) {
    var a = findAgent(id);
    if (!a) { return; }
    ask("Quit \u201c" + agentLine(a) + "\u201d? omp gets Ctrl-D and the session leaves the list. You can start it again from History.", "Quit session").then(function (ok) {
      if (ok) { quitSession(id); } else { swipeOpen = null; renderList(); }
    });
  }
  function quitSession(id) {
    if (quitting[id]) { return; }
    quitting[id] = true;
    toast("Quitting\u2026");
    post(agentUrl(id, "quit"), { confirm: true }, "Session quit").then(function (r) {
      delete quitting[id];
      swipeOpen = null;
      if (!r) { renderList(); return; }
      if (String(selId) === String(id)) { setHash("#/", true); }
    }, function () { delete quitting[id]; swipeOpen = null; renderList(); });
  }

  // ---------------------------------------------------------------- history: past sessions, to start one again
  var HIST_PAGE = 30;
  function loadHistory(reset) {
    var d = M.data.history;
    if (reset || !d) { d = M.data.history = { items: [], next: 0, more: false, total: 0, loaded: false, loading: false, err: null }; }
    if (d.loading) { return; }
    d.loading = true;
    api("/api/history?limit=" + HIST_PAGE + "&offset=" + d.next, { timeout: 12000 }).then(function (r) {
      if (M.data.history !== d) { return; }
      d.loading = false;
      if (r.status === 200 && r.data && Array.isArray(r.data.items)) {
        d.items = d.items.concat(r.data.items);
        d.next = (r.data.offset || 0) + (r.data.limit || HIST_PAGE);     // pages are of files: a page may show fewer sessions
        d.more = !!r.data.more;
        d.total = r.data.total || 0;
        d.err = null;
        d.loaded = true;
      } else if (r.status !== 401) { d.err = errText(r); }
      if (menuSec === "history") { renderMenu(); }
    }, function () {
      if (M.data.history !== d) { return; }
      d.loading = false;
      d.err = "no connection";
      if (menuSec === "history") { renderMenu(); }
    });
  }
  function drawHistory() {
    var d = M.data.history;
    if (!d) { M.body.appendChild(h("div", { class: "note-line" }, "loading history\u2026")); return; }
    if (d.err) { M.body.appendChild(h("div", { class: "note-line err" }, "history unavailable: " + d.err)); }
    if (!d.loaded) { if (!d.err) { M.body.appendChild(h("div", { class: "note-line" }, "loading history\u2026")); } return; }
    M.body.appendChild(h("div", { class: "sec-h" }, "past sessions \u00b7 " + d.items.length + (d.more ? "+" : "")));
    if (!d.items.length) { M.body.appendChild(h("div", { class: "note-line" }, "no past sessions")); }
    d.items.forEach(function (it) {
      var meta = [it.dir, it.account, typeof it.ts === "number" ? fmtAge(nowServer() - it.ts) + " ago" : ""].filter(Boolean).join(" \u00b7 ");
      var go = h("button", { class: "btn write", type: "button", disabled: !it.can_revive || reviving, onclick: function () { confirmRevive(it); } }, "Revive");
      var note = it.live ? h("span", { class: "hl" }, "\u25cf open in agent " + (it.agent || "")) : (it.why ? h("span", { class: "why" }, it.why) : null);
      M.body.appendChild(h("div", { class: "hrow" },
        h("div", { class: "ht" }, it.title),
        it.preview ? h("div", { class: "hp" }, it.preview) : null,
        h("div", { class: "hm" }, meta),
        h("div", { class: "hb" }, go, note)));
    });
    if (d.more) {
      M.body.appendChild(h("div", { class: "startbar" }, h("button", { class: "btn", type: "button", disabled: d.loading, onclick: function () { loadHistory(false); } }, d.loading ? "loading\u2026" : "older sessions")));
    }
  }
  function confirmRevive(it) {
    if (reviving) { return; }
    ask("Start \u201c" + it.title + "\u201d again in a new pane on " + it.account + "? omp resumes it in " + (it.dir || "its directory") + ".", "Revive").then(function (ok) {
      if (ok) { reviveSession(it); }
    });
  }
  function reviveSession(it) {
    reviving = true;
    renderMenu();
    api("/api/history/" + encodeURIComponent(it.id) + "/revive", { body: { confirm: true }, timeout: 20000 }).then(function (r) {
      reviving = false;
      if (r.status >= 200 && r.status < 300 && r.data && r.data.ok) {
        toast("Starting the session\u2026");
        starting = { id: r.data.id, t: Date.now() };
        window.setTimeout(function () {
          if (starting && starting.id === r.data.id) { starting = null; toast("The session has not appeared yet.", true); }
        }, 30000);
        refreshNow();
        if (findAgent(r.data.id)) { applyAgents({ agents: agents, now: nowServer() }); }
        return;
      }
      if (r.status !== 401) { toast(errText(r), true); }
      if (menuSec === "history") { loadHistory(true); }            // it may have changed meanwhile (open now, directory gone)
    }, function () { reviving = false; toast("no connection", true); renderMenu(); });
  }

  // system
  function sparkline(vals, cls) {
    var n = vals.length;
    var svg = sv("svg", { class: "spark " + cls, viewBox: "0 0 120 30", preserveAspectRatio: "none", role: "img", "aria-label": "history" });
    if (n < 2) { return svg; }
    var pts = [];
    vals.forEach(function (v, i) {
      var y = 29 - Math.max(0, Math.min(100, v)) * 0.28;
      pts.push((i * 119 / (n - 1)).toFixed(1) + "," + y.toFixed(1));
    });
    svg.appendChild(sv("path", { class: "area", d: "M0,30 L" + pts.join(" L") + " L119,30 Z" }));
    svg.appendChild(sv("polyline", { points: pts.join(" ") }));
    return svg;
  }
  function sysRow(label, pct, detail, hist, key) {
    var vals = (hist || []).map(function (p) { return p[key]; }).filter(function (v) { return typeof v === "number"; });
    var p = typeof pct === "number" ? pct : null;
    var row = h("div", { class: "sysrow " + (p === null ? "" : levelClass(p)) },
      h("div", { class: "hd" }, h("span", { class: "lab" }, label), h("span", { class: "det" }, detail || ""), h("span", { class: "pc" }, p === null ? "\u2014" : Math.round(p) + "%")),
      h("div", { class: "bar" }, gauge(p === null ? 0 : p, true, label)));
    if (vals.length > 1) { row.appendChild(sparkline(vals, "")); }
    return row;
  }
  function drawSystem() {
    var s = M.data.system;
    if (M.data.systemErr) { M.body.appendChild(h("div", { class: "note-line err" }, "metrics unavailable: " + M.data.systemErr)); }
    if (!s) { if (!M.data.systemErr) { M.body.appendChild(h("div", { class: "note-line" }, "loading metrics\u2026")); } return; }
    M.body.appendChild(h("div", { class: "sec-h" }, "this machine \u00b7 history " + (s.history && s.history.length > 1 ? fmtDur(s.history[s.history.length - 1].ts - s.history[0].ts) : "\u2014")));
    M.body.appendChild(sysRow("CPU", s.cpu, s.cores ? s.cores + " cores" : "", s.history, "cpu"));
    M.body.appendChild(sysRow("RAM", s.mem && s.mem.pct, s.mem ? fmtGiB(s.mem.used) + " / " + fmtGiB(s.mem.total) : "", s.history, "ram"));
    if (s.gpu) { M.body.appendChild(sysRow("GPU", s.gpu.util, (s.gpu.name || "") + (s.gpu.mem_total ? " \u00b7 " + fmtGiB(s.gpu.mem_used) + "/" + fmtGiB(s.gpu.mem_total) : ""), s.history, "gpu")); }
    M.body.appendChild(sysRow("Disk", s.disk && s.disk.pct, s.disk ? fmtGiB(s.disk.free) + " free" : "", s.history, "dsk"));
    var kv = h("div", { class: "kv" });
    if (Array.isArray(s.load)) { kv.appendChild(h("span", { class: "k" }, "load")); kv.appendChild(h("span", { class: "v" }, s.load.map(function (x) { return Number(x).toFixed(2); }).join("  "))); }
    if (typeof s.uptime === "number") { kv.appendChild(h("span", { class: "k" }, "uptime")); kv.appendChild(h("span", { class: "v" }, fmtDur(s.uptime))); }
    M.body.appendChild(h("div", { class: "sec-h" }, "load and uptime"));
    M.body.appendChild(kv);
  }

  // about
  function drawAbout() {
    M.conn = h("span", { class: "v" }, listLoop.fails > 0 || !lastOk ? "reconnecting\u2026" : "live");
    var kv = h("div", { class: "kv" });
    function row(k, v) { kv.appendChild(h("span", { class: "k" }, k)); kv.appendChild(v && v.nodeType ? v : h("span", { class: "v" }, v)); }
    row("connection", M.conn);
    row("mode", meta && meta.readonly ? h("span", { class: "v" }, "read-only (writes are refused)") : "read and write");
    row("agents", agents ? String(agents.length) : "\u2014");
    row("api", meta && meta.v ? "v" + meta.v : "\u2014");
    row("server", window.location.host);
    row("token", "kept on this device");
    M.body.appendChild(h("div", { class: "sec-h" }, "this device"));
    M.body.appendChild(kv);
    M.body.appendChild(h("div", { class: "about-act" },
      h("button", { class: "btn", type: "button", onclick: function () { window.location.reload(); } }, "Reload the page"),
      h("button", { class: "btn bad", type: "button", onclick: function () {
        ask("Forget the access token on this device? You will need the #t= link again.", "Log out").then(function (ok) { if (ok) { clearToken(); } });
      } }, "Log out (forget token)")));
  }

  // ---------------------------------------------------------------- spinner, viewport, keys
  window.setInterval(function () {
    if (document.hidden || !inApp || REDUCED) { return; }
    var els = document.querySelectorAll(".spin");
    var c = spinChar();
    for (var i = 0; i < els.length; i++) { if (els[i].textContent !== c) { els[i].textContent = c; } }
  }, 125);
  window.setInterval(refreshAges, 1000);
  function fitViewport() {
    var vv = window.visualViewport;
    var app = document.getElementById("app");
    if (!vv || !app) { return; }
    if (inApp && Math.abs(vv.height - window.innerHeight) > 80) { app.style.height = vv.height + "px"; window.scrollTo(0, 0); }
    else { app.style.height = ""; }
    if (pinned && D.chat) { D.chat.scrollTop = D.chat.scrollHeight; }
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", fitViewport);
    window.visualViewport.addEventListener("scroll", function () { if (window.visualViewport.offsetTop > 0) { window.scrollTo(0, 0); } });
  }
  window.addEventListener("resize", function () { if (pinned && D.chat && inApp) { D.chat.scrollTop = D.chat.scrollHeight; } });
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape" || !inApp) { return; }
    var dlg = document.getElementById("confirm");
    if (dlg.open) { return; }
    if (sheet) { closeSheet(); ev.preventDefault(); }
    else if (menuSec) { closeMenu(); ev.preventDefault(); }
  });

  // ---------------------------------------------------------------- start
  function start() {
    gen += 1;
    stopAll();
    authFailed = false;
    meta = null; metaRev = null; metaBusy = false; metaFails = 0;
    agents = null; listEtag = null; filter = null; clockOffset = 0; lastOk = 0; nomux = false;
    selId = null; menuSec = null; menuPushed = false; starting = null; modelsCache = {};
    ns = { loaded: false, profiles: [], dirs: [], profile: null, dir: null, filter: "", query: "", busy: false, err: null, found: [], searching: false, timer: null };
    closeSheet(true);
    buildApp();
    C = newChat(null);
    route();
    loadMeta();
    listLoop.start();
  }
  window.addEventListener("hashchange", function () {
    if (takeFragment()) { start(); return; }
    if (inApp) { route(); }
  });
  document.getElementById("confirm-cancel").addEventListener("click", function () { answerConfirm(false); });
  document.getElementById("confirm-ok").addEventListener("click", function () { answerConfirm(true); });
  document.getElementById("confirm").addEventListener("close", function () { if (confirmDone) { var f = confirmDone; confirmDone = null; f(false); } });
  takeFragment();
  if (token) { start(); } else { showLogin(""); }
})();
"""

# The seven-hexagon logo of assets/icon.png (geometry and colours of assets/make-icon.py), as SVG.
LOGO_SVG = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="-284 -284 568 568" width="64" height="64" role="img" aria-label="Hivegrid">
<defs><filter id="g" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="19"/></filter>
<filter id="c" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="17"/></filter>
<clipPath id="k"><polygon points="89.1,0.0 44.6,77.2 -44.5,77.2 -89.1,0.0 -44.6,-77.2 44.6,-77.2"/></clipPath></defs>
<polygon points="123.8,0.0 61.9,107.2 -61.9,107.2 -123.8,0.0 -61.9,-107.2 61.9,-107.2" fill="#188cff" fill-opacity=".78" filter="url(#g)"/>
<polygon points="89.1,0.0 44.6,77.2 -44.5,77.2 -89.1,0.0 -44.6,-77.2 44.6,-77.2" fill="#0e84ff" stroke="#96cdff" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="89.1,-188.0 44.6,-110.8 -44.5,-110.8 -89.1,-188.0 -44.6,-265.2 44.6,-265.2" fill="#1ea876" stroke="#82f0c3" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="251.9,-94.0 207.4,-16.8 118.3,-16.8 73.7,-94.0 118.3,-171.2 207.4,-171.2" fill="#70aaee" stroke="#c4e0ff" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="251.9,94.0 207.4,171.2 118.3,171.2 73.7,94.0 118.3,16.8 207.4,16.8" fill="#ec6e76" stroke="#ffbebe" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="89.1,188.0 44.6,265.2 -44.5,265.2 -89.1,188.0 -44.6,110.8 44.6,110.8" fill="#70aaee" stroke="#c4e0ff" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="-73.7,94.0 -118.3,171.2 -207.4,171.2 -251.9,94.0 -207.4,16.8 -118.3,16.8" fill="#7a8eb8" stroke="#cddaf0" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="-73.7,-94.0 -118.3,-16.8 -207.4,-16.8 -251.9,-94.0 -207.4,-171.2 -118.3,-171.2" fill="#70aaee" stroke="#c4e0ff" stroke-width="3.9" stroke-linejoin="round"/>
<polygon points="54.5,0.0 27.2,47.2 -27.2,47.2 -54.5,0.0 -27.2,-47.2 27.2,-47.2" fill="#3cafff" fill-opacity=".9" filter="url(#c)" clip-path="url(#k)"/>
</svg>
"""

# assets/icon.png scaled to 192x192, base64 on one line.
ICON_PNG_B64 = "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"

# The manifest and the theme-color meta tag in INDEX_HTML are the only places with plain hex colours (they
# cannot use CSS custom properties); the values are DEFAULT_PAL deep and base from hivegrid_ui.
MANIFEST = r"""{
  "name": "Hivegrid",
  "short_name": "Hivegrid",
  "description": "Remote control for your Hivegrid agents",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#070a10",
  "theme_color": "#070a10",
  "icons": [
    { "src": "/icon.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "/logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any" }
  ]
}
"""
