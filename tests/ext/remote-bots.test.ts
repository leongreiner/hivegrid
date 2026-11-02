import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";

// botView, untilText's formatter inputs and the schedule presets are plain functions inside the page's script (an IIFE): cut them out by
// their two-space indent, like remote-chat.test.ts does for mergeFiles.
const py = fs.readFileSync(path.join(import.meta.dir, "../../bin/hivegrid_remote_ui.py"), "utf8");
const cut = (name: string): string => {
	const m = py.match(new RegExp(`\\n {2}function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n {2}\\}\\n`));
	if (!m) throw new Error(`${name} not found`);
	return m[0];
};

interface Bot {
	state: string;
	running?: boolean;
	paused_reason?: string | null;
	done?: boolean;
	last?: { status: string } | null;
}
const botView = new Function(`${cut("botView")}\nreturn botView;`)() as (b: Bot) => { kind: string; label: string };
const rstat = new Function(`${cut("rstat")}\nreturn rstat;`)() as (s: unknown) => string;

describe("how a bot reads", () => {
	test("a live run is working whatever else is true", () => {
		expect(botView({ state: "working", running: true, paused_reason: "x", last: { status: "failed" } })).toEqual({ kind: "working", label: "running" });
	});

	test("the scheduler's states map onto the shared vocabulary", () => {
		expect(botView({ state: "schedule" })).toEqual({ kind: "schedule", label: "waiting" });
		expect(botView({ state: "done", last: { status: "ok" } })).toEqual({ kind: "done", label: "done" });
		expect(botView({ state: "idle" })).toEqual({ kind: "idle", label: "off" });
		expect(botView({ state: "idle", done: true })).toEqual({ kind: "idle", label: "finished" });
	});

	test("a bot that needs a look says why: failed, missed, timed out, cost cap, paused; a usage limit is a limit", () => {
		const att = (status: string) => botView({ state: "attention", last: { status } });
		expect(att("failed")).toEqual({ kind: "attention", label: "failed" });
		expect(att("missed")).toEqual({ kind: "attention", label: "missed" });
		expect(att("timeout")).toEqual({ kind: "attention", label: "timed out" });
		expect(att("cost")).toEqual({ kind: "attention", label: "cost cap" });
		expect(att("limit")).toEqual({ kind: "limit", label: "limit" });
		expect(botView({ state: "attention", paused_reason: "5 failures in a row", last: { status: "failed" } })).toEqual({ kind: "attention", label: "paused" });
		expect(botView({ state: "attention", last: null })).toEqual({ kind: "attention", label: "needs a look" });
	});

	test("a status from the server only becomes a class name when it is a plain word", () => {
		expect(rstat("ok")).toBe("ok");
		expect(rstat("x y")).toBe("other");
		expect(rstat('a"><img')).toBe("other");
	});
});

describe("the page never builds markup from text", () => {
	test("no innerHTML, outerHTML or insertAdjacentHTML anywhere in the script", () => {
		const js = py.slice(py.indexOf('APP_JS = r"""'));
		for (const bad of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
			expect(js.includes(bad)).toBe(false);
		}
	});
});
