import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";

// mergeFiles is a pure function inside the page's script (an IIFE): cut it out by its two-space indent.
const py = fs.readFileSync(path.join(import.meta.dir, "../../bin/hivegrid_remote_ui.py"), "utf8");
const match = py.match(/\n {2}function mergeFiles\(list, files\) \{[\s\S]*?\n {2}\}\n/);
if (!match) throw new Error("mergeFiles not found");

interface Item {
	n: number | string;
	ts: number | null;
	file?: { id: string };
}
interface Sent {
	id: string;
	ts: number;
}
const mergeFiles = new Function(`${match[0]}\nreturn mergeFiles;`)() as (list: Item[], files: Sent[]) => Item[];

const it = (n: number, ts: number | null): Item => ({ n, ts });
const order = (out: Item[]) => out.map(x => (x.file ? `f${x.file.id}` : String(x.n)));

describe("mergeFiles", () => {
	test("a file stands after the last item sent before it, not at the end of the chat", () => {
		const out = mergeFiles([it(1, 10), it(2, 20), it(3, 30), it(4, 40)], [{ id: "a", ts: 25 }]);
		expect(order(out)).toEqual(["1", "2", "fa", "3", "4"]);
	});

	test("files go to their own places, oldest first, and a tie keeps the server's newest-first order reversed", () => {
		const out = mergeFiles(
			[it(1, 10), it(2, 20), it(3, 30)],
			[
				{ id: "late", ts: 35 },
				{ id: "tie2", ts: 15 },
				{ id: "tie1", ts: 15 },
				{ id: "early", ts: 5 },
			],
		);
		expect(order(out)).toEqual(["fearly", "1", "ftie1", "ftie2", "2", "3", "flate"]);
	});

	test("a file sent at the exact time of an item stands after it", () => {
		expect(order(mergeFiles([it(1, 10), it(2, 20)], [{ id: "a", ts: 20 }]))).toEqual(["1", "2", "fa"]);
	});

	test("an item without a time takes the time of the one before it", () => {
		const out = mergeFiles([it(1, 10), it(2, null), it(3, 30)], [{ id: "a", ts: 20 }]);
		expect(order(out)).toEqual(["1", "2", "fa", "3"]);
	});

	test("without any time in the chat the files come last", () => {
		expect(order(mergeFiles([it(1, null), it(2, null)], [{ id: "a", ts: 20 }]))).toEqual(["1", "2", "fa"]);
	});

	test("a file older than every item comes first; an empty chat still lists its files", () => {
		expect(order(mergeFiles([it(5, 50), it(6, 60)], [{ id: "a", ts: 1 }]))).toEqual(["fa", "5", "6"]);
		expect(order(mergeFiles([], [{ id: "a", ts: 1 }]))).toEqual(["fa"]);
	});

	test("files get their own keys and the items are untouched", () => {
		const items = [it(1, 10)];
		const out = mergeFiles(items, [{ id: "a", ts: 11 }]);
		expect(out[1].n).toBe("f:a");
		expect(out[0]).toBe(items[0]);
		expect(mergeFiles(items, [])).toBe(items);
	});
});
