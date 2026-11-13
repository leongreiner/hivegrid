import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";

// enterSends and stepAgent are pure functions inside the page's script (an IIFE): cut them out by their two-space indent.
const py = fs.readFileSync(path.join(import.meta.dir, "../../bin/hivegrid_remote_ui.py"), "utf8");
const cut = (name: string): string => {
	const m = py.match(new RegExp(`\\n {2}function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n {2}\\}\\n`));
	if (!m) throw new Error(`${name} not found`);
	return m[0];
};

interface Key {
	key: string;
	shiftKey?: boolean;
	altKey?: boolean;
	ctrlKey?: boolean;
	metaKey?: boolean;
	isComposing?: boolean;
	keyCode?: number;
}
const enterSends = new Function(`${cut("enterSends")}\nreturn enterSends;`)() as (ev: Key, touch: boolean) => boolean;
const stepAgent = new Function(`${cut("stepAgent")}\nreturn stepAgent;`)() as (ids: string[], cur: string | null, dir: number) => string | null;

describe("Enter in a message field", () => {
	test("on a computer Enter sends and Shift+Enter is a new line", () => {
		expect(enterSends({ key: "Enter" }, false)).toBe(true);
		expect(enterSends({ key: "Enter", shiftKey: true }, false)).toBe(false);
		expect(enterSends({ key: "Enter", altKey: true }, false)).toBe(false);
	});

	test("on a touch-only device Enter is a new line; Ctrl or Cmd+Enter sends everywhere", () => {
		expect(enterSends({ key: "Enter" }, true)).toBe(false);
		expect(enterSends({ key: "Enter", ctrlKey: true }, true)).toBe(true);
		expect(enterSends({ key: "Enter", metaKey: true }, false)).toBe(true);
	});

	test("Enter that confirms an input method's composition never sends", () => {
		expect(enterSends({ key: "Enter", isComposing: true }, false)).toBe(false);
		expect(enterSends({ key: "Enter", keyCode: 229 }, false)).toBe(false);
	});

	test("other keys never send", () => {
		expect(enterSends({ key: "a" }, false)).toBe(false);
		expect(enterSends({ key: "a", ctrlKey: true }, false)).toBe(false);
	});
});

describe("Alt+Up and Alt+Down between agents", () => {
	test("they move to the neighbour and wrap around at both ends", () => {
		expect(stepAgent(["a", "b", "c"], "b", 1)).toBe("c");
		expect(stepAgent(["a", "b", "c"], "c", 1)).toBe("a");
		expect(stepAgent(["a", "b", "c"], "a", -1)).toBe("c");
	});

	test("without an open agent (or one the filter hides) they start at the first or the last", () => {
		expect(stepAgent(["a", "b", "c"], null, 1)).toBe("a");
		expect(stepAgent(["a", "b", "c"], "x", -1)).toBe("c");
		expect(stepAgent([], null, 1)).toBe(null);
	});
});
