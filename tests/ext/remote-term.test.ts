import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";

const py = fs.readFileSync(path.join(import.meta.dir, "../../bin/hivegrid_remote_termjs.py"), "utf8");
const match = py.match(/TERM_JS = r"""([\s\S]*?)"""/);
if (!match) throw new Error("TERM_JS not found");
const SRC = match[1];

const g = globalThis as unknown as Record<string, unknown>;
g.window = globalThis;
(0, eval)(SRC);

interface Row {
	c: string[];
	s: number[];
	v: number;
}
interface Style {
	fg: number;
	bg: number;
	fl: number;
}
interface Em {
	cols: number;
	rows: number;
	cx: number;
	cy: number;
	alt: boolean;
	cursorVisible: boolean;
	title: string;
	quiet: boolean;
	modes: { appCursor: boolean; bracketedPaste: boolean; autowrap: boolean; insert: boolean; mouse: boolean };
	version: number;
	epoch: number;
	sbPushed: number;
	scrollback: Row[];
	grid: Row[];
	styles: Style[];
	write(s: string): void;
	resize(cols: number, rows: number): void;
	reset(): void;
	lineText(y: number): string;
	screenText(): string[];
	scrollbackText(): string[];
	cellStyle(x: number, y: number): Style;
}
interface Utf8 {
	reset(): void;
	decode(b64: string): string;
	decodeBytes(u8: Uint8Array): string;
}
interface Term {
	version: number;
	Emulator: new (cols: number, rows: number, opt?: Record<string, unknown>) => Em;
	Utf8Stream: { new (): Utf8; prototype: Utf8 };
	keyBytes(ev: unknown, modes?: unknown): string | null;
	ctrlChar(ch: unknown): string | null;
	pasteText(text: unknown, bracketed: boolean): string;
	wcwidth(cp: number): number;
}
const HG = g.HGTerm as Term;

const ESC = "\x1b";
const CSI = `${ESC}[`;

const mk = (cols = 10, rows = 4, opt: Record<string, unknown> = {}): Em => new HG.Emulator(cols, rows, opt);
const feed = (cols: number, rows: number, s: string, opt: Record<string, unknown> = {}): Em => {
	const em = mk(cols, rows, opt);
	em.write(s);
	return em;
};

const TRUE = 0x1000000;

describe("module", () => {
	test("attaches to globalThis when window is undefined", () => {
		const saved = g.window;
		const savedTerm = g.HGTerm;
		delete g.window;
		delete g.HGTerm;
		try {
			(0, eval)(SRC);
			expect(typeof (g.HGTerm as Term).Emulator).toBe("function");
		} finally {
			g.window = saved;
			g.HGTerm = savedTerm;
		}
	});
});

describe("wcwidth", () => {
	test("narrow, wide and zero width", () => {
		expect(HG.wcwidth(0x61)).toBe(1);
		expect(HG.wcwidth(0xe9)).toBe(1);
		expect(HG.wcwidth(0x4e2d)).toBe(2);
		expect(HG.wcwidth(0x3042)).toBe(2);
		expect(HG.wcwidth(0xff21)).toBe(2);
		expect(HG.wcwidth(0xac00)).toBe(2);
		expect(HG.wcwidth(0x1f600)).toBe(2);
		expect(HG.wcwidth(0x0301)).toBe(0);
		expect(HG.wcwidth(0x200d)).toBe(0);
		expect(HG.wcwidth(0xfe0f)).toBe(0);
		expect(HG.wcwidth(0x1f3fd)).toBe(0);
		expect(HG.wcwidth(0x2500)).toBe(1);
		expect(HG.wcwidth(0x07)).toBe(0);
	});
});

describe("printing", () => {
	test("plain text with CR and LF", () => {
		const em = feed(10, 4, "hello\r\nworld");
		expect(em.screenText()).toEqual(["hello", "world", "", ""]);
		expect([em.cx, em.cy]).toEqual([5, 1]);
	});

	test("LF alone does not return the carriage", () => {
		const em = feed(10, 3, "ab\ncd");
		expect(em.screenText()).toEqual(["ab", "  cd", ""]);
	});

	test("autowrap is deferred until the next printable character", () => {
		const em = feed(5, 3, "abcde");
		expect([em.cx, em.cy]).toEqual([4, 0]);
		expect(em.screenText()).toEqual(["abcde", "", ""]);
		em.write("f");
		expect([em.cx, em.cy]).toEqual([1, 1]);
		expect(em.screenText()).toEqual(["abcde", "f", ""]);
	});

	test("a carriage return after the last column cancels the pending wrap", () => {
		const em = feed(5, 3, "abcde\rX");
		expect(em.screenText()).toEqual(["Xbcde", "", ""]);
	});

	test("autowrap off overwrites the last column", () => {
		const em = feed(5, 2, `${CSI}?7labcdefg`);
		expect(em.screenText()).toEqual(["abcdg", ""]);
		expect(em.modes.autowrap).toBe(false);
		em.write(`${CSI}?7h`);
		expect(em.modes.autowrap).toBe(true);
	});

	test("wrapping at the bottom scrolls and fills the scrollback", () => {
		const em = feed(5, 2, "11111222223333344444");
		expect(em.screenText()).toEqual(["33333", "44444"]);
		expect(em.scrollbackText()).toEqual(["11111", "22222"]);
	});

	test("BS and tabs", () => {
		const em = feed(20, 2, "abc\bX\ta\tb");
		expect(em.lineText(0)).toBe("abX     a       b");
		expect(em.cx).toBe(17);
	});

	test("HTS and TBC", () => {
		const em = mk(20, 2);
		em.write(`${CSI}3g${CSI}1;5H${ESC}H\r\tX`);
		expect(em.lineText(0)).toBe("    X");
		em.write(`${CSI}1;1H${CSI}5G${CSI}0g\r\tY`);
		expect(em.cx).toBe(19);
	});

	test("DEC special graphics line drawing through G0", () => {
		const em = feed(12, 2, `${ESC}(0lqqk${ESC}(Bx`);
		expect(em.lineText(0)).toBe("\u250c\u2500\u2500\u2510x");
		em.write(`\r\n${ESC}(0xqjmtunvwa\`${ESC}(B`);
		expect(em.lineText(1)).toBe("\u2502\u2500\u2518\u2514\u251c\u2524\u253c\u2534\u252c\u2592\u25c6");
	});

	test("SO and SI select G1 line drawing", () => {
		const em = feed(10, 1, `${ESC})0\x0eq\x0fq`);
		expect(em.lineText(0)).toBe("\u2500q");
	});

	test("wide character occupies two cells with an empty continuation", () => {
		const em = feed(10, 2, "a\u5199b");
		expect(em.lineText(0)).toBe("a\u5199b");
		expect(em.grid[0].c.slice(0, 4)).toEqual(["a", "\u5199", "", "b"]);
		expect(em.cx).toBe(4);
	});

	test("a wide character that does not fit wraps to the next line", () => {
		const em = feed(5, 3, "abcd\u5199");
		expect(em.screenText()).toEqual(["abcd", "\u5199", ""]);
	});

	test("overwriting half of a wide character blanks the other half", () => {
		const em = feed(10, 1, `\u5199${CSI}2GX`);
		expect(em.grid[0].c.slice(0, 3)).toEqual([" ", "X", " "]);
		const em2 = feed(10, 1, `\u5199\rX`);
		expect(em2.grid[0].c.slice(0, 3)).toEqual(["X", " ", " "]);
	});

	test("combining marks and emoji sequences join the previous cell", () => {
		const em = feed(10, 1, "e\u0301x");
		expect(em.grid[0].c[0]).toBe("e\u0301");
		expect(em.lineText(0)).toBe("e\u0301x");
		expect(em.cx).toBe(2);
		const em2 = feed(10, 1, "\u{1F44D}\u{1F3FD}z");
		expect(em2.grid[0].c.slice(0, 3)).toEqual(["\u{1F44D}\u{1F3FD}", "", "z"]);
	});

	test("a surrogate pair split across writes is reassembled", () => {
		const em = mk(10, 1);
		em.write("\ud83d");
		expect(em.lineText(0)).toBe("");
		em.write("\ude00!");
		expect(em.lineText(0)).toBe("\u{1F600}!");
		expect(em.cx).toBe(3);
	});

	test("a lone surrogate prints the replacement character", () => {
		const em = feed(10, 1, "\ud83dA\ude00");
		expect(em.lineText(0)).toBe("\ufffdA\ufffd");
	});

	test("insert mode shifts the rest of the line", () => {
		const em = feed(10, 1, `abc\r${CSI}4hXY`);
		expect(em.lineText(0)).toBe("XYabc");
		expect(em.modes.insert).toBe(true);
		em.write(`${CSI}4l\rZ`);
		expect(em.lineText(0)).toBe("ZYabc");
	});

	test("REP repeats the last printed character", () => {
		const em = feed(10, 1, `a${CSI}3b`);
		expect(em.lineText(0)).toBe("aaaa");
		const em2 = feed(10, 1, `${CSI}3b`);
		expect(em2.lineText(0)).toBe("");
	});

	test("DEL, C1 and other controls are ignored and never printed", () => {
		const em = feed(10, 1, "a\x00\x7f\x01\x9bb\x05c");
		expect(em.lineText(0)).toBe("abc");
	});
});

describe("cursor and erase", () => {
	test("CUP, HVP, CHA, VPA and relative moves", () => {
		const em = mk(10, 5);
		em.write(`${CSI}3;4H`);
		expect([em.cx, em.cy]).toEqual([3, 2]);
		em.write(`${CSI}H`);
		expect([em.cx, em.cy]).toEqual([0, 0]);
		em.write(`${CSI}2;2f`);
		expect([em.cx, em.cy]).toEqual([1, 1]);
		em.write(`${CSI}7G`);
		expect(em.cx).toBe(6);
		em.write(`${CSI}4d`);
		expect(em.cy).toBe(3);
		em.write(`${CSI}2A${CSI}3D`);
		expect([em.cx, em.cy]).toEqual([3, 1]);
		em.write(`${CSI}B${CSI}2C`);
		expect([em.cx, em.cy]).toEqual([5, 2]);
		em.write(`${CSI}2E`);
		expect([em.cx, em.cy]).toEqual([0, 4]);
		em.write(`${CSI}3F`);
		expect([em.cx, em.cy]).toEqual([0, 1]);
		em.write(`${CSI}3\``);
		expect(em.cx).toBe(2);
	});

	test("positions are clamped to the screen", () => {
		const em = mk(10, 5);
		em.write(`${CSI}99;99H`);
		expect([em.cx, em.cy]).toEqual([9, 4]);
		em.write(`${CSI}999999999;999999999H`);
		expect([em.cx, em.cy]).toEqual([9, 4]);
		em.write(`${CSI}99A${CSI}99D`);
		expect([em.cx, em.cy]).toEqual([0, 0]);
	});

	test("ED 0, 1, 2", () => {
		const fill = () => feed(5, 3, `aaaaa\r\nbbbbb\r\nccccc${CSI}2;3H`);
		expect(fill().screenText()).toEqual(["aaaaa", "bbbbb", "ccccc"]);
		let em = fill();
		em.write(`${CSI}J`);
		expect(em.screenText()).toEqual(["aaaaa", "bb", ""]);
		em = fill();
		em.write(`${CSI}1J`);
		expect(em.screenText()).toEqual(["", "   bb", "ccccc"]);
		em = fill();
		em.write(`${CSI}2J`);
		expect(em.screenText()).toEqual(["", "", ""]);
		expect([em.cx, em.cy]).toEqual([2, 1]);
	});

	test("ED 3 clears the scrollback", () => {
		const em = feed(3, 2, "aaabbbccc");
		expect(em.scrollbackText().length).toBe(1);
		const epoch = em.epoch;
		em.write(`${CSI}3J`);
		expect(em.scrollbackText()).toEqual([]);
		expect(em.epoch).toBeGreaterThan(epoch);
	});

	test("EL 0, 1, 2 and ECH", () => {
		const row = () => feed(8, 1, `abcdefgh${CSI}4G`);
		let em = row();
		em.write(`${CSI}K`);
		expect(em.lineText(0)).toBe("abc");
		em = row();
		em.write(`${CSI}1K`);
		expect(em.lineText(0)).toBe("    efgh");
		em = row();
		em.write(`${CSI}2K`);
		expect(em.lineText(0)).toBe("");
		em = row();
		em.write(`${CSI}2X`);
		expect(em.lineText(0)).toBe("abc  fgh");
		em.write(`${CSI}99X`);
		expect(em.lineText(0)).toBe("abc");
	});

	test("ICH and DCH", () => {
		const em = feed(10, 1, `abcdef${CSI}3G${CSI}2@`);
		expect(em.lineText(0)).toBe("ab  cdef");
		em.write(`${CSI}3P`);
		expect(em.lineText(0)).toBe("abdef");
		em.write(`${CSI}99@`);
		expect(em.lineText(0)).toBe("ab");
		const full = feed(5, 1, `abcde${CSI}2G${CSI}2@`);
		expect(full.lineText(0)).toBe("a  bc");
	});

	test("IL and DL", () => {
		const em = feed(3, 4, `111\r\n222\r\n333\r\n444${CSI}2;2H${CSI}L`);
		expect(em.screenText()).toEqual(["111", "", "222", "333"]);
		expect(em.cx).toBe(0);
		em.write(`${CSI}2M`);
		expect(em.screenText()).toEqual(["111", "333", "", ""]);
		expect(em.scrollbackText()).toEqual([]);
	});

	test("IL and DL outside the scroll region do nothing", () => {
		const em = feed(3, 4, `111\r\n222\r\n333\r\n444${CSI}2;3r${CSI}4;1H${CSI}L`);
		expect(em.screenText()).toEqual(["111", "222", "333", "444"]);
	});

	test("scroll region confines LF and keeps lines out of the scrollback", () => {
		const em = feed(3, 5, `111\r\n222\r\n333\r\n444\r\n555${CSI}2;4r${CSI}4;1H\n`);
		expect(em.screenText()).toEqual(["111", "333", "444", "", "555"]);
		expect(em.scrollbackText()).toEqual([]);
		expect([em.cx, em.cy]).toEqual([0, 3]);
	});

	test("DECSTBM homes the cursor and rejects an empty region", () => {
		const em = feed(5, 5, `${CSI}3;3H${CSI}2;4r`);
		expect([em.cx, em.cy]).toEqual([0, 0]);
		em.write(`${CSI}3;3H${CSI}4;2r`);
		expect([em.cx, em.cy]).toEqual([2, 2]);
	});

	test("SU, SD and reverse index", () => {
		const em = feed(3, 3, `111\r\n222\r\n333`);
		em.write(`${CSI}S`);
		expect(em.screenText()).toEqual(["222", "333", ""]);
		expect(em.scrollbackText()).toEqual(["111"]);
		em.write(`${CSI}T`);
		expect(em.screenText()).toEqual(["", "222", "333"]);
		em.write(`${CSI}1;1H${ESC}M`);
		expect(em.screenText()).toEqual(["", "", "222"]);
		em.write(`${CSI}2;3r${CSI}2;1H${ESC}M`);
		expect(em.screenText()).toEqual(["", "", ""]);
	});

	test("IND and NEL", () => {
		const em = feed(5, 3, `ab${ESC}Dcd${ESC}Eef`);
		expect(em.screenText()).toEqual(["ab", "  cd", "ef"]);
	});

	test("scrollback keeps only the configured number of lines", () => {
		const em = mk(3, 2, { scrollback: 2 });
		for (let i = 1; i <= 6; i++) em.write(`l${i}\r\n`);
		expect(em.scrollbackText()).toEqual(["l4", "l5"]);
		expect(em.sbPushed).toBe(5);
		const none = mk(3, 2, { scrollback: 0 });
		none.write("a\r\nb\r\nc\r\nd");
		expect(none.scrollbackText()).toEqual([]);
	});

	test("scrollback only fills when the region starts at row 0", () => {
		const em = feed(3, 4, `${CSI}2;4r${CSI}4;1Hx\n\n`);
		expect(em.scrollbackText()).toEqual([]);
		const em2 = feed(3, 4, `${CSI}1;3r${CSI}1;1Ha\r\nb\r\nc\r\nd`);
		expect(em2.scrollbackText()).toEqual(["a"]);
	});
});

describe("colours and attributes", () => {
	test("16 colours, bright colours and flags", () => {
		const em = feed(10, 1, `${CSI}31;44mA${CSI}91;104mB${CSI}1;2;3;4;5;7;8;9mC`);
		expect(em.cellStyle(0, 0)).toEqual({ fg: 1, bg: 4, fl: 0 });
		expect(em.cellStyle(1, 0)).toEqual({ fg: 9, bg: 12, fl: 0 });
		expect(em.cellStyle(2, 0)).toEqual({ fg: 9, bg: 12, fl: 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 });
	});

	test("reset and individual attribute off codes", () => {
		const em = feed(10, 1, `${CSI}1;3;4;7;9;31;42mA${CSI}22;23;24;27;29;39;49mB${CSI}1;31mC${CSI}mD${CSI}1;21mE`);
		expect(em.cellStyle(0, 0).fl).toBe(1 | 4 | 8 | 32 | 128);
		expect(em.cellStyle(1, 0)).toEqual({ fg: -1, bg: -1, fl: 0 });
		expect(em.cellStyle(2, 0)).toEqual({ fg: 1, bg: -1, fl: 1 });
		expect(em.cellStyle(3, 0)).toEqual({ fg: -1, bg: -1, fl: 0 });
		expect(em.cellStyle(4, 0).fl).toBe(1 | 8);
		const em2 = feed(10, 1, `${CSI}1;2m${CSI}22mA${CSI}0;8m${CSI}28mB${CSI}5m${CSI}25mC`);
		expect(em2.cellStyle(0, 0).fl).toBe(0);
		expect(em2.cellStyle(1, 0).fl).toBe(0);
		expect(em2.cellStyle(2, 0).fl).toBe(0);
	});

	test("inverse is only a flag in the model", () => {
		const em = feed(5, 1, `${CSI}7;32mx`);
		expect(em.cellStyle(0, 0)).toEqual({ fg: 2, bg: -1, fl: 32 });
	});

	test("256 colours with semicolon and colon forms", () => {
		const em = feed(10, 1, `${CSI}38;5;200mA${CSI}48;5;17mB${CSI}38:5:99mC${CSI}48:5:255mD`);
		expect(em.cellStyle(0, 0).fg).toBe(200);
		expect(em.cellStyle(1, 0)).toEqual({ fg: 200, bg: 17, fl: 0 });
		expect(em.cellStyle(2, 0).fg).toBe(99);
		expect(em.cellStyle(3, 0).bg).toBe(255);
	});

	test("truecolor with semicolon and colon forms", () => {
		const em = feed(10, 1, `${CSI}38;2;10;20;30mA${CSI}48;2;1;2;3mB${CSI}38:2::40:50:60mC${CSI}48:2:7:8:9mD${CSI}38:2:0:255:128:0mE`);
		expect(em.cellStyle(0, 0).fg).toBe(TRUE | 0x0a141e);
		expect(em.cellStyle(1, 0).bg).toBe(TRUE | 0x010203);
		expect(em.cellStyle(2, 0).fg).toBe(TRUE | 0x28323c);
		expect(em.cellStyle(3, 0).bg).toBe(TRUE | 0x070809);
		expect(em.cellStyle(4, 0).fg).toBe(TRUE | 0xff8000);
	});

	test("extended colours inside a longer SGR list consume their arguments", () => {
		const em = feed(5, 1, `${CSI}1;38;2;9;8;7;4;48;5;3;3mA`);
		expect(em.cellStyle(0, 0)).toEqual({ fg: TRUE | 0x090807, bg: 3, fl: 1 | 8 | 4 });
	});

	test("incomplete or out of range extended colours are ignored", () => {
		const em = feed(5, 1, `${CSI}38;5;300mA${CSI}38;2;1mB${CSI}38mC`);
		expect(em.cellStyle(0, 0).fg).toBe(-1);
		expect(em.cellStyle(2, 0).fg).toBe(-1);
	});

	test("underline sub-parameters", () => {
		const em = feed(5, 1, `${CSI}4:3mA${CSI}4:0mB${CSI}4mC`);
		expect(em.cellStyle(0, 0).fl).toBe(8);
		expect(em.cellStyle(1, 0).fl).toBe(0);
		expect(em.cellStyle(2, 0).fl).toBe(8);
	});

	test("underline colour is parsed and ignored", () => {
		const em = feed(5, 1, `${CSI}58;2;1;2;3;1mA${CSI}58:5:9;4mB`);
		expect(em.cellStyle(0, 0)).toEqual({ fg: -1, bg: -1, fl: 1 });
		expect(em.cellStyle(1, 0)).toEqual({ fg: -1, bg: -1, fl: 1 | 8 });
	});

	test("erased cells take the current background", () => {
		const em = feed(4, 2, `${CSI}44m${CSI}K${CSI}2;1H${CSI}2K`);
		expect(em.cellStyle(3, 0).bg).toBe(4);
		expect(em.cellStyle(0, 1).bg).toBe(4);
		expect(em.cellStyle(0, 0).fl).toBe(0);
		em.write(`${CSI}0m${CSI}41m${CSI}2J`);
		expect(em.cellStyle(2, 1).bg).toBe(1);
		em.write(`${CSI}0m${CSI}1;1H${CSI}2P`);
		expect(em.cellStyle(3, 0).bg).toBe(-1);
	});

	test("the style table is rebuilt from live cells when it grows large", () => {
		const em = mk(10, 3);
		for (let i = 0; i < 6000; i++) em.write(`${CSI}38;2;${i & 255};${i >> 8};1m${i % 10 === 0 ? CSI + "2J" + CSI + "H" : ""}x`);
		expect(em.styles.length).toBeLessThan(4200);
		em.write(`${CSI}38;2;5;6;7m${CSI}1;1HQ`);
		expect(em.cellStyle(0, 0).fg).toBe(TRUE | 0x050607);
		expect(em.lineText(0)[0]).toBe("Q");
	});
});

describe("save, restore and alternate screen", () => {
	test("ESC 7 and ESC 8 restore cursor, pen and charset", () => {
		const em = feed(10, 3, `ab${CSI}31m${ESC}(0${ESC}7${CSI}3;5H${CSI}0m${ESC}(Bx${ESC}8q`);
		expect(em.lineText(0)).toBe("ab\u2500");
		expect(em.cellStyle(2, 0).fg).toBe(1);
		expect(em.lineText(2)).toBe("    x");
	});

	test("ESC 8 without a save homes the cursor and resets the pen", () => {
		const em = feed(10, 3, `ab${CSI}31m${ESC}8c`);
		expect(em.lineText(0)).toBe("cb");
		expect(em.cellStyle(0, 0).fg).toBe(-1);
	});

	test("CSI s and CSI u", () => {
		const em = feed(10, 3, `ab${CSI}s${CSI}3;1Hz${CSI}ux`);
		expect(em.lineText(0)).toBe("abx");
		expect(em.lineText(2)).toBe("z");
	});

	test("1049 swaps in a blank grid and restores the main screen and cursor", () => {
		const em = feed(10, 3, "main\r\nline2");
		em.write(`${CSI}?1049h`);
		expect(em.alt).toBe(true);
		expect(em.screenText()).toEqual(["", "", ""]);
		em.write(`${CSI}2;3Halt\r\nmore`);
		expect(em.screenText()).toEqual(["", "  alt", "more"]);
		em.write(`${CSI}?1049l`);
		expect(em.alt).toBe(false);
		expect(em.screenText()).toEqual(["main", "line2", ""]);
		expect([em.cx, em.cy]).toEqual([5, 1]);
		em.write(`${CSI}?1049h`);
		expect(em.screenText()).toEqual(["", "", ""]);
	});

	test("47 and 1047 also switch screens", () => {
		for (const n of [47, 1047]) {
			const em = feed(10, 2, `main${CSI}?${n}hxx${CSI}?${n}l`);
			expect(em.alt).toBe(false);
			expect(em.screenText()).toEqual(["main", ""]);
			expect(em.cx).toBe(4);
		}
	});

	test("the alternate screen never feeds the scrollback", () => {
		const em = feed(5, 2, `${CSI}?1049h1\r\n2\r\n3\r\n4`);
		expect(em.scrollbackText()).toEqual([]);
		em.write(`${CSI}?1049l`);
		expect(em.scrollbackText()).toEqual([]);
	});

	test("scroll region does not leak between screens", () => {
		const em = feed(5, 4, `${CSI}?1049h${CSI}2;3r${CSI}?1049l`);
		em.write(`${CSI}4;1Ha\n`);
		expect(em.scrollbackText()).toEqual([""]);
		expect(em.lineText(2)).toBe("a");
	});

	test("cursor visibility, application cursor and bracketed paste modes", () => {
		const em = mk();
		expect(em.cursorVisible).toBe(true);
		em.write(`${CSI}?25l${CSI}?1h${CSI}?2004h`);
		expect([em.cursorVisible, em.modes.appCursor, em.modes.bracketedPaste]).toEqual([false, true, true]);
		em.write(`${CSI}?25h${CSI}?1l${CSI}?2004l`);
		expect([em.cursorVisible, em.modes.appCursor, em.modes.bracketedPaste]).toEqual([true, false, false]);
	});

	test("mouse modes are only recorded", () => {
		const replies: string[] = [];
		const em = mk(10, 3, { onReply: (s: string) => replies.push(s) });
		expect(em.modes.mouse).toBe(false);
		em.write(`${CSI}?1000h${CSI}?1006h`);
		expect(em.modes.mouse).toBe(true);
		em.write(`${CSI}?1000l`);
		expect(em.modes.mouse).toBe(true);
		em.write(`${CSI}?1006l`);
		expect(em.modes.mouse).toBe(false);
		em.write(`${CSI}?1003h${CSI}?1003l${CSI}?2026h${CSI}?1004h${CSI}?12h`);
		expect(em.modes.mouse).toBe(false);
		expect(replies).toEqual([]);
		expect(em.screenText()).toEqual(["", "", ""]);
	});

	test("soft reset restores modes but keeps the screen", () => {
		const em = feed(10, 3, `text${CSI}?7l${CSI}?1h${CSI}4h${CSI}?25l${CSI}31m${CSI}2;3r`);
		em.write(`${CSI}!p`);
		expect(em.modes.autowrap).toBe(true);
		expect(em.modes.appCursor).toBe(false);
		expect(em.modes.insert).toBe(false);
		expect(em.cursorVisible).toBe(true);
		expect(em.lineText(0)).toBe("text");
		em.write("Z");
		expect(em.cellStyle(em.cx - 1, em.cy).fg).toBe(-1);
	});

	test("RIS clears everything", () => {
		const em = feed(5, 2, `abcdefghij${CSI}?1h${CSI}31m${ESC}]2;t\x07`);
		expect(em.scrollbackText().length).toBe(0);
		em.write("klmno\r\npqrst");
		expect(em.scrollbackText().length).toBeGreaterThan(0);
		em.write(`${ESC}c`);
		expect(em.screenText()).toEqual(["", ""]);
		expect(em.scrollbackText()).toEqual([]);
		expect(em.modes.appCursor).toBe(false);
		expect(em.title).toBe("");
		expect([em.cx, em.cy]).toEqual([0, 0]);
		em.write("x");
		expect(em.cellStyle(0, 0).fg).toBe(-1);
		em.reset();
		expect(em.screenText()).toEqual(["", ""]);
	});
});

describe("replies", () => {
	const collect = (s: string, quiet = false) => {
		const replies: string[] = [];
		const em = mk(12, 5, { onReply: (r: string) => replies.push(r) });
		em.quiet = quiet;
		em.write(s);
		return { replies, em };
	};

	test("cursor position report", () => {
		expect(collect(`ab\r\nc${CSI}6n`).replies).toEqual([`${CSI}2;2R`]);
		expect(collect(`${CSI}?6n`).replies).toEqual([`${CSI}1;1R`]);
	});

	test("device attributes and status", () => {
		expect(collect(`${CSI}c`).replies).toEqual([`${CSI}?62;c`]);
		expect(collect(`${CSI}0c`).replies).toEqual([`${CSI}?62;c`]);
		expect(collect(`${CSI}>c`).replies).toEqual([`${CSI}>0;0;0c`]);
		expect(collect(`${CSI}5n`).replies).toEqual([`${CSI}0n`]);
	});

	test("window size report, other window operations are ignored", () => {
		expect(collect(`${CSI}18t`).replies).toEqual([`${CSI}8;5;12t`]);
		const r = collect(`${CSI}8;40;100t${CSI}22;0t${CSI}14t`);
		expect(r.replies).toEqual([]);
		expect(r.em.screenText()).toEqual(["", "", "", "", ""]);
	});

	test("quiet suppresses every reply", () => {
		const r = collect(`${CSI}6n${CSI}c${CSI}>c${CSI}5n${CSI}18t`, true);
		expect(r.replies).toEqual([]);
		const r2 = collect(`${CSI}6n`, false);
		r2.em.quiet = true;
		r2.em.write(`${CSI}6n`);
		expect(r2.replies.length).toBe(1);
		r2.em.quiet = false;
		r2.em.write(`${CSI}5n`);
		expect(r2.replies.length).toBe(2);
	});

	test("a throwing callback does not break parsing", () => {
		const em = mk(10, 2, {
			onReply: () => {
				throw new Error("boom");
			},
			onTitle: () => {
				throw new Error("boom");
			},
			onBell: () => {
				throw new Error("boom");
			},
		});
		em.write(`${CSI}6n${ESC}]2;t\x07\x07ok`);
		expect(em.lineText(0)).toBe("ok");
	});

	test("replies work without a callback", () => {
		const em = feed(10, 2, `${CSI}6n${CSI}c`);
		expect(em.screenText()).toEqual(["", ""]);
	});
});

describe("OSC, DCS and friends", () => {
	test("title from OSC 0, 1 and 2 with BEL and ST terminators", () => {
		const titles: string[] = [];
		const em = mk(20, 2, { onTitle: (t: string) => titles.push(t) });
		em.write(`${ESC}]0;first\x07`);
		expect(em.title).toBe("first");
		em.write(`${ESC}]2;second${ESC}\\`);
		expect(em.title).toBe("second");
		em.write(`${ESC}]1;third\x07`);
		expect(em.title).toBe("third");
		expect(titles).toEqual(["first", "second", "third"]);
		expect(em.screenText()).toEqual(["", ""]);
	});

	test("title keeps semicolons and drops controls and bidi characters", () => {
		const em = feed(20, 2, `${ESC}]2;a;b\x01c\x7fd\u202ee\u2066f\u200eg\u061ch\x85i\x07`);
		expect(em.title).toBe("a;bcdefghi");
	});

	test("title is cut at 120 characters without splitting a surrogate pair", () => {
		const em = feed(20, 2, `${ESC}]0;${"x".repeat(300)}\x07`);
		expect(em.title.length).toBe(120);
		const em2 = feed(20, 2, `${ESC}]0;${"\u{1F600}".repeat(200)}\x07`);
		expect(Array.from(em2.title).length).toBe(120);
		expect(em2.title.length).toBe(240);
	});

	test("an OSC without a number or with another number leaves the title alone", () => {
		const em = feed(20, 2, `${ESC}]2;keep\x07${ESC}]2\x07${ESC}]7;file:///x\x07${ESC}]02;no\x07${ESC}]x;no\x07`);
		expect(em.title).toBe("keep");
	});

	test("OSC 52, 8, 4, 10, 133 do nothing visible and never reply", () => {
		const replies: string[] = [];
		const titles: string[] = [];
		const em = mk(20, 2, { onReply: (r: string) => replies.push(r), onTitle: (t: string) => titles.push(t) });
		em.write(`${ESC}]52;c;aGVsbG8=\x07${ESC}]52;c;?\x07${ESC}]8;;http://x${ESC}\\link${ESC}]8;;${ESC}\\`);
		em.write(`${ESC}]4;1;?\x07${ESC}]10;?\x07${ESC}]11;?${ESC}\\${ESC}]133;A\x07ok`);
		expect(replies).toEqual([]);
		expect(titles).toEqual([]);
		expect(em.title).toBe("");
		expect(em.screenText()).toEqual(["linkok", ""]);
	});

	test("DCS, APC, PM and SOS strings are swallowed up to ST", () => {
		const em = feed(20, 2, `a${ESC}P1$rtext\x07more${ESC}\\b${ESC}_apc${ESC}\\c${ESC}^pm${ESC}\\d${ESC}Xsos${ESC}\\e`);
		expect(em.lineText(0)).toBe("abcde");
	});

	test("BEL terminates an OSC without ringing the bell", () => {
		const bells: number[] = [];
		const em = mk(10, 1, { onBell: () => bells.push(1) });
		em.write(`${ESC}]0;t\x07`);
		expect(bells.length).toBe(0);
		em.write("\x07");
		expect(bells.length).toBe(1);
	});

	test("an ESC that is not a string terminator starts a new sequence", () => {
		const em = feed(10, 1, `${ESC}]0;abc${ESC}[31mx`);
		expect(em.title).toBe("");
		expect(em.lineText(0)).toBe("x");
		expect(em.cellStyle(0, 0).fg).toBe(1);
	});

	test("CAN and SUB abort sequences", () => {
		const em = feed(10, 1, `${CSI}3\x18a${CSI}3\x1ab${ESC}]0;t\x18c`);
		expect(em.lineText(0)).toBe("abc");
		expect(em.title).toBe("");
	});

	test("a runaway OSC or DCS is cut at 4096 characters and ignored", () => {
		const em = mk(80, 2);
		em.write(`${ESC}]0;${"a".repeat(5000)}\x07`);
		expect(em.title).toBe("");
		em.write(`${ESC}[2J${ESC}[HP${ESC}P${"b".repeat(5000)}${ESC}\\Q`);
		expect(em.lineText(0).startsWith("PQ")).toBe(false);
		em.write(`${ESC}[2J${ESC}[Hok`);
		expect(em.lineText(0)).toBe("ok");
		const em2 = mk(80, 2);
		em2.write(`${ESC}]0;${"a".repeat(4000)}\x07`);
		expect(em2.title.length).toBe(120);
	});

	test("unknown and oversized CSI sequences are ignored", () => {
		const em = feed(10, 1, `a${CSI}${"1;".repeat(400)}mb${CSI}?9999z${CSI}>4;1mc${CSI}=1ud${CSI}$pe${CSI}1 qf`);
		expect(em.lineText(0)).toBe("abcdef");
		expect(em.cellStyle(1, 0).fl).toBe(0);
	});

	test("ESC sequences with intermediates and keypad modes are ignored", () => {
		const em = feed(10, 1, `a${ESC}=b${ESC}>c${ESC}#8d${ESC} Fe${ESC}%Gf${ESC}Ng`);
		expect(em.lineText(0)).toBe("abcdefg");
	});

	test("a sequence split across write calls", () => {
		const em = mk(10, 2);
		for (const part of [ESC, "[", "3", "1", ";", "1", "m", "X", ESC, "]", "2", ";", "ab", ESC, "\\", ESC, "[2", ";", "3H", "Y"]) em.write(part);
		expect(em.cellStyle(0, 0)).toEqual({ fg: 1, bg: -1, fl: 1 });
		expect(em.title).toBe("ab");
		expect(em.lineText(1)).toBe("  Y");
	});

	test("C0 controls inside a CSI are executed", () => {
		const em = feed(10, 3, `ab${CSI}1\x08CX`);
		expect(em.lineText(0)).toBe("abX");
		const bells: number[] = [];
		const em2 = mk(10, 2, { onBell: () => bells.push(1) });
		em2.write(`${CSI}3\x07mA`);
		expect(bells.length).toBe(1);
		expect(em2.cellStyle(0, 0).fl).toBe(4);
	});
});

describe("robustness", () => {
	test("writing 1 MiB of text is fast", () => {
		const unit = "The quick brown fox jumps over the lazy dog 0123456789\r\n\x1b[32mgreen\x1b[0m \u2500\u2502 more text here\r\n";
		let big = "";
		while (big.length < 1024 * 1024) big += unit;
		const em = mk(80, 24);
		const t0 = performance.now();
		em.write(big);
		const dt = performance.now() - t0;
		expect(dt).toBeLessThan(2000);
		expect(em.scrollback.length).toBe(2000);
		expect(em.screenText().some(l => l.includes("quick") || l.includes("green"))).toBe(true);
	});

	test("1 MiB of wrapped characters without newlines", () => {
		const em = mk(80, 24);
		const t0 = performance.now();
		em.write("x".repeat(1024 * 1024));
		expect(performance.now() - t0).toBeLessThan(2000);
		expect(em.cy).toBe(23);
	});

	test("huge counts stay bounded", () => {
		const em = mk(80, 24);
		const t0 = performance.now();
		em.write(`a${CSI}999999999b${CSI}999999999@${CSI}999999999P${CSI}999999999L${CSI}999999999M${CSI}999999999S${CSI}999999999T${CSI}999999999X`);
		expect(performance.now() - t0).toBeLessThan(2000);
		expect(em.cx).toBeLessThan(80);
		expect(em.cy).toBeLessThan(24);
	});

	// mulberry32
	const prng = (seed: number) => () => {
		seed = (seed + 0x6d2b79f5) | 0;
		let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
	const TOKENS = [
		`${ESC}[`, `${ESC}]`, `${ESC}P`, `${ESC}\\`, `${ESC}(`, `${ESC})`, `${ESC}#`, `${ESC}_`, `${ESC}^`, `${ESC}X`, `${ESC}7`, `${ESC}8`,
		`${ESC}c`, `${ESC}M`, `${ESC}D`, `${ESC}E`, `${ESC}H`, `\x07`, "\x08", "\t", "\n", "\r", "\x0b", "\x0c", "\x0e", "\x0f", "\x18", "\x1a",
		"0", "1", "2", "3", "4", "5", "9", ";", ":", "?", "!", "$", " ", ">", "=", "<", "99999", "65536",
		"m", "H", "J", "K", "r", "h", "l", "@", "P", "L", "M", "S", "T", "b", "X", "c", "n", "t", "s", "u", "A", "B", "C", "D", "E", "F", "G", "d", "f", "g", "I", "Z",
		"?1049", "?1000", "?7", "?25", "?47", "?2004", "38;2;1;2;3", "48:5:7", "38:2::1:2:3",
		"\u5199", "\u{1F600}", "\u0301", "\u200d", "\ufe0f", "\ud83d", "\ude00", "abc", "\u009b", "\x7f", "\x00",
	];
	const garbage = (rnd: () => number, len: number) => {
		const parts: string[] = [];
		let n = 0;
		while (n < len) {
			const r = rnd();
			let s: string;
			if (r < 0.6) s = TOKENS[Math.floor(rnd() * TOKENS.length)];
			else if (r < 0.8) s = String.fromCharCode(Math.floor(rnd() * 256));
			else if (r < 0.9) s = String.fromCharCode(Math.floor(rnd() * 0x10000));
			else s = String.fromCodePoint(Math.floor(rnd() * 0x110000));
			parts.push(s);
			n += s.length;
		}
		return parts.join("");
	};

	test("random garbage never throws, hangs or leaves the cursor out of bounds", () => {
		for (const seed of [1, 2, 3, 4, 5, 6]) {
			const rnd = prng(seed);
			const em = mk(1 + Math.floor(rnd() * 100), 1 + Math.floor(rnd() * 40), { scrollback: 50, onReply: () => {} });
			const data = garbage(rnd, 100_000);
			let i = 0;
			while (i < data.length) {
				const n = 1 + Math.floor(rnd() * 400);
				em.write(data.slice(i, i + n));
				i += n;
				expect(em.cx).toBeGreaterThanOrEqual(0);
				expect(em.cx).toBeLessThan(em.cols);
				expect(em.cy).toBeGreaterThanOrEqual(0);
				expect(em.cy).toBeLessThan(em.rows);
				expect(em.grid.length).toBe(em.rows);
				expect(em.scrollback.length).toBeLessThanOrEqual(50);
				if (rnd() < 0.01) em.resize(1 + Math.floor(rnd() * 120), 1 + Math.floor(rnd() * 50));
			}
			for (const row of em.grid) {
				expect(row.c.length).toBe(em.cols);
				expect(row.s.length).toBe(em.cols);
				expect(row.s.every((id: number) => id >= 0 && id < em.styles.length)).toBe(true);
			}
		}
	});

	test("pure random bytes decoded as UTF-8 never throw", () => {
		const rnd = prng(99);
		const bytes = new Uint8Array(200_000);
		for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(rnd() * 256);
		const dec = new HG.Utf8Stream();
		const em = mk(40, 10);
		for (let i = 0; i < bytes.length; i += 977) em.write(dec.decodeBytes(bytes.subarray(i, i + 977)));
		expect(em.cx).toBeLessThan(40);
		expect(em.cy).toBeLessThan(10);
	});
});

describe("resize", () => {
	test("growing pads the screen and keeps the content", () => {
		const em = feed(5, 2, "abc\r\nde");
		em.resize(8, 4);
		expect(em.screenText()).toEqual(["abc", "de", "", ""]);
		expect([em.cols, em.rows]).toEqual([8, 4]);
		expect([em.cx, em.cy]).toEqual([2, 1]);
		em.write("fgh");
		expect(em.lineText(1)).toBe("defgh");
		for (const row of em.grid) expect(row.c.length).toBe(8);
	});

	test("shrinking rows with the cursor at the bottom pushes the top lines to the scrollback", () => {
		const em = feed(4, 4, "1\r\n2\r\n3\r\n4");
		em.resize(4, 2);
		expect(em.screenText()).toEqual(["3", "4"]);
		expect(em.scrollbackText()).toEqual(["1", "2"]);
		expect([em.cx, em.cy]).toEqual([1, 1]);
	});

	test("shrinking rows with the cursor at the top drops the bottom lines", () => {
		const em = feed(4, 4, "1\r\n2\r\n3\r\n4\x1b[H");
		em.resize(4, 2);
		expect(em.screenText()).toEqual(["1", "2"]);
		expect(em.scrollbackText()).toEqual([]);
		expect([em.cx, em.cy]).toEqual([0, 0]);
	});

	test("shrinking columns truncates and clamps the cursor", () => {
		const em = feed(10, 2, "abcdefghij");
		em.resize(4, 2);
		expect(em.screenText()).toEqual(["abcd", ""]);
		expect(em.cx).toBe(3);
		em.write("X");
		expect(em.lineText(0)).toBe("abcX");
		expect(em.cy).toBe(0);
		em.write("Y");
		expect(em.cy).toBe(1);
	});

	test("truncating through a wide character blanks it", () => {
		const em = feed(6, 1, "ab\u5199cd");
		em.resize(3, 1);
		expect(em.lineText(0)).toBe("ab");
	});

	test("resize resets the scroll region and keeps tab stops sane", () => {
		const em = feed(10, 6, `${CSI}2;3r`);
		em.resize(20, 8);
		em.write(`${CSI}8;1H\n`);
		expect([em.cx, em.cy]).toEqual([0, 7]);
		expect(em.scrollbackText().length).toBe(1);
		em.write(`${CSI}1;1H\t\t\tx`);
		expect(em.lineText(0)).toBe(`${" ".repeat(19)}x`);
		expect(em.cx).toBe(19);
	});

	test("resize while on the alternate screen", () => {
		const em = feed(6, 3, `main${CSI}?1049h${CSI}HALT`);
		em.resize(4, 2);
		expect(em.screenText()).toEqual(["ALT", ""]);
		em.write(`${CSI}?1049l`);
		expect(em.alt).toBe(false);
		expect(em.screenText()).toEqual(["main", ""]);
		expect(em.cx).toBe(3);
		expect(em.cy).toBe(0);
		em.resize(10, 5);
		expect(em.grid.length).toBe(5);
	});

	test("random resizes keep the cursor in bounds", () => {
		const em = mk(10, 5);
		const sizes: [number, number][] = [[1, 1], [3, 7], [80, 24], [2, 2], [10, 1], [1, 9], [30, 3]];
		for (const [c, r] of sizes) {
			em.write("0123456789\r\nabcdef\r\n\u5199\u5199");
			em.resize(c, r);
			expect(em.cx).toBeLessThan(c);
			expect(em.cy).toBeLessThan(r);
			expect(em.grid.length).toBe(r);
			em.write("\x1b[H\x1b[Jxyz\r\n\r\n\r\n");
			expect(em.cy).toBeLessThan(r);
		}
	});

	test("version grows on every state change", () => {
		const em = mk(5, 2);
		const v0 = em.version;
		em.write("a");
		const v1 = em.version;
		expect(v1).toBeGreaterThan(v0);
		em.resize(6, 3);
		expect(em.version).toBeGreaterThan(v1);
		const v2 = em.version;
		em.reset();
		expect(em.version).toBeGreaterThan(v2);
	});
});

describe("Utf8Stream", () => {
	const b64 = (...bytes: number[]) => Buffer.from(bytes).toString("base64");

	test("decodes ASCII and complete multibyte text", () => {
		const s = new HG.Utf8Stream();
		expect(s.decode(Buffer.from("h\u00e9llo \u20ac \u{1F600}").toString("base64"))).toBe("h\u00e9llo \u20ac \u{1F600}");
	});

	test("a multibyte character split across calls is completed by the next call", () => {
		const s = new HG.Utf8Stream();
		expect(s.decode(b64(0x61, 0xe2, 0x82))).toBe("a");
		expect(s.decode(b64(0xac, 0x62))).toBe("\u20acb");
		expect(s.decode(b64(0xf0, 0x9f))).toBe("");
		expect(s.decode(b64(0x98))).toBe("");
		expect(s.decode(b64(0x80))).toBe("\u{1F600}");
	});

	test("malformed bytes become U+FFFD", () => {
		const s = new HG.Utf8Stream();
		expect(s.decode(b64(0x61, 0xff, 0x62))).toBe("a\ufffdb");
		expect(s.decode(b64(0xc3, 0x28))).toBe("\ufffd(");
	});

	test("reset drops a pending partial character", () => {
		const s = new HG.Utf8Stream();
		expect(s.decode(b64(0xe2, 0x82))).toBe("");
		s.reset();
		expect(s.decode(b64(0xac))).toBe("\ufffd");
	});

	test("decodeBytes and invalid base64", () => {
		const s = new HG.Utf8Stream();
		expect(s.decodeBytes(new Uint8Array([0xe2, 0x82]))).toBe("");
		expect(s.decodeBytes(new Uint8Array([0xac]))).toBe("\u20ac");
		expect(HG.Utf8Stream.prototype.decodeBytes).toBe(s.decodeBytes);
		expect(s.decode("!!!not base64")).toBe("");
		expect(s.decode("")).toBe("");
	});

	test("feeds the emulator without breaking characters", () => {
		const em = mk(10, 1);
		const s = new HG.Utf8Stream();
		const bytes = Buffer.from("\u4e2d\u6587!");
		for (const b of bytes) em.write(s.decode(Buffer.from([b]).toString("base64")));
		expect(em.lineText(0)).toBe("\u4e2d\u6587!");
	});
});

describe("keyBytes", () => {
	const k = (key: string, mods: Record<string, boolean> = {}, modes: Record<string, boolean> = {}) =>
		HG.keyBytes({ key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...mods }, modes);
	const ctrl = { ctrlKey: true };
	const alt = { altKey: true };
	const shift = { shiftKey: true };

	test("simple keys", () => {
		expect(k("Enter")).toBe("\r");
		expect(k("Backspace")).toBe("\x7f");
		expect(k("Backspace", ctrl)).toBe("\x08");
		expect(k("Tab")).toBe("\t");
		expect(k("Tab", shift)).toBe(`${CSI}Z`);
		expect(k("Escape")).toBe("\x1b");
		expect(k("Enter", alt)).toBe("\x1b\r");
		expect(k("Backspace", alt)).toBe("\x1b\x7f");
	});

	test("arrows, normal and application mode", () => {
		const a = { ArrowUp: "A", ArrowDown: "B", ArrowRight: "C", ArrowLeft: "D" };
		for (const [key, l] of Object.entries(a)) {
			expect(k(key)).toBe(`${CSI}${l}`);
			expect(k(key, {}, { appCursor: true })).toBe(`${ESC}O${l}`);
			expect(k(key, shift)).toBe(`${CSI}1;2${l}`);
			expect(k(key, alt)).toBe(`${CSI}1;3${l}`);
			expect(k(key, ctrl)).toBe(`${CSI}1;5${l}`);
			expect(k(key, { ctrlKey: true, shiftKey: true, altKey: true })).toBe(`${CSI}1;8${l}`);
			expect(k(key, ctrl, { appCursor: true })).toBe(`${CSI}1;5${l}`);
		}
	});

	test("Home and End", () => {
		expect(k("Home")).toBe(`${CSI}H`);
		expect(k("End")).toBe(`${CSI}F`);
		expect(k("Home", {}, { appCursor: true })).toBe(`${ESC}OH`);
		expect(k("End", {}, { appCursor: true })).toBe(`${ESC}OF`);
		expect(k("Home", shift)).toBe(`${CSI}1;2H`);
		expect(k("End", ctrl)).toBe(`${CSI}1;5F`);
	});

	test("Insert, Delete, PageUp, PageDown", () => {
		expect(k("Insert")).toBe(`${CSI}2~`);
		expect(k("Delete")).toBe(`${CSI}3~`);
		expect(k("PageUp")).toBe(`${CSI}5~`);
		expect(k("PageDown")).toBe(`${CSI}6~`);
		expect(k("Delete", ctrl)).toBe(`${CSI}3;5~`);
		expect(k("PageUp", shift)).toBe(`${CSI}5;2~`);
		expect(k("PageDown", alt)).toBe(`${CSI}6;3~`);
		expect(k("Insert", { shiftKey: true, altKey: true })).toBe(`${CSI}2;4~`);
	});

	test("function keys", () => {
		expect(["F1", "F2", "F3", "F4"].map(x => k(x))).toEqual([`${ESC}OP`, `${ESC}OQ`, `${ESC}OR`, `${ESC}OS`]);
		expect(["F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"].map(x => k(x))).toEqual(
			["15", "17", "18", "19", "20", "21", "23", "24"].map(n => `${CSI}${n}~`),
		);
		expect(k("F1", shift)).toBe(`${CSI}1;2P`);
		expect(k("F4", ctrl)).toBe(`${CSI}1;5S`);
		expect(k("F5", shift)).toBe(`${CSI}15;2~`);
		expect(k("F12", { ctrlKey: true, altKey: true })).toBe(`${CSI}24;7~`);
		expect(k("F1", {}, { appCursor: true })).toBe(`${ESC}OP`);
	});

	test("Ctrl with letters, case-insensitive", () => {
		expect(k("a", ctrl)).toBe("\x01");
		expect(k("A", ctrl)).toBe("\x01");
		expect(k("c", ctrl)).toBe("\x03");
		expect(k("d", ctrl)).toBe("\x04");
		expect(k("z", ctrl)).toBe("\x1a");
		expect(k("Z", { ctrlKey: true, shiftKey: true })).toBe("\x1a");
		expect(k("b", { ctrlKey: true, shiftKey: true })).toBe("\x02");
	});

	test("Ctrl with punctuation", () => {
		expect(k(" ", ctrl)).toBe("\x00");
		expect(k("@", ctrl)).toBe("\x00");
		expect(k("[", ctrl)).toBe("\x1b");
		expect(k("\\", ctrl)).toBe("\x1c");
		expect(k("]", ctrl)).toBe("\x1d");
		expect(k("^", ctrl)).toBe("\x1e");
		expect(k("6", ctrl)).toBe("\x1e");
		expect(k("_", ctrl)).toBe("\x1f");
		expect(k("/", ctrl)).toBe("\x1f");
		expect(k("?", ctrl)).toBe("\x7f");
		expect(k("1", ctrl)).toBeNull();
		expect(k("\u00e9", ctrl)).toBeNull();
	});

	test("Alt with a single character", () => {
		expect(k("b", alt)).toBe("\x1bb");
		expect(k("B", { altKey: true, shiftKey: true })).toBe("\x1bB");
		expect(k(".", alt)).toBe("\x1b.");
		expect(k("\u00e9", alt)).toBe("\x1b\u00e9");
		expect(k("\u{1F600}", alt)).toBe("\x1b\u{1F600}");
		expect(k("a", { ctrlKey: true, altKey: true })).toBe("\x1b\x01");
	});

	test("printable characters are left to the browser", () => {
		for (const key of ["a", "Z", "1", " ", "\u00e9", "\u{1F600}", "~", "?"]) expect(k(key)).toBeNull();
		expect(k("A", shift)).toBeNull();
	});

	test("meta combinations and Ctrl+Shift browser shortcuts are left to the browser", () => {
		expect(k("c", { metaKey: true })).toBeNull();
		expect(k("v", { metaKey: true })).toBeNull();
		expect(k("ArrowLeft", { metaKey: true })).toBeNull();
		expect(k("Enter", { metaKey: true })).toBeNull();
		for (const key of "CVXAFRIJKTNW") {
			expect(k(key, { ctrlKey: true, shiftKey: true })).toBeNull();
			expect(k(key.toLowerCase(), { ctrlKey: true, shiftKey: true })).toBeNull();
		}
		expect(k("ArrowUp", { ctrlKey: true, shiftKey: true })).toBe(`${CSI}1;6A`);
	});

	test("unknown keys", () => {
		for (const key of ["Dead", "Unidentified", "Shift", "Control", "Alt", "Meta", "CapsLock", "ContextMenu", "Process", "F13", ""]) {
			expect(k(key)).toBeNull();
		}
		expect(k("Dead", alt)).toBeNull();
		expect(HG.keyBytes(null, {})).toBeNull();
		expect(HG.keyBytes({}, {})).toBeNull();
		expect(HG.keyBytes({ key: "ArrowUp" })).toBe(`${CSI}A`);
	});

	test("AltGraph is left to the browser", () => {
		const ev = { key: "@", ctrlKey: true, altKey: true, getModifierState: (m: string) => m === "AltGraph" };
		expect(HG.keyBytes(ev, {})).toBeNull();
	});
});

describe("ctrlChar", () => {
	test("letters and punctuation", () => {
		expect(HG.ctrlChar("a")).toBe("\x01");
		expect(HG.ctrlChar("C")).toBe("\x03");
		expect(HG.ctrlChar("z")).toBe("\x1a");
		expect(HG.ctrlChar(" ")).toBe("\x00");
		expect(HG.ctrlChar("@")).toBe("\x00");
		expect(HG.ctrlChar("[")).toBe("\x1b");
		expect(HG.ctrlChar("\\")).toBe("\x1c");
		expect(HG.ctrlChar("]")).toBe("\x1d");
		expect(HG.ctrlChar("^")).toBe("\x1e");
		expect(HG.ctrlChar("6")).toBe("\x1e");
		expect(HG.ctrlChar("_")).toBe("\x1f");
		expect(HG.ctrlChar("/")).toBe("\x1f");
		expect(HG.ctrlChar("?")).toBe("\x7f");
	});

	test("no control character", () => {
		for (const ch of ["1", "-", "\u00e9", "ab", "", "\n"]) expect(HG.ctrlChar(ch)).toBeNull();
		expect(HG.ctrlChar(undefined)).toBeNull();
		expect(HG.ctrlChar(5)).toBeNull();
	});
});

describe("pasteText", () => {
	test("newlines become carriage returns", () => {
		expect(HG.pasteText("a\nb\r\nc\rd", false)).toBe("a\rb\rc\rd");
		expect(HG.pasteText("\n\n", false)).toBe("\r\r");
	});

	test("controls are removed but tab and CR stay", () => {
		expect(HG.pasteText("a\tb\x00\x01\x07\x08\x0b\x0c\x0e\x1fc\x7fd\x85e\x9ff", false)).toBe("a\tbcdef");
	});

	test("an ESC can never be pasted, so the bracketed paste end marker cannot be injected", () => {
		const evil = "x\x1b[201~rm -rf ~\r";
		const plain = HG.pasteText(evil, false);
		expect(plain.includes("\x1b")).toBe(false);
		const out = HG.pasteText(evil, true);
		expect(out.startsWith(`${CSI}200~`)).toBe(true);
		expect(out.endsWith(`${CSI}201~`)).toBe(true);
		const inner = out.slice(`${CSI}200~`.length, out.length - `${CSI}201~`.length);
		expect(inner.includes("\x1b")).toBe(false);
		expect(out.indexOf(`${CSI}201~`)).toBe(out.length - 6);
	});

	test("bracketed wrapping", () => {
		expect(HG.pasteText("hi\nthere", true)).toBe(`${CSI}200~hi\rthere${CSI}201~`);
		expect(HG.pasteText("", true)).toBe(`${CSI}200~${CSI}201~`);
		expect(HG.pasteText("plain", false)).toBe("plain");
	});

	test("non-string input is stringified", () => {
		expect(HG.pasteText(42, false)).toBe("42");
	});
});
