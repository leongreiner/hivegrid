/**
 * hivegrid-agent-state: omp extension that publishes the main session's state to
 *   ${XDG_RUNTIME_DIR:-/tmp}/hivegrid-agents/<WEZTERM_PANE>.json
 * for the hivegrid hive panel and the overview dock, and executes plan-review answers the
 * hive drops into
 *   <dir>/<pane>.cmd.<unique>.json
 * When the session ends the file says `exited`, so the pane reads as an agent that quit
 * (not as a plain shell) until another agent starts there or the hive drops the file with
 * the pane. The file also carries the session's model, thinking level and context fill, and
 *   <dir>/<pane>.models.json
 * lists the models and thinking levels the phone remote may switch to (`set_model`,
 * `set_thinking` command files).
 *
 * The state machine is the pure, exported `reduce`; everything else is thin I/O that
 * never writes to stdout/stderr (omp's TUI owns the terminal) and never throws into omp.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

// ---------------------------------------------------------------------------------
// Pure state machine
// ---------------------------------------------------------------------------------

export type AgentStateName = "working" | "busy" | "done" | "idle" | "attention";

export type Pending =
	| { kind: "plan_review"; reviewId: string; title: string; choices: { id: string; label: string }[] }
	| { kind: "approval"; tool: string; summary: string }
	| { kind: "ask"; question: string; options: string[] };

export interface ToolRun {
	name: string;
	start: number;
	summary: string;
}

export interface AgentModel {
	state: AgentStateName;
	reason: string | null;
	since: number;
	pending: Pending | null;
	error: string | null;
	/** the agent loop is running (between agent_start and agent_end/session_stop) */
	running: boolean;
	/** an agent loop has run at least once (idle -> done once it settles) */
	started: boolean;
	/** running async/background jobs, as last observed */
	jobs: number;
	tools: Record<string, ToolRun>;
	approvalPending: boolean;
}

export type HiveEvent =
	| { type: "agent_start" }
	| { type: "agent_end"; jobs?: number }
	| { type: "session_stop"; jobs?: number }
	| { type: "tool_start"; id: string; name: string; args?: unknown }
	| { type: "tool_end"; id: string; name?: string; isError?: boolean; errorText?: string }
	| { type: "approval_requested"; tool: string; summary?: string }
	| { type: "approval_resolved" }
	| { type: "plan_review_requested"; review: unknown }
	| { type: "plan_review_resolved" }
	| { type: "tick"; jobs: number };

/** A tool running longer than this with no model output turns `working` into `busy`. */
export const BUSY_AFTER_S = 8;
/** Tools that block on subagents / the human: never "busy". */
const NON_BUSY_TOOLS = new Set(["task", "ask"]);

export function initialModel(now: number): AgentModel {
	return {
		state: "idle",
		reason: null,
		since: now,
		pending: null,
		error: null,
		running: false,
		started: false,
		jobs: 0,
		tools: {},
		approvalPending: false,
	};
}

function clip(text: string, max: number): string {
	const one = text.replace(/\s+/g, " ").trim();
	return one.length > max ? `${one.slice(0, max - 1)}…` : one;
}

function asRecord(v: unknown): Record<string, unknown> {
	return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

/** One-line summary of a tool call's args ("npm test", a path, ...). */
export function summarizeArgs(args: unknown): string {
	const a = asRecord(args);
	for (const key of ["command", "cmd", "path", "file_path", "pattern", "query", "url", "description", "prompt"]) {
		const v = a[key];
		if (typeof v === "string" && v) return clip(v, 60);
	}
	return "";
}

/** Extract question + options from an `ask` tool call's args (shape unverified: tolerate variants). */
export function extractAsk(args: unknown): { question: string; options: string[] } {
	const a = asRecord(args);
	const first = Array.isArray(a.questions) && a.questions.length ? asRecord(a.questions[0]) : a;
	const q = first.question ?? first.prompt ?? first.text ?? a.question;
	const rawOptions = Array.isArray(first.options) ? first.options : Array.isArray(a.options) ? a.options : [];
	const options: string[] = [];
	for (const o of rawOptions) {
		if (typeof o === "string") options.push(clip(o, 80));
		else {
			const r = asRecord(o);
			const label = r.label ?? r.title ?? r.text ?? r.value ?? r.id;
			if (typeof label === "string") options.push(clip(label, 80));
		}
	}
	return { question: typeof q === "string" ? clip(q, 300) : "", options };
}

/** omp's fixed plan-review answers (resolvePlanReview choices); its event carries no list (18.12). */
export const PLAN_CHOICES = [
	{ id: "execute", label: "Execute" },
	{ id: "compact", label: "Compact" },
	{ id: "keep", label: "Keep" },
	{ id: "refine", label: "Refine" },
	{ id: "save", label: "Save" },
];

/** Normalise omp's plan_review_requested payload (or ctx.getPendingPlanReview()) into a Pending. */
export function planReviewPending(review: unknown): Pending | null {
	const r = asRecord(review);
	const reviewId = r.reviewId;
	if (typeof reviewId !== "string" || !reviewId) return null;
	const choices: { id: string; label: string }[] = [];
	if (Array.isArray(r.choices)) {
		for (const c of r.choices) {
			if (typeof c === "string") choices.push({ id: c, label: c });
			else {
				const cr = asRecord(c);
				const id = cr.id ?? cr.choice ?? cr.value;
				if (typeof id === "string") choices.push({ id, label: typeof cr.label === "string" ? cr.label : id });
			}
		}
	}
	return {
		kind: "plan_review",
		reviewId,
		title: typeof r.title === "string" ? clip(r.title, 200) : "",
		choices: choices.length ? choices : PLAN_CHOICES.map(c => ({ ...c })),
	};
}

function pickTool(tools: Record<string, ToolRun>, filter: (t: ToolRun) => boolean, oldest: boolean): ToolRun | null {
	let best: ToolRun | null = null;
	for (const t of Object.values(tools)) {
		if (!filter(t)) continue;
		if (!best || (oldest ? t.start < best.start : t.start > best.start)) best = t;
	}
	return best;
}

function toolLabel(t: ToolRun, now: number | null): string {
	const base = t.summary ? `${t.name}: ${t.summary}` : t.name;
	return now === null ? base : `${base} (${Math.max(0, Math.floor(now - t.start))}s)`;
}

/** Derive state/reason from the facts held in the model; preserves `since` while the state is unchanged. */
function settle(m: AgentModel, now: number): AgentModel {
	let state: AgentStateName;
	let reason: string | null;
	if (m.pending) {
		state = "attention";
		reason =
			m.pending.kind === "plan_review"
				? "plan review"
				: m.pending.kind === "approval"
					? `approval: ${m.pending.tool}`
					: "ask";
	} else if (m.running) {
		const slow = pickTool(m.tools, t => !NON_BUSY_TOOLS.has(t.name) && now - t.start > BUSY_AFTER_S, true);
		if (slow) {
			state = "busy";
			reason = toolLabel(slow, now);
		} else {
			state = "working";
			const cur = pickTool(m.tools, () => true, false);
			reason = cur ? toolLabel(cur, null) : null;
		}
	} else if (m.jobs > 0) {
		state = "busy";
		reason = m.jobs === 1 ? "1 background job" : `${m.jobs} background jobs`;
	} else {
		state = m.started ? "done" : "idle";
		reason = null;
	}
	const since = state === m.state ? m.since : now;
	if (state === m.state && reason === m.reason && since === m.since) return m;
	return { ...m, state, reason, since };
}

/** Pure transition function. Never mutates `m`. */
export function reduce(m: AgentModel, ev: HiveEvent, now: number): AgentModel {
	let n: AgentModel = m;
	switch (ev.type) {
		case "agent_start":
			n = { ...m, running: true, started: true, tools: {}, pending: null, approvalPending: false };
			break;
		case "agent_end":
		case "session_stop":
			// omp (print mode, 18.12.6) fires session_stop BEFORE agent_end; both mean "loop over".
			n = {
				...m,
				running: false,
				tools: {},
				// approvals/asks die with the loop; an open plan review is answered independently
				pending: m.pending?.kind === "plan_review" ? m.pending : null,
				approvalPending: false,
				jobs: ev.jobs ?? m.jobs,
			};
			break;
		case "tool_start": {
			const tools = { ...m.tools, [ev.id]: { name: ev.name, start: now, summary: summarizeArgs(ev.args) } };
			n = { ...m, tools, running: true, started: true };
			if (ev.name === "ask") {
				const a = extractAsk(ev.args);
				n = { ...n, pending: { kind: "ask", question: a.question, options: a.options } };
			}
			break;
		}
		case "tool_end": {
			const tools = { ...m.tools };
			const ended = tools[ev.id];
			delete tools[ev.id];
			n = { ...m, tools };
			if ((ended?.name ?? ev.name) === "ask" && m.pending?.kind === "ask") n = { ...n, pending: null };
			n = { ...n, error: ev.isError ? clip(ev.errorText || "tool error", 300) : null };
			break;
		}
		case "approval_requested":
			n = {
				...m,
				pending: { kind: "approval", tool: ev.tool, summary: clip(ev.summary ?? "", 120) },
				approvalPending: true,
			};
			break;
		case "approval_resolved":
			n = { ...m, pending: m.pending?.kind === "approval" ? null : m.pending, approvalPending: false };
			break;
		case "plan_review_requested": {
			const p = planReviewPending(ev.review);
			n = p ? { ...m, pending: p } : m;
			break;
		}
		case "plan_review_resolved":
			n = { ...m, pending: m.pending?.kind === "plan_review" ? null : m.pending };
			break;
		case "tick":
			n = { ...m, jobs: ev.jobs };
			break;
	}
	return settle(n, now);
}

/** Error text written by the I/O layer (e.g. a rejected plan-review answer). */
export function withError(m: AgentModel, error: string | null): AgentModel {
	return m.error === error ? m : { ...m, error };
}

// ---------------------------------------------------------------------------------
// State file document
// ---------------------------------------------------------------------------------

/**
 * Token counts and cost exactly as omp reports them for the session (its `getUsageStatistics()`
 * snapshot, subagents included). Fields omp did not report are null; nothing is estimated here.
 */
export interface UsageDoc {
	input: number | null;
	output: number | null;
	cacheRead: number | null;
	cacheWrite: number | null;
	/** omp's sum of every request's `totalTokens` (a cached prompt counts again on each request) */
	total: number | null;
	/** USD as omp sums it over the session; `costKind` says what it is made of */
	cost: number | null;
	/** the part of `cost` that subagents spent */
	subagentCost: number | null;
	/**
	 * `reported`: every priced request came from a gateway whose own charge omp keeps;
	 * `estimated`: omp priced the reported tokens from its model catalog (API list price, which
	 * is not a bill on a subscription); `mixed`: both; null: no cost yet.
	 */
	costKind: "reported" | "estimated" | "mixed" | null;
}

export interface DocMeta {
	pane: number;
	pid: number;
	sessionId: string | null;
	/** Absolute path of the session's JSONL file, null until omp has written it (a chat without a message has none). */
	sessionFile: string | null;
	cwd: string;
	title: string | null;
	profile: string | null;
	usage?: UsageDoc | null;
	/** the session's current model, thinking level and context fill; absent/null when omp did not say */
	model?: ModelRef | null;
	thinking?: string | null;
	context?: ContextDoc | null;
}

/** A model as the phone shows it: `id` is `provider/id`, the string `set_model` takes back. */
export interface ModelRef {
	id: string;
	name: string;
	provider: string;
}

export interface ContextDoc {
	tokens: number;
	window: number;
	pct: number;
}

/** omp's thinking levels, lowest first ("off" disables thinking). omp clamps a level the model lacks. */
export const THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

const MAX_MODELS = 400;

function str(v: unknown, max: number): string | null {
	return typeof v === "string" && v ? clip(v.replace(/[\u0000-\u001f\u007f]/g, " "), max) : null;
}

/** `{id: "provider/id", name, provider}` of an omp model object; null when it names no provider or id. */
export function modelRef(m: unknown): ModelRef | null {
	const r = asRecord(m);
	const provider = str(r.provider, 80);
	const id = str(r.id, 160);
	if (!provider || !id) return null;
	return { id: `${provider}/${id}`, name: str(r.name, 120) ?? id, provider };
}

/** The `<pane>.models.json` document: the models omp can switch to and the thinking levels. */
export function modelsDoc(models: unknown, levels: string[] = THINKING_LEVELS): Record<string, unknown> {
	const out: { id: string; name: string; provider: string; reasoning: boolean }[] = [];
	const seen = new Set<string>();
	for (const m of Array.isArray(models) ? models : []) {
		const ref = modelRef(m);
		if (!ref || seen.has(ref.id)) continue;
		seen.add(ref.id);
		out.push({ ...ref, reasoning: !!asRecord(m).reasoning });
		if (out.length >= MAX_MODELS) break;
	}
	return { v: 1, thinking_levels: levels, models: out };
}

/** The omp model object whose `provider/id` is `id`, or undefined. */
export function findModel(models: unknown, id: string): unknown {
	for (const m of Array.isArray(models) ? models : []) if (modelRef(m)?.id === id) return m;
	return undefined;
}

/** `ctx.getContextUsage()` as {tokens, window, pct}; null unless omp reported a token count and a window. */
export function contextDoc(usage: unknown): ContextDoc | null {
	const u = asRecord(usage);
	const tokens = amount(u.tokens);
	const window = amount(u.contextWindow);
	if (tokens === null || window === null || window <= 0) return null;
	const pct = amount(u.percent) ?? (tokens / window) * 100;
	return { tokens, window, pct: Math.round(pct * 10) / 10 };
}

/** A thinking level omp reported, as a short string, else null. */
export function thinkingName(level: unknown): string | null {
	return typeof level === "string" && THINKING_LEVELS.includes(level) ? level : null;
}

/**
 * Providers whose own per-request charge omp keeps instead of pricing the tokens itself
 * (omp docs, provider-quirks: OpenRouter and ClinePass `usage.cost` is authoritative when the
 * gateway reports it). Every other provider's cost is omp's catalog price times the reported tokens.
 */
export const GATEWAY_COST_PROVIDERS: Record<string, true> = { openrouter: true, "cline-pass": true };

function amount(v: unknown): number | null {
	return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** Provider name of a persisted session entry that cost money; "?" when the entry names none (a subagent's spend). */
export function pricedProvider(entry: unknown): string | null {
	const e = asRecord(entry);
	const m = asRecord(e.type === "message" ? e.message : e);
	let usage: unknown;
	if (e.type === "model_usage" || m.role === "assistant") usage = m.usage;
	else if (m.role === "toolResult" && m.toolName === "task") usage = asRecord(m.details).usage;
	if (!amount(asRecord(asRecord(usage).cost).total)) return null;
	return typeof m.provider === "string" && m.provider ? m.provider : "?";
}

export function costKindOf(providers: Iterable<string>): UsageDoc["costKind"] {
	let gateway = 0;
	let n = 0;
	for (const p of providers) {
		n++;
		if (GATEWAY_COST_PROVIDERS[p]) gateway++;
	}
	if (n === 0) return null;
	return gateway === n ? "reported" : gateway === 0 ? "estimated" : "mixed";
}

/**
 * The state file's `usage` from omp's `sessionManager.getUsageStatistics()` and the providers
 * that priced the session. Only numbers omp reported are copied; null when it reported none.
 */
export function usageDoc(stats: unknown, providers: Iterable<string>): UsageDoc | null {
	const s = asRecord(stats);
	const doc: UsageDoc = {
		input: amount(s.input),
		output: amount(s.output),
		cacheRead: amount(s.cacheRead),
		cacheWrite: amount(s.cacheWrite),
		total: amount(s.totalTokens),
		cost: amount(s.cost),
		subagentCost: amount(s.subagentCost),
		costKind: null,
	};
	if (doc.input === null && doc.output === null && doc.total === null && doc.cost === null) return null;
	// a subagent's spend names no provider: it counts as estimated unless a gateway charge is the only cost
	const named = new Set(providers);
	if (doc.subagentCost) named.add("?");
	if (doc.cost) doc.costKind = costKindOf(named);
	return doc;
}

export function toDoc(m: AgentModel, meta: DocMeta, updated: number): Record<string, unknown> {
	return {
		v: 1,
		pane: meta.pane,
		pid: meta.pid,
		sessionId: meta.sessionId,
		sessionFile: meta.sessionFile,
		cwd: meta.cwd,
		title: meta.title,
		profile: meta.profile,
		usage: meta.usage ?? null,
		model: meta.model ?? null,
		thinking: meta.thinking ?? null,
		context: meta.context ?? null,
		state: m.state,
		reason: m.reason,
		since: m.since,
		updated,
		pending: m.pending,
		error: m.error,
	};
}

/** The document of an agent that has quit: state `exited` since `now`, nothing pending. */
export function exitedDoc(m: AgentModel, meta: DocMeta, now: number): Record<string, unknown> {
	return { ...toDoc(m, meta, now), state: "exited", reason: null, since: now, pending: null, error: null };
}

/** profile = path component after `profiles/` in the agent dir, else $OMP_PROFILE/$PI_PROFILE, else null. */
export function profileFromAgentDir(agentDir: string | null | undefined, env: NodeJS.ProcessEnv = process.env): string | null {
	if (agentDir) {
		const parts = agentDir.split(/[\\/]+/);
		const i = parts.lastIndexOf("profiles");
		if (i >= 0 && parts[i + 1]) return parts[i + 1];
	}
	return env.OMP_PROFILE || env.PI_PROFILE || null;
}

export function stateDir(env: NodeJS.ProcessEnv = process.env): string {
	return path.join(env.XDG_RUNTIME_DIR || "/tmp", "hivegrid-agents");
}

/** Does `name` look like a command file for `pane`? */
export function isCmdFile(name: string, pane: number): boolean {
	return name.startsWith(`${pane}.cmd.`) && name.endsWith(".json");
}

// ---------------------------------------------------------------------------------
// Command channel
// ---------------------------------------------------------------------------------

export interface CmdCtx {
	resolvePlanReview(reviewId: string, choice: string, input?: { feedback?: string; role?: string }): boolean | Promise<boolean>;
	/** switch to the model `provider/id`; false when it is unknown or has no credentials */
	setModel?(id: string): boolean | Promise<boolean>;
	setThinking?(level: string): void | Promise<void>;
}

/**
 * Execute one parsed command. Returns an error message to surface in the state file, or null.
 * Unknown command types are ignored (null).
 */
export async function handleCommand(cmd: unknown, ctx: CmdCtx): Promise<string | null> {
	const c = asRecord(cmd);
	if (c.type === "set_model") {
		if (typeof c.model !== "string" || !c.model || c.model.length > 300) return "model switch malformed";
		if (!ctx.setModel) return "this omp cannot switch models";
		return (await ctx.setModel(c.model)) ? null : `model "${clip(c.model, 80)}" was not switched (unknown or no credentials)`;
	}
	if (c.type === "set_thinking") {
		if (typeof c.level !== "string" || !THINKING_LEVELS.includes(c.level)) return "thinking level malformed";
		if (!ctx.setThinking) return "this omp cannot change the thinking level";
		await ctx.setThinking(c.level);
		return null;
	}
	if (c.type !== "plan_review") return null;
	const reviewId = c.reviewId;
	const choice = c.choice;
	if (typeof reviewId !== "string" || typeof choice !== "string") return "plan review answer malformed";
	const input: { feedback?: string; role?: string } = {};
	if (typeof c.feedback === "string" && c.feedback) input.feedback = c.feedback;
	if (typeof c.role === "string" && c.role) input.role = c.role;
	const ok = await ctx.resolvePlanReview(reviewId, choice, Object.keys(input).length ? input : undefined);
	return ok ? null : `plan review answer "${choice}" was rejected (review closed or choice unavailable)`;
}

// ---------------------------------------------------------------------------------
// Extension (I/O layer)
// ---------------------------------------------------------------------------------

const TICK_MS = 1000;
const HEARTBEAT_S = 5;

export default function hivegridAgentState(pi: any): void {
	try {
		const paneEnv = process.env.WEZTERM_PANE;
		const pane = paneEnv && /^\d+$/.test(paneEnv) ? Number(paneEnv) : NaN;
		if (!Number.isFinite(pane)) return;

		const dir = stateDir();
		const file = path.join(dir, `${pane}.json`);
		const nowS = () => Date.now() / 1000;

		let model = initialModel(nowS());
		let ctxRef: any = null;
		let lastSig = "";
		let lastWrite = 0;
		let timer: unknown = null;
		let dead = false;
		let busyCmd = false;
		let usage: UsageDoc | null = null;
		let scanned: { cost: unknown; providers: Set<string> } | null = null;
		const modelsFile = path.join(dir, `${pane}.models.json`);
		let modelsSig = "";

		const listModels = (ctx: any): unknown[] => {
			try {
				const list = ctx?.models?.list?.() ?? ctx?.modelRegistry?.getAvailable?.();
				return Array.isArray(list) ? list : [];
			} catch {
				return [];
			}
		};

		const isMain = (ctx: any): boolean => {
			try {
				return ctx?.agent?.kind === "main";
			} catch {
				return false;
			}
		};

		const meta = (): DocMeta => {
			let agentDir: string | null = null;
			try {
				agentDir = pi?.pi?.getAgentDir?.() ?? process.env.PI_CODING_AGENT_DIR ?? null;
			} catch {
				agentDir = process.env.PI_CODING_AGENT_DIR ?? null;
			}
			let sessionId: string | null = null;
			let sessionFile: string | null = null;
			let title: string | null = null;
			try {
				sessionId = ctxRef?.sessionManager?.getSessionId?.() ?? null;
			} catch {}
			try {
				sessionFile = ctxRef?.sessionManager?.getSessionFile?.() ?? null;
			} catch {}
			try {
				title = ctxRef?.sessionManager?.getSessionName?.() ?? pi?.getSessionName?.() ?? null;
			} catch {}
			let model: ModelRef | null = null;
			let thinking: string | null = null;
			let context: ContextDoc | null = null;
			try {
				model = modelRef(ctxRef?.models?.current?.() ?? ctxRef?.model);
			} catch {}
			try {
				thinking = thinkingName(pi?.getThinkingLevel?.());
			} catch {}
			try {
				context = contextDoc(ctxRef?.getContextUsage?.());
			} catch {}
			return {
				pane,
				pid: process.pid,
				sessionId,
				sessionFile,
				cwd: ctxRef?.cwd ?? process.cwd(),
				title: title || null,
				profile: profileFromAgentDir(agentDir),
				usage,
				model,
				thinking,
				context,
			};
		};

		/** Writes `<pane>.models.json` when the list of switchable models changed (or `force`). */
		const publishModels = (ctx: any, force = false) => {
			try {
				const doc = modelsDoc(listModels(ctx));
				const sig = JSON.stringify(doc);
				if (!force && sig === modelsSig) return;
				fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
				const tmp = `${modelsFile}.tmp.${process.pid}`;
				fs.writeFileSync(tmp, sig, { mode: 0o600 });
				fs.renameSync(tmp, modelsFile);
				modelsSig = sig;
			} catch {}
		};

		const put = (doc: Record<string, unknown>) => {
			fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
			const tmp = `${file}.tmp.${process.pid}`;
			fs.writeFileSync(tmp, JSON.stringify(doc), { mode: 0o600 });
			fs.renameSync(tmp, file);
		};

		/**
		 * Re-reads omp's usage totals: the snapshot is a cheap copy, so every call takes one; the
		 * session entries are walked (for the providers that priced it) only when the cost moved.
		 */
		const refreshUsage = (ctx: any) => {
			try {
				const sm = ctx?.sessionManager;
				const stats = sm?.getUsageStatistics?.();
				if (!stats) return;
				if (!scanned || scanned.cost !== stats.cost) {
					const providers = new Set<string>();
					const entries = sm?.getEntries?.();
					if (Array.isArray(entries)) {
						for (const e of entries) {
							const p = pricedProvider(e);
							if (p) providers.add(p);
						}
					}
					scanned = { cost: stats.cost, providers };
				}
				usage = usageDoc(stats, scanned.providers);
			} catch {}
		};

		const write = (force: boolean) => {
			if (dead) return;
			try {
				const t = nowS();
				const doc = toDoc(model, meta(), t);
				const sig = JSON.stringify({ ...doc, updated: 0 });
				if (!force && sig === lastSig && t - lastWrite < HEARTBEAT_S) return;
				put(doc);
				lastSig = sig;
				lastWrite = t;
			} catch {}
		};

		const jobCount = (ctx: any): number | undefined => {
			try {
				const snap = ctx?.getAsyncJobSnapshot?.();
				if (!snap || !Array.isArray(snap.running)) return undefined;
				return snap.running.filter((j: any) => !j?.status || j.status === "running").length;
			} catch {
				return undefined;
			}
		};

		const apply = (ev: HiveEvent) => {
			try {
				model = reduce(model, ev, nowS());
				if (ev.type === "agent_end" || ev.type === "session_stop") refreshUsage(ctxRef);
				write(false);
			} catch {}
		};

		// Sync only: it also runs from process.on("exit").
		const markExited = () => {
			if (dead) return;
			dead = true;
			try {
				refreshUsage(ctxRef);
				put(exitedDoc(model, meta(), nowS()));
			} catch {}
			try {
				fs.rmSync(modelsFile, { force: true });
			} catch {}
		};

		const cmdCtx = (ctx: any): CmdCtx => ({
			resolvePlanReview: (reviewId, choice, input) => ctx.resolvePlanReview(reviewId, choice, input),
			setModel: async id => {
				const target = findModel(listModels(ctx), id);
				if (!target || typeof pi.setModel !== "function") return false;
				return (await pi.setModel(target)) !== false;
			},
			setThinking: async level => {
				if (typeof pi.setThinkingLevel !== "function") throw new Error("not supported");
				await pi.setThinkingLevel(level);
			},
		});

		const pollCommands = async (ctx: any) => {
			if (busyCmd) return;
			busyCmd = true;
			try {
				let names: string[] = [];
				try {
					names = fs.readdirSync(dir).filter(n => isCmdFile(n, pane)).sort();
				} catch {
					return;
				}
				for (const name of names) {
					const p = path.join(dir, name);
					let parsed: unknown = null;
					try {
						parsed = JSON.parse(fs.readFileSync(p, "utf8"));
					} catch {}
					try {
						fs.rmSync(p, { force: true });
					} catch {}
					if (parsed === null) continue;
					let err: string | null = null;
					try {
						err = await handleCommand(parsed, cmdCtx(ctx));
					} catch (e) {
						err = `command failed: ${e instanceof Error ? e.message : String(e)}`;
					}
					if (err) model = withError(model, err);
					write(true);
				}
			} finally {
				busyCmd = false;
			}
		};

		const tick = (ctx: any) => {
			try {
				if (dead) return;
				refreshUsage(ctx);
				publishModels(ctx);
				const jobs = jobCount(ctx);
				apply({ type: "tick", jobs: jobs ?? model.jobs });
				write(false); // heartbeat
				void pollCommands(ctx).catch(() => {});
			} catch {}
		};

		const guard =
			(fn: (e: any, ctx: any) => void) =>
			(e: any, ctx: any) => {
				try {
					if (!isMain(ctx)) return;
					ctxRef = ctx;
					fn(e, ctx);
				} catch {}
			};

		pi.on(
			"session_start",
			guard((_e, ctx) => {
				// a new session in the same process (after /new, a resume) publishes afresh
				if (dead) {
					dead = false;
					model = initialModel(nowS());
				}
				usage = null;
				scanned = null;
				refreshUsage(ctx);
				publishModels(ctx, true);
				write(true);
				const pr = ctx?.getPendingPlanReview?.();
				if (pr) apply({ type: "plan_review_requested", review: pr });
				if (!timer) timer = ctx.setInterval(() => tick(ctx), TICK_MS);
			}),
		);
		pi.on(
			"agent_start",
			guard(() => apply({ type: "agent_start" })),
		);
		pi.on(
			"turn_end",
			guard((_e, ctx) => {
				refreshUsage(ctx); // omp has persisted the turn's message: its totals include it now
				write(false);
			}),
		);
		pi.on(
			"agent_end",
			guard((_e, ctx) => apply({ type: "agent_end", jobs: jobCount(ctx) })),
		);
		pi.on(
			"session_stop",
			guard((_e, ctx) => apply({ type: "session_stop", jobs: jobCount(ctx) })),
		);
		pi.on(
			"tool_execution_start",
			guard(e => apply({ type: "tool_start", id: String(e?.toolCallId ?? ""), name: String(e?.toolName ?? ""), args: e?.args })),
		);
		pi.on(
			"tool_execution_end",
			guard((e, ctx) => {
				let errorText = "";
				if (e?.isError) {
					const first = Array.isArray(e?.result?.content) ? e.result.content.find((c: any) => c?.type === "text") : null;
					errorText = typeof first?.text === "string" ? first.text : "";
				}
				apply({ type: "tool_end", id: String(e?.toolCallId ?? ""), name: e?.toolName, isError: !!e?.isError, errorText });
				const jobs = jobCount(ctx);
				if (jobs !== undefined) apply({ type: "tick", jobs });
			}),
		);
		pi.on(
			"tool_approval_requested",
			guard(e => {
				const tool = String(e?.toolName ?? e?.tool ?? e?.name ?? "tool");
				apply({ type: "approval_requested", tool, summary: summarizeArgs(e?.args ?? e?.input ?? e?.arguments) });
			}),
		);
		pi.on(
			"tool_approval_resolved",
			guard(() => apply({ type: "approval_resolved" })),
		);
		pi.on(
			"plan_review_requested",
			guard(e => apply({ type: "plan_review_requested", review: e })),
		);
		pi.on(
			"plan_review_resolved",
			guard(() => apply({ type: "plan_review_resolved" })),
		);
		pi.on("session_shutdown", (_e: any, ctx: any) => {
			try {
				if (!isMain(ctx)) return;
				try {
					if (timer) ctx.clearTimer(timer);
				} catch {}
				timer = null;
				markExited();
			} catch {}
		});

		// best-effort mark if the process ends without session_shutdown (sync-only in 'exit')
		process.on("exit", () => {
			try {
				if (ctxRef) markExited();
			} catch {}
		});
	} catch {
		// never throw into omp
	}
}
