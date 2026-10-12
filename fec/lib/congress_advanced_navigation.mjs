const TYPE_SLUG_TO_CODE = {
  "house-bill": "hr",
  "senate-bill": "s",
  "house-resolution": "hres",
  "senate-resolution": "sres",
  "house-joint-resolution": "hjres",
  "senate-joint-resolution": "sjres",
  "house-concurrent-resolution": "hconres",
  "senate-concurrent-resolution": "sconres",
  "house-amendment": "hamdt",
  "senate-amendment": "samdt",
};

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function canonicalSearchExpression(value) {
  return compact(value)
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .toLocaleLowerCase("en-US");
}

function canonicalMeasureLabel(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function parseLegislationPath(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.hostname.toLowerCase() !== "www.congress.gov") return null;
  const match = url.pathname.match(
    /\/(?:index\.php\/)?(bill|amendment)\/(\d+)(?:st|nd|rd|th)-congress\/([^/]+)\/(\d+)\/?$/i,
  );
  if (!match) return null;
  const [, recordKind, congressText, typeSlug, numberText] = match;
  const type = TYPE_SLUG_TO_CODE[typeSlug.toLowerCase()];
  if (!type) return null;
  const congress = Number(congressText);
  const number = Number(numberText);
  return {
    record_kind: recordKind.toLowerCase(),
    congress,
    type,
    type_slug: typeSlug.toLowerCase(),
    number,
    canonical_key: `${congress}:${type}:${number}`,
  };
}

/**
 * Congress.gov redirects an Advanced Search with exactly one result directly to
 * that bill/amendment page. Accept that response only when the loaded page
 * carries the original highlighted query, rank=1, an edit-search control, and
 * disabled previous/next result navigation. Those independent signals bind the
 * bill page to the just-requested Advanced Search and prove cardinality one.
 */
export function classifySingleResultRedirect({
  requestedUrl,
  loadedUrl,
  title,
  bodyText,
  query,
  congresses,
  legislationTypes,
  dualTransportRedirectMatch = false,
}) {
  let requested;
  let loaded;
  try {
    requested = new URL(requestedUrl);
    loaded = new URL(loadedUrl);
  } catch {
    return null;
  }
  if (
    requested.hostname.toLowerCase() !== "www.congress.gov" ||
    !/\/(?:index\.php\/)?advanced-search\/legislation\/?$/i.test(
      requested.pathname,
    )
  ) {
    return null;
  }
  const canonical = parseLegislationPath(loaded.href);
  if (!canonical) return null;

  const normalizedQuery = canonicalSearchExpression(query);
  const highlightedQuery = canonicalSearchExpression(
    loaded.searchParams.get("hl"),
  );
  const body = compact(bodyText);
  const titleText = compact(title);
  const congressSet = new Set((congresses ?? []).map(Number));
  const typeSet = new Set((legislationTypes ?? []).map(String));
  const surfaceObservations = {
    edit_search_control_present: /\bEDIT THIS\s+SEARCH\b/i.test(body),
    previous_result_disabled: /\bPREVIOUS\s*\(disabled\)/i.test(body),
    next_result_disabled: /\bNEXT\s*\(disabled\)/i.test(body),
    dual_transport_redirect_match: dualTransportRedirectMatch === true,
  };
  const legacyDisabledNavigationProof =
    surfaceObservations.edit_search_control_present &&
    surfaceObservations.previous_result_disabled &&
    surfaceObservations.next_result_disabled;
  const proof = {
    requested_advanced_search: true,
    loaded_official_legislation_page: true,
    highlighted_query_exact: highlightedQuery === normalizedQuery,
    rank_is_one: loaded.searchParams.get("r") === "1",
    search_session_present: Boolean(loaded.searchParams.get("s")),
    cardinality_one_proven:
      legacyDisabledNavigationProof || dualTransportRedirectMatch === true,
    title_identifies_loaded_measure:
      new RegExp(
        `${canonical.congress}(?:st|nd|rd|th) Congress`,
        "i",
      ).test(titleText) &&
      canonicalMeasureLabel(titleText.split(/\s+-\s+/, 1)[0]) ===
        canonicalMeasureLabel(displayNumberForCanonical(canonical)),
    congress_in_contract: congressSet.has(canonical.congress),
    type_in_contract: typeSet.has(canonical.type),
  };
  if (Object.values(proof).some((value) => value !== true)) return null;
  return {
    kind: "single_result_redirect",
    total: 1,
    canonical,
    proof,
    surface_observations: surfaceObservations,
    cardinality_proof:
      legacyDisabledNavigationProof
        ? "disabled-result-navigation-v1"
        : "dual-transport-session-redirect-v2",
    requested_url: requested.href,
    loaded_url: loaded.href,
    highlighted_query: loaded.searchParams.get("hl"),
    search_session: loaded.searchParams.get("s"),
    result_rank: 1,
  };
}

export function displayNumberForCanonical(canonical) {
  const prefixes = {
    hr: "H.R.",
    s: "S.",
    hres: "H.Res.",
    sres: "S.Res.",
    hjres: "H.J.Res.",
    sjres: "S.J.Res.",
    hconres: "H.Con.Res.",
    sconres: "S.Con.Res.",
    hamdt: "H.Amdt.",
    samdt: "S.Amdt.",
  };
  const prefix = prefixes[canonical.type];
  if (!prefix) throw new Error(`Unsupported legislation type: ${canonical.type}`);
  return `${prefix}${canonical.number}`;
}

export function parseSingleResultPageSnapshot({
  loadedUrl,
  title,
  bodyText,
  sponsorUrl = null,
}) {
  const canonical = parseLegislationPath(loadedUrl);
  if (!canonical) {
    throw new Error(`Not a Congress.gov legislation detail URL: ${loadedUrl}`);
  }
  const cleanBody = String(bodyText ?? "");
  const lines = cleanBody
    .split(/\r?\n/)
    .map(compact)
    .filter(Boolean);
  const lineWithPrefix = (prefix) =>
    lines.find((line) =>
      line.toLowerCase().startsWith(prefix.toLowerCase()),
    ) ?? null;
  const titleMatch = compact(title).match(
    /^(.+?)\s+-\s+\d+(?:st|nd|rd|th) Congress\s+\([^)]+\):\s*(.*?)\s+\|\s+Congress\.gov\b/i,
  );
  const congressTerm = compact(title).match(
    /(\d+(?:st|nd|rd|th) Congress\s+\([^)]+\))/i,
  )?.[1] ?? null;
  const sponsorLabelIndex = lines.findIndex((line) =>
    /^Sponsor:\s*$/i.test(line),
  );
  const sponsorLineWithValue =
    lines.find(
      (line) =>
        /^Sponsor:\s*\S/i.test(line) &&
        /\((Introduced|Submitted|Offered|Proposed)\s+\d{2}\/\d{2}\/\d{4}\)/i.test(
          line,
        ),
    ) ?? null;
  const sponsorValueAfterLabel =
    sponsorLabelIndex >= 0 ? lines[sponsorLabelIndex + 1] ?? null : null;
  const sponsorLine =
    sponsorLineWithValue ??
    (sponsorValueAfterLabel &&
    /\((Introduced|Submitted|Offered|Proposed)\s+\d{2}\/\d{2}\/\d{4}\)/i.test(
      sponsorValueAfterLabel,
    )
      ? `Sponsor: ${sponsorValueAfterLabel}`
      : null);
  const actionDateMatch = sponsorLine?.match(
    /\((Introduced|Submitted|Offered|Proposed)\s+(\d{2}\/\d{2}\/\d{4})\)/i,
  );
  const latestActionLine = lineWithPrefix("Latest Action:");
  const latestActionDateMatch = latestActionLine?.match(
    /Latest Action:\s*.*?\s-\s(\d{2}\/\d{2}\/\d{4})\s/i,
  );
  const trackerMatch = compact(cleanBody).match(
    /This (?:bill|resolution|amendment) has the status\s+(.+?)\s+Here are the steps/i,
  );
  return {
    display_number: titleMatch?.[1] ?? null,
    congress_term: congressTerm,
    title: titleMatch?.[2] ?? null,
    purpose: lineWithPrefix("Purpose:"),
    sponsor_line: sponsorLine,
    sponsor:
      sponsorLine
        ?.replace(/^Sponsor:\s*/i, "")
        .replace(
          /\s+\((?:Introduced|Submitted|Offered|Proposed)\s+\d{2}\/\d{2}\/\d{4}\).*$/i,
          "",
        )
        .trim() || null,
    sponsor_url: sponsorUrl,
    initial_action: actionDateMatch?.[1] ?? null,
    initial_action_date: actionDateMatch?.[2] ?? null,
    cosponsor_count: Number(
      compact(cleanBody).match(/\bCosponsors\s*\((\d+)\)/i)?.[1] ?? 0,
    ),
    committees: lineWithPrefix("Committees:"),
    latest_action: latestActionLine,
    latest_action_date: latestActionDateMatch?.[1] ?? null,
    amends: lineWithPrefix("Amends "),
    tracker_status: trackerMatch?.[1]?.trim() ?? null,
    result_url: loadedUrl,
    loaded_url: loadedUrl,
    raw_text: compact(cleanBody),
  };
}
