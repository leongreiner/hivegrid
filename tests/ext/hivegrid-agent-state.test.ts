import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import factory, {
	type HiveEvent,
	handleCommand,
	initialModel,
	isCmdFile,
	profileFromAgentDir,
	reduce,
	toDoc,
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
		expect(profileFromAgentDir("/h/.omp/profiles/work/agent", {})).toBe("work");
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
			{ pane: 3, pid: 9, sessionId: "s", cwd: "/x", title: null, profile: null },
			2,
		);
		expect(Object.keys(doc).sort()).toEqual(
			["cwd", "error", "pane", "pending", "pid", "profile", "reason", "sessionId", "since", "state", "title", "updated", "v"].sort(),
		);
		expect(doc.v).toBe(1);
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
			const pi = { on: (n: string, h: (e: unknown, ctx: unknown) => unknown) => (handlers[n] = h), pi: { getAgentDir: () => "/h/.omp/profiles/team/agent" } };
			const resolved: unknown[][] = [];
			let tickFn: (() => void) | null = null;
			const mkCtx = (kind: string) => ({
				agent: { kind },
				cwd: "/work",
				sessionManager: { getSessionId: () => "sid", getSessionName: () => "T" },
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
				v: 1, pane: 4242, state: "idle", profile: "team", cwd: "/work", sessionId: "sid", title: "T", pending: null,
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
});
