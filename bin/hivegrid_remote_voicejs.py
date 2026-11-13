"""The voice mode of the phone page as Python string constants (served by hivegrid_remote.py like the terminal's):
`/voice.js` (the overlay, the session with Google's Live API, tool calls), `/voice-worklet.js` (the microphone as
16 kHz PCM) and `/voice.css`. No library, no external asset. See hivegrid_remote_voice.py and docs/remote.md ("Voice").
"""

VOICE_WORKLET_JS = r"""// The microphone as 16 kHz mono 16-bit PCM in chunks of 40 ms, with the chunk's level (RMS, 0..1).
class HGMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;   // input samples per output sample
    this.acc = 0;                     // sum of the input samples of the output sample being built
    this.n = 0;                       // how many
    this.pos = 0;                     // input samples seen since the last output sample
    this.out = new Int16Array(640);
    this.fill = 0;
    this.sq = 0;
  }
  process(inputs) {
    var ch = inputs[0] && inputs[0][0];
    if (!ch) { return true; }
    for (var i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.n += 1;
      this.pos += 1;
      if (this.pos >= this.step) {
        this.pos -= this.step;
        var v = this.acc / this.n;
        this.acc = 0;
        this.n = 0;
        v = v < -1 ? -1 : (v > 1 ? 1 : v);
        this.sq += v * v;
        this.out[this.fill++] = v < 0 ? v * 32768 : v * 32767;
        if (this.fill === this.out.length) {
          var buf = this.out.buffer.slice(0);
          this.port.postMessage({ pcm: buf, level: Math.sqrt(this.sq / this.fill) }, [buf]);
          this.fill = 0;
          this.sq = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("hg-mic", HGMic);
"""

VOICE_JS = r"""(function () {
  "use strict";
  // Voice mode: one overlay, one session at a time. HGVoice.open(ctx) with ctx = { h, api, toast, ask, refresh }.
  var OUT_RATE = 24000;
  var TOOL_TIMEOUT = 150000;
  var LABEL = { connecting: "connecting\u2026", listening: "listening", speaking: "speaking", working: "working", muted: "muted", error: "stopped" };
  var GLYPH = { connecting: "\u25d4", listening: "\u25c9", speaking: "\u25ce", working: "\u21bb", muted: "\u25cb", error: "!" };
  var S = null;        // the open session

  function b64(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i += 8192) { s += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192)); }
    return window.btoa(s);
  }
  function pcmFloats(data) {
    var raw = window.atob(data);
    var out = new Float32Array(raw.length >> 1);
    for (var i = 0; i < out.length; i++) {
      var v = raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8);
      out[i] = (v >= 32768 ? v - 65536 : v) / 32768;
    }
    return out;
  }
  function argText(args) {
    var parts = [];
    Object.keys(args || {}).forEach(function (k) { parts.push(k + " " + String(args[k]).slice(0, 60)); });
    return parts.join(", ");
  }

  function setState(s, state, detail) {
    if (S !== s) { return; }
    s.state = state;
    s.el.className = "voice v-" + state;
    s.glyph.textContent = GLYPH[state];
    s.label.textContent = LABEL[state] + (detail ? " \u00b7 " + detail : "");
  }
  // The state the orb shows when nothing else decides it: a running tool, queued speech, the mute switch.
  function settle(s) {
    if (S !== s || s.state === "error" || s.state === "connecting") { return; }
    var tools = Object.keys(s.tools);
    if (s.sources.length) { setState(s, "speaking"); }
    else if (tools.length) { setState(s, "working", s.tools[tools[0]]); }
    else { setState(s, s.muted ? "muted" : "listening"); }
  }
  function line(s, who, text, cls) {
    var row = s.ctx.h("p", { class: "vl " + (cls || "") }, s.ctx.h("span", { class: "vw" }, who), s.ctx.h("span", { class: "vt" }, text));
    s.log.appendChild(row);
    while (s.log.childNodes.length > 80) { s.log.removeChild(s.log.firstChild); }
    s.log.scrollTop = s.log.scrollHeight;
    return row.lastChild;
  }
  // Transcripts arrive in pieces: they grow the open line of that speaker until the turn ends.
  function say(s, who, text) {
    if (!text) { return; }
    if (!s.open[who]) { s.open[who] = line(s, who === "you" ? "you" : "hivegrid", "", who === "you" ? "me" : ""); }
    s.open[who].textContent += text;
    s.log.scrollTop = s.log.scrollHeight;
  }

  function stopSpeech(s) {
    s.sources.splice(0).forEach(function (src) { src.onended = null; try { src.stop(); } catch (e) { /* not started */ } });
    s.next = 0;
  }
  function play(s, data) {
    var f = pcmFloats(data);
    if (!f.length || !s.audio) { return; }
    var buf = s.audio.createBuffer(1, f.length, OUT_RATE);
    buf.getChannelData(0).set(f);
    var src = s.audio.createBufferSource();
    src.buffer = buf;
    src.connect(s.audio.destination);
    var at = Math.max(s.audio.currentTime + 0.04, s.next);
    src.start(at);
    s.next = at + buf.duration;
    s.sources.push(src);
    src.onended = function () {
      var i = s.sources.indexOf(src);
      if (i >= 0) { s.sources.splice(i, 1); }
      settle(s);
    };
    settle(s);
  }

  function send(s, msg) { if (s.ws && s.ws.readyState === 1) { s.ws.send(JSON.stringify(msg)); } }

  function runTool(s, fc) {
    var ws = s.ws;
    var name = String(fc.name);
    var args = fc.args && typeof fc.args === "object" ? fc.args : {};
    var row = line(s, "tool", name + (argText(args) ? " \u00b7 " + argText(args) : ""), "tool");
    function answer(text, failed) {
      delete s.tools[fc.id];
      row.textContent += failed ? "  \u2717" : "  \u2713";
      if (s.cancelled[fc.id]) { delete s.cancelled[fc.id]; settle(s); return; }
      if (S === s && s.ws === ws) {
        send(s, { toolResponse: { functionResponses: [{ id: fc.id, name: name, response: failed ? { error: text } : { result: text } }] } });
      }
      settle(s);
    }
    s.tools[fc.id] = name;
    settle(s);
    var sure = s.confirm.indexOf(name) >= 0
      ? s.ctx.ask("The voice assistant wants to run " + name + (argText(args) ? " (" + argText(args) + ")" : "") + ".", "Allow")
      : Promise.resolve(true);
    sure.then(function (ok) {
      if (!ok) { answer("the user declined this on the screen", true); return; }
      s.ctx.api("/api/voice/tool", { body: { name: name, args: args, confirm: s.confirm.indexOf(name) >= 0 }, timeout: TOOL_TIMEOUT }).then(function (r) {
        if (r.status === 200 && r.data) { answer(String(r.data.text), !!r.data.error); if (s.ctx.refresh) { s.ctx.refresh(); } }
        else { answer(r.data && r.data.message ? String(r.data.message) : "the request failed (" + r.status + ")", true); }
      }, function () { answer("no connection to Hivegrid", true); });
    });
  }

  function onMessage(s, m) {
    if (m.setupComplete) {
      s.fails = 0;
      s.up = true;
      // Google ends a connection after about ten minutes; the next one does not know what was said before.
      if (s.sessions++) { line(s, "note", "reconnected: the assistant starts a new conversation", "tool"); }
      setState(s, s.muted ? "muted" : "listening");
      return;
    }
    if (m.toolCall && m.toolCall.functionCalls) { m.toolCall.functionCalls.forEach(function (fc) { runTool(s, fc); }); }
    if (m.toolCallCancellation && m.toolCallCancellation.ids) { m.toolCallCancellation.ids.forEach(function (id) { if (s.tools[id]) { s.cancelled[id] = true; } }); }
    if (m.goAway) { reconnect(s); }
    var c = m.serverContent;
    if (!c) { return; }
    if (c.interrupted) { stopSpeech(s); s.open.model = null; settle(s); }
    if (c.inputTranscription) { say(s, "you", c.inputTranscription.text); }
    if (c.outputTranscription) { s.open.you = null; say(s, "model", c.outputTranscription.text); }
    ((c.modelTurn && c.modelTurn.parts) || []).forEach(function (p) { if (p.inlineData && p.inlineData.data) { play(s, p.inlineData.data); } });
    if (c.turnComplete) { s.open.you = null; s.open.model = null; }
  }

  function fail(s, text) {
    if (S !== s) { return; }
    stopSpeech(s);
    setState(s, "error", text);
    s.retry.hidden = false;
  }
  // A new connection with a fresh token (each token starts one session).
  function connect(s) {
    if (S !== s) { return; }
    if (s.ws) { s.ws.onclose = null; s.ws.onmessage = null; try { s.ws.close(); } catch (e) { /* closed */ } s.ws = null; }
    s.tools = {};
    s.up = false;
    s.retry.hidden = true;
    setState(s, "connecting");
    s.ctx.api("/api/voice/session", { body: {}, timeout: 15000 }).then(function (r) {
      if (S !== s) { return; }
      if (r.status !== 200 || !r.data) { fail(s, r.data && r.data.message ? String(r.data.message) : "no session (" + r.status + ")"); return; }
      s.confirm = r.data.confirm || [];
      var setup = r.data.setup;
      var ws = new WebSocket(r.data.url + "?access_token=" + encodeURIComponent(r.data.token));
      s.ws = ws;
      ws.onopen = function () { ws.send(JSON.stringify({ setup: setup })); };
      ws.onmessage = function (ev) {
        var text = typeof ev.data === "string" ? Promise.resolve(ev.data) : new Response(ev.data).text();
        text.then(function (t) {
          if (S !== s || s.ws !== ws) { return; }
          var m = null;
          try { m = JSON.parse(t); } catch (e) { return; }
          onMessage(s, m);
        });
      };
      ws.onclose = function (ev) {
        if (S !== s || s.ws !== ws) { return; }
        s.ws = null;
        s.fails += 1;
        // A session Google never started (no quota, a bad key, a model that is gone) is not tried again by itself:
        // every try is a new token and a new request against the same limit.
        if (!s.up || s.fails > 3) { fail(s, ev.reason ? String(ev.reason).slice(0, 120) : "the connection closed"); return; }
        window.setTimeout(function () { connect(s); }, 400 * s.fails);
      };
    }, function () { fail(s, "no connection to Hivegrid"); });
  }
  function reconnect(s) { if (S === s && s.state !== "connecting") { connect(s); } }

  function startMic(s) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.AudioWorkletNode) {
      return Promise.reject(new Error("this browser has no microphone API (the page needs https)"));
    }
    s.audio = new AC();
    return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }).then(function (stream) {
      if (S !== s) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
      s.stream = stream;
      return s.audio.audioWorklet.addModule("/voice-worklet.js").then(function () {
        if (S !== s) { return; }
        var node = new AudioWorkletNode(s.audio, "hg-mic", { numberOfInputs: 1, numberOfOutputs: 0 });
        node.port.onmessage = function (ev) {
          if (S !== s) { return; }
          if (!s.calm) { s.orb.style.setProperty("--lvl", String(Math.min(1, ev.data.level * 6).toFixed(2))); }
          if (s.muted || !s.ws || s.ws.readyState !== 1 || s.state === "connecting") { return; }
          s.ws.send(JSON.stringify({ realtimeInput: { audio: { data: b64(new Uint8Array(ev.data.pcm)), mimeType: "audio/pcm;rate=16000" } } }));
        };
        s.audio.createMediaStreamSource(stream).connect(node);
        s.node = node;
        return s.audio.resume();
      });
    });
  }

  function close() {
    var s = S;
    if (!s) { return; }
    S = null;
    document.removeEventListener("keydown", s.onKey, true);
    if (s.ws) { s.ws.onclose = null; s.ws.onmessage = null; try { s.ws.close(); } catch (e) { /* closed */ } }
    stopSpeech(s);
    if (s.stream) { s.stream.getTracks().forEach(function (t) { t.stop(); }); }
    if (s.node) { s.node.port.onmessage = null; s.node.disconnect(); }
    if (s.audio) { s.audio.close().catch(function () { /* closed */ }); }
    if (s.lock) { s.lock.release().catch(function () { /* released */ }); }
    if (s.el.parentNode) { s.el.parentNode.removeChild(s.el); }
    if (s.back && s.back.focus) { s.back.focus({ preventScroll: true }); }
  }

  function open(ctx) {
    if (S) { return; }
    var h = ctx.h;
    var s = { ctx: ctx, state: "connecting", ws: null, audio: null, stream: null, node: null, lock: null, muted: false, sessions: 0, fails: 0, up: false,
              sources: [], next: 0, tools: {}, cancelled: {}, open: { you: null, model: null }, confirm: [], back: document.activeElement,
              calm: !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) };
    s.glyph = h("span", { class: "vg", "aria-hidden": "true" });
    s.orb = h("div", { class: "vorb" }, s.glyph);
    s.label = h("p", { class: "vstate", role: "status" });
    s.log = h("div", { class: "vlog", role: "log", "aria-label": "Conversation" });
    s.mute = h("button", { class: "btn", type: "button", "aria-pressed": "false", onclick: function () {
      s.muted = !s.muted;
      s.mute.setAttribute("aria-pressed", String(s.muted));
      s.mute.textContent = s.muted ? "Unmute" : "Mute";
      if (s.stream) { s.stream.getAudioTracks().forEach(function (t) { t.enabled = !s.muted; }); }
      settle(s);
    } }, "Mute");
    s.retry = h("button", { class: "btn primary", type: "button", hidden: true, onclick: function () { s.fails = 0; connect(s); } }, "Try again");
    s.end = h("button", { class: "btn danger", type: "button", onclick: close }, "End");
    s.el = h("div", { class: "voice v-connecting", role: "dialog", "aria-modal": "true", "aria-label": "Voice" },
      h("header", { class: "vtop" }, h("span", { class: "accent" }, "\u2b21 Voice"), h("span", { class: "vsub" }, "talk to manage your agents")),
      h("div", { class: "vmid" }, s.orb, s.label), s.log, h("footer", { class: "vfoot" }, s.mute, s.retry, s.end));
    s.onKey = function (ev) { if (ev.key === "Escape" && !document.querySelector("dialog[open]")) { ev.preventDefault(); close(); } };
    document.addEventListener("keydown", s.onKey, true);
    document.body.appendChild(s.el);
    S = s;
    setState(s, "connecting");
    s.end.focus({ preventScroll: true });
    if (navigator.wakeLock && navigator.wakeLock.request) {
      navigator.wakeLock.request("screen").then(function (l) { if (S === s) { s.lock = l; } else { l.release(); } }, function () { /* the screen may sleep */ });
    }
    startMic(s).then(function () { connect(s); }, function (err) {
      fail(s, err && err.name === "NotAllowedError" ? "the microphone is not allowed for this page" : String((err && err.message) || "no microphone"));
      s.retry.hidden = true;
    });
  }

  window.HGVoice = { open: open, close: close, isOpen: function () { return !!S; } };
})();
"""

VOICE_CSS = r"""/* Voice mode: a full-screen overlay. Colours only from the theme's custom properties (see design.md). */
.voice { position: fixed; inset: 0; z-index: 60; display: flex; flex-direction: column; background: var(--base); color: var(--text);
  padding: calc(var(--sat) + 10px) calc(var(--sar) + 14px) calc(var(--sab) + 14px) calc(var(--sal) + 14px); --sc: var(--amber); }
.voice.v-listening { --sc: var(--green); }
.voice.v-speaking { --sc: var(--accent); }
.voice.v-working, .voice.v-connecting { --sc: var(--amber); }
.voice.v-muted { --sc: var(--grey); }
.voice.v-error { --sc: var(--red); }
.vtop { display: flex; align-items: baseline; gap: 10px; padding-bottom: 8px; border-bottom: 1px solid var(--rule-base); font-weight: 700; }
.vtop .vsub { color: var(--muted); font-weight: 400; font-size: 13px; }
.vmid { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 28px 0 18px; }
.vorb { --lvl: 0; width: 132px; height: 132px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  border: 2px solid var(--sc); background: color-mix(in srgb, var(--base) 88%, var(--sc) 12%);
  box-shadow: 0 0 0 calc(var(--lvl) * 18px) color-mix(in srgb, transparent 82%, var(--sc) 18%); transition: box-shadow 90ms linear, border-color 150ms; }
.voice:not(.v-listening) .vorb { box-shadow: none; }
.vg { font-size: 44px; line-height: 1; color: var(--sc); }
.voice.v-working .vg, .voice.v-connecting .vg { animation: vspin 1.2s linear infinite; }
@keyframes vspin { to { transform: rotate(360deg); } }
.vstate { margin: 0; color: var(--sc); font-weight: 700; text-align: center; overflow-wrap: anywhere; }
.vlog { flex: 1 1 auto; min-height: 0; overflow-y: auto; width: 100%; max-width: 720px; margin: 0 auto; padding: 4px 0; }
.vl { margin: 0 0 8px; display: flex; gap: 10px; line-height: 1.4; }
.vl .vw { flex: 0 0 68px; color: var(--muted); font-size: 13px; padding-top: 1px; }
.vl .vt { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; color: var(--bright); }
.vl.me .vt { color: var(--text); }
.vl.tool .vt { color: var(--dim); font-family: var(--mono); font-size: 13px; }
.vfoot { display: flex; justify-content: center; gap: 12px; padding-top: 12px; border-top: 1px solid var(--rule-base); }
.vfoot .btn { min-width: 96px; min-height: 44px; }
@media (prefers-reduced-motion: reduce) { .voice .vg { animation: none; } .vorb { transition: none; } }
"""
