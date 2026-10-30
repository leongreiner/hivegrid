import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import factory, {
	contextDoc,
	costKindOf,
	findModel,
	type HiveEvent,
	limitFromEntries,
	limitOfEntry,
	handleCommand,
	initialModel,
	isCmdFile,
	modelRef,
	modelsDoc,
	pricedProvider,
	profileFromAgentDir,
	reduce,
	THINKING_LEVELS,
	thinkingName,
	toDoc,
	usageDoc,
	withError,
} from "../../ext/hivegrid-agent-state";

const run = (events: [HiveEvent, number][], t0 = 1000) => {
	let m = initialModel(t0);
	const seen: string[] = [];
	for (const [ev, t] of events) {
		m = reduce(m, ev, t);
		seen.push(`${m.state}|${m.reason ?? ""}`);
	}
	return { m, seen };
};

describe("reduce", () => {
	test("idle -> working -> busy -> working -> done", () => {
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "tool_start", id: "a", name: "bash", args: { command: "npm test" } }, 1002],
			[{ type: "tick", jobs: 0 }, 1005],
			[{ type: "tick", jobs: 0 }, 1011],
			[{ type: "tool_end", id: "a", name: "bash" }, 1050],
			[{ type: "session_stop", jobs: 0 }, 1051],
			[{ type: "agent_end", jobs: 0 }, 1051],
		]);
		expect(seen).toEqual([
			"working|",
			"working|bash: npm test",
			"working|bash: npm test",
			"busy|bash: npm test (9s)",
			"working|",
			"done|",
			"done|",
		]);
		expect(m.since).toBe(1051);
	});

	test("since only moves when the state changes", () => {
		const { m } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "tick", jobs: 0 }, 1003],
			[{ type: "tick", jobs: 0 }, 1004],
		]);
		expect(m.state).toBe("working");
		expect(m.since).toBe(1001);
	});

	test("initial state is idle and stays idle on ticks", () => {
		const { m } = run([[{ type: "tick", jobs: 0 }, 1010]]);
		expect(m.state).toBe("idle");
		expect(m.since).toBe(1000);
	});

	test("agent_end with background jobs is busy, then done once they finish", () => {
		const { seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "agent_end", jobs: 2 }, 1002],
			[{ type: "tick", jobs: 2 }, 1003],
			[{ type: "tick", jobs: 1 }, 1004],
			[{ type: "tick", jobs: 0 }, 1005],
			[{ type: "agent_start" }, 1006],
		]);
		expect(seen).toEqual([
			"working|",
			"busy|2 background jobs",
			"busy|2 background jobs",
			"busy|1 background job",
			"done|",
			"working|",
		]);
	});

	test("task and ask tools never turn busy", () => {
		const { m } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "tool_start", id: "t", name: "task", args: { description: "sub" } }, 1002],
			[{ type: "tick", jobs: 0 }, 1100],
		]);
		expect(m.state).toBe("working");
	});

	test("approval pending -> attention -> resolved", () => {
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "approval_requested", tool: "bash", summary: "rm -rf x" }, 1002],
			[{ type: "approval_resolved" }, 1010],
		]);
		expect(seen).toEqual(["working|", "attention|approval: bash", "working|"]);
		expect(m.pending).toBeNull();
		const mid = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "approval_requested", tool: "bash", summary: "rm -rf x" }, 1002],
		]).m;
		expect(mid.pending).toEqual({ kind: "approval", tool: "bash", summary: "rm -rf x" });
	});

	test("ask tool -> attention with question/options until tool_end", () => {
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[
				{
					type: "tool_start",
					id: "q",
					name: "ask",
					args: { questions: [{ question: "Which?", options: [{ label: "A" }, { label: "B" }] }] },
				},
				1002,
			],
			[{ type: "tool_end", id: "q", name: "ask" }, 1003],
		]);
		expect(seen).toEqual(["working|", "attention|ask", "working|"]);
		expect(m.pending).toBeNull();
		const mid = reduce(
			reduce(initialModel(0), { type: "agent_start" }, 1),
			{ type: "tool_start", id: "q", name: "ask", args: { question: "Which?", options: ["x", "y"] } },
			2,
		);
		expect(mid.pending).toEqual({ kind: "ask", question: "Which?", options: ["x", "y"] });
	});

	test("plan review pending survives loop end, cleared on resolve", () => {
		const review = {
			reviewId: "r1",
			title: "My plan",
			choices: [
				{ id: "execute", label: "Approve and execute" },
				{ id: "refine", label: "Refine" },
			],
		};
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "plan_review_requested", review }, 1002],
			[{ type: "agent_end", jobs: 0 }, 1003],
			[{ type: "plan_review_resolved" }, 1004],
		]);
		expect(seen).toEqual(["working|", "attention|plan review", "attention|plan review", "done|"]);
		expect(m.pending).toBeNull();
		const mid = reduce(initialModel(0), { type: "plan_review_requested", review }, 1);
		expect(mid.pending).toEqual({
			kind: "plan_review",
			reviewId: "r1",
			title: "My plan",
			choices: review.choices,
		});
	});

	test("plan review without a choice list gets omp's fixed answers", () => {
		const m = reduce(initialModel(0), { type: "plan_review_requested", review: { reviewId: "r2", title: "t" } }, 1);
		const p = m.pending;
		expect(p?.kind).toBe("plan_review");
		if (p?.kind !== "plan_review") return;
		expect(p.choices.map(c => c.id)).toEqual(["execute", "compact", "keep", "refine", "save"]);
	});

	test("malformed plan review is ignored", () => {
		const m = reduce(initialModel(0), { type: "plan_review_requested", review: { title: "x" } }, 1);
		expect(m.pending).toBeNull();
		expect(m.state).toBe("idle");
	});

	test("error set on failing tool and cleared on next success", () => {
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "tool_start", id: "a", name: "bash", args: { command: "false" } }, 1002],
			[{ type: "tool_end", id: "a", isError: true, errorText: "exit 1" }, 1003],
		]);
		expect(m.error).toBe("exit 1");
		expect(seen.length).toBe(3);
		const ok = reduce(m, { type: "tool_start", id: "b", name: "read", args: {} }, 1004);
		expect(reduce(ok, { type: "tool_end", id: "b" }, 1005).error).toBeNull();
		expect(withError(m, "x").error).toBe("x");
	});

	test("does not mutate its input", () => {
		const m0 = initialModel(5);
		const snapshot = JSON.stringify(m0);
		reduce(m0, { type: "agent_start" }, 6);
		expect(JSON.stringify(m0)).toBe(snapshot);
	});
});

describe("helpers", () => {
	test("profileFromAgentDir", () => {
		expect(profileFromAgentDir("/h/.omp/profiles/alpha/agent", {})).toBe("alpha");
		expect(profileFromAgentDir("/h/.omp/agent", {})).toBeNull();
		expect(profileFromAgentDir("/h/.omp/agent", { OMP_PROFILE: "x" })).toBe("x");
	});
	test("isCmdFile", () => {
		expect(isCmdFile("19.cmd.abc.json", 19)).toBe(true);
		expect(isCmdFile("190.cmd.abc.json", 19)).toBe(false);
		expect(isCmdFile("19.json", 19)).toBe(false);
	});
	test("toDoc has the contract keys", () => {
		const doc = toDoc(
			initialModel(1),
			{ pane: 3, pid: 9, sessionId: "s", sessionFile: "/s.jsonl", cwd: "/x", title: null, profile: null },
			2,
		);
		expect(Object.keys(doc).sort()).toEqual(
			[
				"context", "cwd", "error", "limit", "model", "pane", "pending", "pid", "profile", "reason", "sessionFile", "sessionId", "since", "state",
				"thinking", "title", "updated", "usage", "v",
			].sort(),
		);
		expect(doc.model).toBeNull();
		expect(doc.thinking).toBeNull();
		expect(doc.context).toBeNull();
		expect(doc.sessionFile).toBe("/s.jsonl");
		expect(doc.v).toBe(1);
	});
});

describe("usage", () => {
	const stats = { input: 10, output: 52, cacheRead: 100, cacheWrite: 8333, totalTokens: 8495, cost: 0.5, subagentCost: 0.125, premiumRequests: 0 };
	const meta = { pane: 3, pid: 9, sessionId: "s", sessionFile: null, cwd: "/x", title: null, profile: null };

	test("usageDoc copies only what omp reported", () => {
		expect(usageDoc(stats, ["anthropic"])).toEqual({
			input: 10, output: 52, cacheRead: 100, cacheWrite: 8333, total: 8495, cost: 0.5, subagentCost: 0.125, costKind: "estimated",
		});
	});

	test("a field omp did not report is null, not 0", () => {
		const doc = usageDoc({ input: 7, output: 1, cost: 0 }, []);
		expect(doc).toMatchObject({ input: 7, output: 1, cacheRead: null, cacheWrite: null, total: null, subagentCost: null, cost: 0 });
		expect(doc?.costKind).toBeNull();
	});

	test("nothing reported -> null; junk values are dropped", () => {
		expect(usageDoc(undefined, [])).toBeNull();
		expect(usageDoc({}, [])).toBeNull();
		expect(usageDoc({ input: "5", output: -1, totalTokens: NaN, cost: Infinity }, [])).toBeNull();
		expect(usageDoc({ input: 5, output: "x", cost: -2 }, [])).toMatchObject({ input: 5, output: null, cost: null });
	});

	test("costKind: gateway charges are reported, catalog prices estimated, both mixed", () => {
		expect(costKindOf([])).toBeNull();
		expect(costKindOf(["openrouter"])).toBe("reported");
		expect(costKindOf(["openrouter", "cline-pass"])).toBe("reported");
		expect(costKindOf(["anthropic"])).toBe("estimated");
		expect(costKindOf(["openrouter", "anthropic"])).toBe("mixed");
		expect(usageDoc({ input: 1, cost: 1 }, ["openrouter"])?.costKind).toBe("reported");
		// a subagent's spend names no provider, so a gateway session with subagents is mixed
		expect(usageDoc({ input: 1, cost: 1, subagentCost: 0.5 }, ["openrouter"])?.costKind).toBe("mixed");
	});

	test("pricedProvider reads session entries, ignoring free and non-usage ones", () => {
		const msg = (provider: string, total: number) => ({ type: "message", message: { role: "assistant", provider, usage: { cost: { total } } } });
		expect(pricedProvider(msg("anthropic", 0.01))).toBe("anthropic");
		expect(pricedProvider(msg("anthropic", 0))).toBeNull();
		expect(pricedProvider({ type: "model_usage", provider: "openrouter", usage: { cost: { total: 0.2 } } })).toBe("openrouter");
		expect(pricedProvider({ type: "message", message: { role: "toolResult", toolName: "task", details: { usage: { cost: { total: 1 } } } } })).toBe("?");
		expect(pricedProvider({ type: "message", message: { role: "user", content: "hi" } })).toBeNull();
		expect(pricedProvider(null)).toBeNull();
	});

	test("toDoc carries usage, null when absent", () => {
		expect(toDoc(initialModel(1), meta, 2).usage).toBeNull();
		expect(toDoc(initialModel(1), { ...meta, usage: usageDoc(stats, ["anthropic"]) }, 2).usage).toMatchObject({ input: 10, cost: 0.5 });
	});
});

describe("model, thinking, context", () => {
	const sonnet = { provider: "anthropic", id: "claude-sonnet-5-5", name: "Sonnet 5.5", reasoning: true, secret: "x" };
	const local = { provider: "local", id: "tiny", reasoning: false };

	test("modelRef is provider/id plus a name, nothing else", () => {
		expect(modelRef(sonnet)).toEqual({ id: "anthropic/claude-sonnet-5-5", name: "Sonnet 5.5", provider: "anthropic" });
		expect(modelRef(local)).toEqual({ id: "local/tiny", name: "tiny", provider: "local" });
		expect(modelRef({ id: "x" })).toBeNull();
		expect(modelRef(null)).toBeNull();
		expect(modelRef({ provider: "p\x1b[31m", id: "i", name: "a\nb" })).toEqual({ id: "p [31m/i", name: "a b", provider: "p [31m" });
	});

	test("modelsDoc lists unique models with the reasoning flag and the levels", () => {
		const doc = modelsDoc([sonnet, local, sonnet, 5, {}]) as { v: number; thinking_levels: string[]; models: Record<string, unknown>[] };
		expect(doc.v).toBe(1);
		expect(doc.thinking_levels).toEqual(THINKING_LEVELS);
		expect(doc.models).toEqual([
			{ id: "anthropic/claude-sonnet-5-5", name: "Sonnet 5.5", provider: "anthropic", reasoning: true },
			{ id: "local/tiny", name: "tiny", provider: "local", reasoning: false },
		]);
		expect((modelsDoc(undefined) as { models: unknown[] }).models).toEqual([]);
	});

	test("findModel returns the omp object, not a copy", () => {
		expect(findModel([local, sonnet], "anthropic/claude-sonnet-5-5")).toBe(sonnet);
		expect(findModel([local], "anthropic/claude-sonnet-5-5")).toBeUndefined();
	});

	test("contextDoc needs tokens and a window; percent is computed when omp gives none", () => {
		expect(contextDoc({ tokens: 50, contextWindow: 200, percent: 25 })).toEqual({ tokens: 50, window: 200, pct: 25 });
		expect(contextDoc({ tokens: 1, contextWindow: 3, percent: null })).toEqual({ tokens: 1, window: 3, pct: 33.3 });
		expect(contextDoc({ tokens: null, contextWindow: 200, percent: null })).toBeNull();
		expect(contextDoc({ tokens: 0, contextWindow: 0 })).toBeNull();
		expect(contextDoc(undefined)).toBeNull();
	});

	test("thinkingName passes known levels only", () => {
		expect(thinkingName("high")).toBe("high");
		expect(thinkingName("turbo")).toBeNull();
		expect(thinkingName(3)).toBeNull();
	});

	test("set_model / set_thinking commands", async () => {
		const calls: string[] = [];
		const ctx = {
			resolvePlanReview: () => true,
			setModel: (id: string) => (calls.push(`m:${id}`), id === "a/b"),
			setThinking: (level: string) => void calls.push(`t:${level}`),
		};
		expect(await handleCommand({ type: "set_model", model: "a/b" }, ctx)).toBeNull();
		expect(await handleCommand({ type: "set_model", model: "a/zzz" }, ctx)).toContain("not switched");
		expect(await handleCommand({ type: "set_model" }, ctx)).toContain("malformed");
		expect(await handleCommand({ type: "set_thinking", level: "high" }, ctx)).toBeNull();
		expect(await handleCommand({ type: "set_thinking", level: "turbo" }, ctx)).toContain("malformed");
		expect(calls).toEqual(["m:a/b", "m:a/zzz", "t:high"]);
		const bare = { resolvePlanReview: () => true };
		expect(await handleCommand({ type: "set_model", model: "a/b" }, bare)).toContain("cannot");
		expect(await handleCommand({ type: "set_thinking", level: "low" }, bare)).toContain("cannot");
	});
});

describe("handleCommand", () => {
	const mk = (ret: boolean) => {
		const calls: unknown[][] = [];
		return {
			calls,
			ctx: {
				resolvePlanReview: (...a: unknown[]) => {
					calls.push(a);
					return ret;
				},
			},
		};
	};
	test("routes plan_review with feedback/role", async () => {
		const { ctx, calls } = mk(true);
		const err = await handleCommand(
			{ type: "plan_review", reviewId: "r1", choice: "refine", feedback: "more", role: "slow" },
			ctx,
		);
		expect(err).toBeNull();
		expect(calls).toEqual([["r1", "refine", { feedback: "more", role: "slow" }]]);
	});
	test("false return -> error message", async () => {
		const { ctx } = mk(false);
		expect(await handleCommand({ type: "plan_review", reviewId: "r1", choice: "execute" }, ctx)).toContain("rejected");
	});
	test("unknown type ignored; malformed reported", async () => {
		const { ctx, calls } = mk(true);
		expect(await handleCommand({ type: "other" }, ctx)).toBeNull();
		expect(await handleCommand({ type: "plan_review" }, ctx)).toContain("malformed");
		expect(calls).toEqual([]);
	});
});

describe("extension wiring (fake pi)", () => {
	test("writes, consumes cmd files, marks exited on shutdown, publishes a new session; ignores subagents", async () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hg-ext-"));
		const oldEnv = { x: process.env.XDG_RUNTIME_DIR, p: process.env.WEZTERM_PANE };
		process.env.XDG_RUNTIME_DIR = dir;
		process.env.WEZTERM_PANE = "4242";
		try {
			const handlers: Record<string, (e: unknown, ctx: unknown) => unknown> = {};
			const pi = { on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h), pi: { getAgentDir: () => "/h/.omp/profiles/beta/agent" } };
			const resolved: unknown[][] = [];
			let tickFn: (() => void) | null = null;
			const mkCtx = (kind: string) => ({
				agent: { kind },
				cwd: "/work",
				sessionManager: { getSessionId: () => "sid", getSessionFile: () => "/work/sid.jsonl", getSessionName: () => "T" },
				getAsyncJobSnapshot: () => ({ running: [] }),
				getPendingPlanReview: () => undefined,
				resolvePlanReview: (...a: unknown[]) => (resolved.push(a), false),
				setInterval: (fn: () => void) => ((tickFn = fn), 1),
				clearTimer: () => {},
			});
			factory(pi);
			const main = mkCtx("main");
			const stateFile = path.join(dir, "hivegrid-agents", "4242.json");
			await handlers.session_start({}, main);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8"))).toMatchObject({
				v: 1, pane: 4242, state: "idle", profile: "beta", cwd: "/work", sessionId: "sid", sessionFile: "/work/sid.jsonl", title: "T", pending: null,
			});
			await handlers.agent_start({}, mkCtx("sub"));
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8")).state).toBe("idle");
			await handlers.agent_start({}, main);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8")).state).toBe("working");

			const cmd = path.join(dir, "hivegrid-agents", "4242.cmd.u1.json");
			const other = path.join(dir, "hivegrid-agents", "7.cmd.u1.json");
			fs.writeFileSync(cmd, JSON.stringify({ type: "plan_review", reviewId: "r", choice: "execute" }));
			fs.writeFileSync(other, "{}");
			tickFn!();
			await new Promise(r => setTimeout(r, 50));
			expect(fs.existsSync(cmd)).toBe(false);
			expect(fs.existsSync(other)).toBe(true);
			expect(resolved).toEqual([["r", "execute", undefined]]);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8")).error).toContain("rejected");

			await handlers.session_shutdown({}, main);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8"))).toMatchObject({
				pane: 4242, state: "exited", pending: null, reason: null, error: null,
			});
			await handlers.agent_start({}, main);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8")).state).toBe("exited");
			await handlers.session_start({}, main);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8"))).toMatchObject({ state: "idle", error: null });
			await handlers.session_shutdown({}, main); // so the process "exit" hook has nothing left to write
		} finally {
			process.env.XDG_RUNTIME_DIR = oldEnv.x;
			if (oldEnv.p === undefined) delete process.env.WEZTERM_PANE;
			else process.env.WEZTERM_PANE = oldEnv.p;
			if (oldEnv.x === undefined) delete process.env.XDG_RUNTIME_DIR;
			fs.rmSync(dir, { recursive: true, force: true });
		}
	});

	test("no WEZTERM_PANE -> registers nothing", () => {
		const old = process.env.WEZTERM_PANE;
		delete process.env.WEZTERM_PANE;
		try {
			let n = 0;
			factory({ on: () => n++ });
			expect(n).toBe(0);
		} finally {
			if (old !== undefined) process.env.WEZTERM_PANE = old;
		}
	});

	test("publishes omp's usage totals and re-reads them when the cost moves", async () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hg-ext-"));
		const oldEnv = { x: process.env.XDG_RUNTIME_DIR, p: process.env.WEZTERM_PANE };
		process.env.XDG_RUNTIME_DIR = dir;
		process.env.WEZTERM_PANE = "4243";
		try {
			const handlers: Record<string, (e: unknown, ctx: unknown) => unknown> = {};
			const pi = { on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h), pi: { getAgentDir: () => "/h/.omp/agent" } };
			let stats: Record<string, number> = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: 0, subagentCost: 0 };
			let entries: unknown[] = [];
			let scans = 0;
			let tickFn: (() => void) | null = null;
			const ctx = {
				agent: { kind: "main" },
				cwd: "/work",
				sessionManager: {
					getSessionId: () => "sid",
					getSessionName: () => "T",
					getUsageStatistics: () => ({ ...stats }),
					getEntries: () => (scans++, entries),
				},
				getAsyncJobSnapshot: () => ({ running: [] }),
				getPendingPlanReview: () => undefined,
				setInterval: (fn: () => void) => ((tickFn = fn), 1),
				clearTimer: () => {},
			};
			factory(pi);
			const stateFile = path.join(dir, "hivegrid-agents", "4243.json");
			const read = () => JSON.parse(fs.readFileSync(stateFile, "utf8"));
			await handlers.session_start({}, ctx);
			expect(read().usage).toMatchObject({ input: 0, cost: 0, costKind: null });

			stats = { input: 10, output: 52, cacheRead: 0, cacheWrite: 8333, totalTokens: 8395, cost: 0.05, subagentCost: 0 };
			entries = [{ type: "message", message: { role: "assistant", provider: "anthropic", usage: { cost: { total: 0.05 } } } }];
			await handlers.turn_end({}, ctx);
			expect(read().usage).toEqual({
				input: 10, output: 52, cacheRead: 0, cacheWrite: 8333, total: 8395, cost: 0.05, subagentCost: 0, costKind: "estimated",
			});

			const before = scans;
			tickFn!();
			tickFn!();
			expect(scans).toBe(before); // the cost did not move: the entries are not walked again

			await handlers.session_shutdown({}, ctx);
			expect(read()).toMatchObject({ state: "exited", usage: { total: 8395, cost: 0.05 } });
		} finally {
			process.env.XDG_RUNTIME_DIR = oldEnv.x;
			if (oldEnv.p === undefined) delete process.env.WEZTERM_PANE;
			else process.env.WEZTERM_PANE = oldEnv.p;
			if (oldEnv.x === undefined) delete process.env.XDG_RUNTIME_DIR;
			fs.rmSync(dir, { recursive: true, force: true });
		}
	});

	test("an omp without usage statistics still publishes the state, usage null", async () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hg-ext-"));
		const oldEnv = { x: process.env.XDG_RUNTIME_DIR, p: process.env.WEZTERM_PANE };
		process.env.XDG_RUNTIME_DIR = dir;
		process.env.WEZTERM_PANE = "4244";
		try {
			const handlers: Record<string, (e: unknown, ctx: unknown) => unknown> = {};
			factory({ on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h), pi: {} });
			const ctx = {
				agent: { kind: "main" },
				cwd: "/work",
				sessionManager: { getSessionId: () => "sid", getSessionName: () => "T" },
				getAsyncJobSnapshot: () => ({ running: [] }),
				getPendingPlanReview: () => undefined,
				setInterval: () => 1,
				clearTimer: () => {},
			};
			await handlers.session_start({}, ctx);
			await handlers.turn_end({}, ctx);
			expect(JSON.parse(fs.readFileSync(path.join(dir, "hivegrid-agents", "4244.json"), "utf8"))).toMatchObject({ state: "idle", usage: null });
			await handlers.session_shutdown({}, ctx);
		} finally {
			process.env.XDG_RUNTIME_DIR = oldEnv.x;
			if (oldEnv.p === undefined) delete process.env.WEZTERM_PANE;
			else process.env.WEZTERM_PANE = oldEnv.p;
			if (oldEnv.x === undefined) delete process.env.XDG_RUNTIME_DIR;
			fs.rmSync(dir, { recursive: true, force: true });
		}
	});
});

describe("extension wiring: models and commands", () => {
	test("publishes model, thinking, context and the models file; executes set_model and set_thinking", async () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hg-ext-"));
		const oldEnv = { x: process.env.XDG_RUNTIME_DIR, p: process.env.WEZTERM_PANE };
		process.env.XDG_RUNTIME_DIR = dir;
		process.env.WEZTERM_PANE = "4244";
		try {
			const handlers: Record<string, (e: unknown, ctx: unknown) => unknown> = {};
			const a = { provider: "anthropic", id: "one", name: "One", reasoning: true };
			const b = { provider: "openai", id: "two", name: "Two", reasoning: false };
			let current = a;
			let level = "medium";
			let list = [a, b];
			const pi = {
				on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h),
				pi: { getAgentDir: () => "/h/.omp/agent" },
				setModel: async (m: typeof a) => ((current = m), true),
				getThinkingLevel: () => level,
				setThinkingLevel: (l: string) => void (level = l),
			};
			let tickFn: (() => void) | null = null;
			const ctx = {
				agent: { kind: "main" },
				cwd: "/work",
				sessionManager: { getSessionId: () => "sid", getSessionName: () => "T" },
				models: { list: () => list, current: () => current },
				getContextUsage: () => ({ tokens: 10, contextWindow: 100, percent: 10 }),
				getAsyncJobSnapshot: () => ({ running: [] }),
				getPendingPlanReview: () => undefined,
				resolvePlanReview: () => true,
				setInterval: (fn: () => void) => ((tickFn = fn), 1),
				clearTimer: () => {},
			};
			factory(pi);
			const base = path.join(dir, "hivegrid-agents");
			const stateFile = path.join(base, "4244.json");
			const modelsFile = path.join(base, "4244.models.json");
			await handlers.session_start({}, ctx);
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8"))).toMatchObject({
				model: { id: "anthropic/one", name: "One", provider: "anthropic" }, thinking: "medium", context: { tokens: 10, window: 100, pct: 10 },
			});
			expect(fs.statSync(modelsFile).mode & 0o777).toBe(0o600);
			expect(JSON.parse(fs.readFileSync(modelsFile, "utf8")).models.map((m: { id: string }) => m.id)).toEqual(["anthropic/one", "openai/two"]);

			fs.writeFileSync(path.join(base, "4244.cmd.m1.json"), JSON.stringify({ type: "set_model", model: "openai/two" }));
			fs.writeFileSync(path.join(base, "4244.cmd.m2.json"), JSON.stringify({ type: "set_thinking", level: "high" }));
			tickFn!();
			await new Promise(r => setTimeout(r, 50));
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8"))).toMatchObject({ model: { id: "openai/two" }, thinking: "high", error: null });

			fs.writeFileSync(path.join(base, "4244.cmd.m3.json"), JSON.stringify({ type: "set_model", model: "openai/missing" }));
			tickFn!();
			await new Promise(r => setTimeout(r, 50));
			expect(JSON.parse(fs.readFileSync(stateFile, "utf8")).error).toContain("not switched");

			list = [b];
			tickFn!();
			expect(JSON.parse(fs.readFileSync(modelsFile, "utf8")).models.map((m: { id: string }) => m.id)).toEqual(["openai/two"]);

			await handlers.session_shutdown({}, ctx);
			expect(fs.existsSync(modelsFile)).toBe(false);
		} finally {
			process.env.XDG_RUNTIME_DIR = oldEnv.x;
			if (oldEnv.p === undefined) delete process.env.WEZTERM_PANE;
			else process.env.WEZTERM_PANE = oldEnv.p;
			if (oldEnv.x === undefined) delete process.env.XDG_RUNTIME_DIR;
			fs.rmSync(dir, { recursive: true, force: true });
		}
	});
});

// Shaped like the entries of a real omp session file (ids, usage and request controls left out).
const limitEntry = (over: Record<string, unknown> = {}) => ({
	type: "message",
	timestamp: "2026-10-28T09:41:04.054Z",
	message: {
		role: "assistant",
		content: [],
		stopReason: "error",
		errorStatus: 429,
		errorMessage:
			'429 {"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your account\'s rate limit. Please try again later."},"request_id":"req_x"} retry-after-ms=14523000',
		timestamp: 1793086676483,
		completedAt: 1793086676845,
		...over,
	},
});
const userEntry = { type: "message", message: { role: "user", content: [{ type: "text", text: "go on" }] } };
const okEntry = { type: "message", message: { role: "assistant", content: [{ type: "text", text: "done" }], stopReason: "stop" } };

describe("usage limit", () => {
	test("a refused request with a long retry-after is the account's limit, reset = completion + retry-after", () => {
		expect(limitOfEntry(limitEntry())).toEqual({ hit_at: 1793086676.845, reset_at: 1793101199.845 });
	});

	test("what is not a limit stop", () => {
		expect(limitOfEntry(okEntry)).toBeNull();
		expect(limitOfEntry(userEntry)).toBeNull();
		expect(limitOfEntry(limitEntry({ stopReason: "aborted" }))).toBeNull();
		// another provider error and other 4xx/5xx
		expect(limitOfEntry(limitEntry({ errorStatus: 500, errorMessage: "500 internal" }))).toBeNull();
		expect(limitOfEntry(limitEntry({ errorStatus: undefined, errorMessage: "The socket connection was closed unexpectedly before the response completed" }))).toBeNull();
		// a 429 that reopens within seconds is a burst limit omp retries itself
		expect(limitOfEntry(limitEntry({ errorMessage: '429 {"error":{"type":"rate_limit_error"}} retry-after-ms=8000' }))).toBeNull();
		// a 429 that is not a rate or usage limit
		expect(limitOfEntry(limitEntry({ errorMessage: "429 too many open files" }))).toBeNull();
		expect(limitOfEntry(null)).toBeNull();
		expect(limitOfEntry({ type: "message", message: "text" })).toBeNull();
	});

	test("no retry-after: still a limit, without a reset time", () => {
		const e = limitEntry({ errorMessage: '429 {"error":{"type":"rate_limit_error"}}' });
		expect(limitOfEntry(e)).toEqual({ hit_at: 1793086676.845, reset_at: null });
		// the hit time falls back to the message, then the entry
		expect(limitOfEntry(limitEntry({ completedAt: undefined }))?.hit_at).toBe(1793086676.483);
		const bare = limitEntry({ completedAt: undefined, timestamp: undefined });
		expect(limitOfEntry(bare)?.hit_at).toBe(1793180464.054);
		expect(limitOfEntry({ ...bare, timestamp: undefined, message: { ...bare.message, timestamp: undefined } })).toBeNull();
	});

	test("an absurd retry-after is a limit without a reset time", () => {
		for (const digits of ["9".repeat(5000), "9".repeat(13), "1" + "0".repeat(11)]) {
			const e = limitEntry({ errorMessage: '429 {"error":{"type":"rate_limit_error"}} retry-after-ms=' + digits });
			expect(limitOfEntry(e)).toEqual({ hit_at: 1793086676.845, reset_at: null });
		}
	});

	test("only the last message decides; non-message entries are skipped", () => {
		const note = { type: "model_usage", usage: {} };
		expect(limitFromEntries([okEntry, limitEntry(), note])).toMatchObject({ reset_at: 1793101199.845 });
		expect(limitFromEntries([limitEntry(), userEntry])).toBeNull(); // the chat went on after the refusal
		expect(limitFromEntries([limitEntry(), okEntry])).toBeNull();
		expect(limitFromEntries([])).toBeNull();
		expect(limitFromEntries(undefined)).toBeNull();
	});

	test("the loop ends in `limit`, not `done`; precedence, and a new loop clears it", () => {
		const limit = { hit_at: 1000, reset_at: 5000 };
		const { m, seen } = run([
			[{ type: "agent_start" }, 1001],
			[{ type: "session_stop", jobs: 0, limit }, 1002],
			[{ type: "agent_end", jobs: 0, limit }, 1002],
			[{ type: "tick", jobs: 0 }, 1010],
			[{ type: "agent_start" }, 1020],
		]);
		expect(seen).toEqual(["working|", "limit|usage limit", "limit|usage limit", "limit|usage limit", "working|"]);
		expect(m.limit).toBeNull();
		// since stays at the first limit event
		expect(run([[{ type: "agent_start" }, 1001], [{ type: "agent_end", limit }, 1002], [{ type: "agent_end", limit }, 1009]]).m.since).toBe(1002);
		// background jobs still running: busy; a plan review waiting: attention
		expect(run([[{ type: "agent_start" }, 1001], [{ type: "agent_end", jobs: 1, limit }, 1002]]).seen[1]).toBe("busy|1 background job");
		const review = { reviewId: "r", title: "P" };
		expect(run([[{ type: "agent_start" }, 1001], [{ type: "plan_review_requested", review }, 1002], [{ type: "agent_end", limit }, 1003]]).seen[2]).toBe("attention|plan review");
		// an ordinary end after a limited one is done again
		expect(run([[{ type: "agent_start" }, 1001], [{ type: "agent_end", limit }, 1002], [{ type: "agent_start" }, 1003], [{ type: "agent_end", jobs: 0 }, 1004]]).seen[3]).toBe("done|");
	});

	test("the state file carries the limit only while the state is `limit`", () => {
		const limit = { hit_at: 1000, reset_at: 5000 };
		const meta = { pane: 1, pid: 2, sessionId: null, sessionFile: null, cwd: "/w", title: null, profile: null };
		const limited = run([[{ type: "agent_start" }, 1001], [{ type: "agent_end", limit }, 1002]]).m;
		expect(toDoc(limited, meta, 1003)).toMatchObject({ state: "limit", reason: "usage limit", limit });
		const busy = run([[{ type: "agent_start" }, 1001], [{ type: "agent_end", limit, jobs: 1 }, 1002]]).m;
		expect(toDoc(busy, meta, 1003).limit).toBeNull();
		expect(toDoc(initialModel(1), meta, 2).limit).toBeNull();
	});

	test("wiring: agent_end reads the session and writes state `limit`; the next prompt clears it", async () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hg-ext-"));
		const oldEnv = { x: process.env.XDG_RUNTIME_DIR, p: process.env.WEZTERM_PANE };
		process.env.XDG_RUNTIME_DIR = dir;
		process.env.WEZTERM_PANE = "4251";
		try {
			const handlers: Record<string, (e: unknown, ctx: unknown) => unknown> = {};
			factory({ on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h), pi: {} });
			let entries: unknown[] = [userEntry];
			const ctx = {
				agent: { kind: "main" },
				cwd: "/work",
				sessionManager: { getSessionId: () => "sid", getSessionName: () => "T", getEntries: () => entries },
				getAsyncJobSnapshot: () => ({ running: [] }),
				getPendingPlanReview: () => undefined,
				setInterval: () => 1,
				clearTimer: () => {},
			};
			const read = () => JSON.parse(fs.readFileSync(path.join(dir, "hivegrid-agents", "4251.json"), "utf8"));
			await handlers.session_start({}, ctx);
			await handlers.agent_start({}, ctx);
			entries = [userEntry, limitEntry()];
			await handlers.session_stop({}, ctx);
			await handlers.agent_end({}, ctx);
			expect(read()).toMatchObject({ state: "limit", reason: "usage limit", limit: { hit_at: 1793086676.845, reset_at: 1793101199.845 } });
			await handlers.agent_start({}, ctx);
			expect(read()).toMatchObject({ state: "working", limit: null });
			entries = [userEntry, limitEntry(), userEntry, okEntry];
			await handlers.agent_end({}, ctx);
			expect(read()).toMatchObject({ state: "done", limit: null });
			// an omp without getEntries ends in done
			await handlers.agent_start({}, ctx);
			entries = undefined as unknown as unknown[];
			await handlers.agent_end({}, ctx);
			expect(read().state).toBe("done");
			await handlers.session_shutdown({}, ctx);
		} finally {
			process.env.XDG_RUNTIME_DIR = oldEnv.x;
			if (oldEnv.p === undefined) delete process.env.WEZTERM_PANE;
			else process.env.WEZTERM_PANE = oldEnv.p;
			if (oldEnv.x === undefined) delete process.env.XDG_RUNTIME_DIR;
			fs.rmSync(dir, { recursive: true, force: true });
		}
	});
});
