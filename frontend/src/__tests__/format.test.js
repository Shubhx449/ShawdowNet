import { describe, expect, it } from "vitest";
import { fmt, toCsv } from "../format";

describe("fmt", () => {
  it("shows a dash for missing values, not 'null' or 'NaN'", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt(undefined)).toBe("—");
  });
  it("trims trailing zeros but keeps significant digits", () => {
    expect(fmt(1.5)).toBe("1.5");
    expect(fmt(0.125)).toBe("0.125");
    expect(fmt(2)).toBe("2");
    expect(fmt(100)).toBe("100");
    expect(fmt(-85, 1)).toBe("-85");
    expect(fmt(0)).toBe("0");
    expect(fmt(0.0001)).toBe("0"); // rounds away at 3 dp
    expect(fmt(0.98765, 4)).toBe("0.9877");
  });
  it("does not corrupt integers when asked for zero decimals", () => {
    expect(fmt(100, 0)).toBe("100"); // used to become "1"
    expect(fmt(10, 0)).toBe("10");
    expect(fmt(7.6, 0)).toBe("8");
  });
});

describe("toCsv", () => {
  it("writes empty cells for null/undefined", () => {
    expect(toCsv([["a", null, undefined, 0]])).toBe("a,,,0\n");
  });
  it("quotes cells containing commas, quotes or newlines, and doubles embedded quotes", () => {
    expect(toCsv([["x,y", 'say "hi"', "l1\nl2", "plain"]])).toBe('"x,y","say ""hi""","l1\nl2",plain\n');
  });
  it("keeps booleans and numbers as-is", () => {
    expect(toCsv([[true, false, 1.25]])).toBe("true,false,1.25\n");
  });
});
