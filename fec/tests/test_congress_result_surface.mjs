import assert from "node:assert/strict";
import test from "node:test";

import {
  selectStrictMainText,
} from "../lib/congress_result_surface.mjs";

function parseRange(text) {
  if (/No results found/i.test(text)) {
    return { start: 0, end: 0, total: 0 };
  }
  const match = text.match(/Search Results\s+\d+-\d+ of (\d+)/i);
  if (!match) throw new Error("not a settled result");
  return { start: 1, end: Number(match[1]), total: Number(match[1]) };
}

test("identical duplicate result surfaces are accepted once", () => {
  const text = "Search Results 1-2 of 2";
  assert.equal(selectStrictMainText([text, ` ${text} `], parseRange), text);
});

test("stale no-result plus progress keeps polling", () => {
  assert.equal(
    selectStrictMainText(
      ["No results found.", "A summary is in progress."],
      parseRange,
    ),
    "A summary is in progress.",
  );
});

test("stale nonzero result plus progress keeps polling", () => {
  assert.equal(
    selectStrictMainText(
      ["Search Results 1-5 of 5", "A summary is in progress."],
      parseRange,
    ),
    "A summary is in progress.",
  );
});

test("duplicate zero-result surfaces may differ only in surrounding navigation", () => {
  assert.equal(
    selectStrictMainText(
      [
        "No results found. Try another search. Back to previous page",
        "No results found. Try another search.",
      ],
      parseRange,
    ),
    "No results found. Try another search.",
  );
});

test("distinct settled surfaces fail closed even with the same range", () => {
  assert.throws(
    () =>
      selectStrictMainText(
        [
          "Search Results 1-5 of 5 old",
          "Search Results 1-5 of 5 current",
        ],
        parseRange,
      ),
    /invalid #main result surface.*no result was accepted/,
  );
});

test("progress mixed with an invalid surface keeps polling", () => {
  assert.equal(
    selectStrictMainText(
      ["A summary is in progress.", "unrecognized stale content"],
      parseRange,
    ),
    "A summary is in progress.",
  );
});
