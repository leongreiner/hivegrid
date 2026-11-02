import { beforeEach, describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";

const py = fs.readFileSync(path.join(import.meta.dir, "../../bin/hivegrid_remote_ui.py"), "utf8");
const match = py.match(/SW_JS = r"""([\s\S]*?)"""/);
if (!match) throw new Error("SW_JS not found");
const SRC = match[1];

type Handler = (ev: Record<string, unknown>) => void;
interface Shown {
	title: string;
	opts: { body: string; tag?: string; renotify: boolean; icon: string; data: { url: string } };
}
interface Client {
	url: string;
	focus?: () => Promise<Client>;
	navigate: (url: string) => Promise<Client>;
}

const SCOPE = "https://box.tail1234.ts.net/";
let handlers: Record<string, Handler>;
let shown: Shown[];
let opened: string[];
let clients: Client[];
let waited: Promise<unknown>[];

function load(): void {
	handlers = {};
	shown = [];
	opened = [];
	waited = [];
	const fake = {
		addEventListener: (name: string, fn: Handler) => {
			handlers[name] = fn;
		},
		skipWaiting: () => {},
		registration: {
			scope: SCOPE,
			showNotification: (title: string, opts: Shown["opts"]) => {
				shown.push({ title, opts });
				return Promise.resolve();
			},
		},
		clients: {
			claim: () => Promise.resolve(),
			matchAll: () => Promise.resolve(clients),
			openWindow: (url: string) => {
				opened.push(url);
				return Promise.resolve(null);
			},
		},
	};
	new Function("self", SRC)(fake);
}

function event(extra: Record<string, unknown>): Record<string, unknown> {
	return { waitUntil: (p: Promise<unknown>) => waited.push(p), ...extra };
}
function push(payload: unknown, raw = false): void {
	handlers.push(
		event({
			data: { json: () => (raw ? JSON.parse(payload as string) : payload) },
		}),
	);
}
function click(url: unknown): { closed: boolean } {
	const state = { closed: false };
	handlers.notificationclick(
		event({
			notification: { close: () => (state.closed = true), data: url === undefined ? undefined : { url } },
		}),
	);
	return state;
}

beforeEach(() => {
	clients = [];
	load();
});

describe("push", () => {
	test("shows the title, body and tag the server sent, and replaces an older message for the same agent", () => {
		push({ title: "Hivegrid: agent needs you", body: "3 \u00b7 fix the build", tag: "agent-7", url: "#/a/7" });
		expect(shown).toHaveLength(1);
		expect(shown[0].title).toBe("Hivegrid: agent needs you");
		expect(shown[0].opts).toMatchObject({ body: "3 \u00b7 fix the build", tag: "agent-7", renotify: true, icon: "/icon.png" });
		expect(shown[0].opts.data.url).toBe("#/a/7");
	});

	test("a message without a tag never replaces another and has no renotify", () => {
		push({ title: "t", body: "b" });
		expect(shown[0].opts.tag).toBeUndefined();
		expect(shown[0].opts.renotify).toBe(false);
	});

	test("text is plain, one line and cut; a page route is the only link it can carry", () => {
		push({ title: "a\u001b[31m\r\nb".padEnd(400, "x"), body: "y".repeat(900), url: "https://evil.example/" });
		expect(shown[0].title).not.toMatch(/[\u0000-\u001f]/);
		expect(shown[0].title.length).toBe(120);
		expect(shown[0].opts.body.length).toBe(300);
		expect(shown[0].opts.data.url).toBe("#/");
		for (const bad of ["//evil", "#/../x\n", `#/${"a".repeat(200)}`, 5, null, { x: 1 }]) {
			shown.length = 0;
			push({ title: "t", url: bad });
			expect(shown[0].opts.data.url).toBe("#/");
		}
	});

	test("a message that is not an object, or not JSON at all, still shows something instead of failing silently", () => {
		push('"just text"', true);
		push("{not json", true);
		handlers.push(event({ data: null }));
		expect(shown.map((s) => s.title)).toEqual(["Hivegrid", "Hivegrid", "Hivegrid"]);
	});
});

describe("notificationclick", () => {
	test("closes the notification and focuses the open page at the agent", async () => {
		const calls: string[] = [];
		const page: Client = {
			url: `${SCOPE}#/bots`,
			focus: () => Promise.resolve(page),
			navigate: (u) => (calls.push(u), Promise.resolve(page)),
		};
		clients = [{ url: "https://other.example/", navigate: () => Promise.reject(new Error("wrong tab")) }, page];
		const state = click("#/a/7");
		await Promise.all(waited);
		expect(state.closed).toBe(true);
		expect(calls).toEqual([`${SCOPE}#/a/7`]);
		expect(opened).toEqual([]);
	});

	test("opens a window when no page is open, and when focusing fails", async () => {
		click("#/bots");
		await Promise.all(waited);
		expect(opened).toEqual([`${SCOPE}#/bots`]);
		const broken: Client = { url: SCOPE, focus: () => Promise.reject(new Error("no")), navigate: () => Promise.resolve(broken) };
		clients = [broken];
		waited = [];
		click("#/a/2");
		await Promise.all(waited);
		expect(opened).toEqual([`${SCOPE}#/bots`, `${SCOPE}#/a/2`]);
	});

	test("a tampered or missing route opens the start page, never another address", async () => {
		for (const bad of ["https://evil.example/", "//evil", undefined, 5]) {
			opened.length = 0;
			waited = [];
			click(bad);
			await Promise.all(waited);
			expect(opened).toEqual([SCOPE + "#/"]);
		}
	});
});
