function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function resultSummaryIsInProgress(value) {
  return /\ba summary is in progress\.?\b/i.test(compact(value));
}

export function selectStrictMainText(values, parseResultRange) {
  const normalized = values.map(compact).filter(Boolean);
  const distinct = [...new Set(normalized)];
  if (distinct.length === 1) return distinct[0];

  const classified = distinct.map((text) => {
    if (resultSummaryIsInProgress(text)) {
      return { text, kind: "summary_in_progress" };
    }
    try {
      return { text, kind: "parseable_result", range: parseResultRange(text) };
    } catch {
      return { text, kind: "invalid" };
    }
  });
  const progress = classified.filter(
    (item) => item.kind === "summary_in_progress",
  );
  const invalid = classified.filter((item) => item.kind === "invalid");

  // A stale parseable result can coexist with Congress.gov's live progress
  // surface. Keep polling until the transient surface disappears; accepting
  // either the stale result or a false zero here would silently lose recall.
  if (progress.length) return progress[0].text;

  // Congress.gov can render the zero-result main region twice, with one copy
  // carrying only an extra "Back to previous page" navigation line.  Require
  // every visible surface to independently parse as zero before accepting the
  // shortest canonical surface.  Distinct nonzero surfaces remain fail-closed.
  if (
    invalid.length === 0 &&
    classified.length > 0 &&
    classified.every(
      (item) =>
        item.kind === "parseable_result" &&
        item.range?.start === 0 &&
        item.range?.end === 0 &&
        item.range?.total === 0,
    )
  ) {
    return classified
      .map((item) => item.text)
      .sort((left, right) => left.length - right.length)[0];
  }

  throw new Error(
    "Congress.gov exposed an invalid #main result surface: " +
      `${normalized.length} non-empty node(s), ${distinct.length} distinct ` +
      "value(s); no result was accepted.",
  );
}
