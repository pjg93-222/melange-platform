import {
  classifySingleResultRedirect,
  displayNumberForCanonical,
  parseSingleResultPageSnapshot,
} from "./congress_advanced_navigation.mjs";
import {
  resultSummaryIsInProgress,
  selectStrictMainText,
} from "./congress_result_surface.mjs";

export const ADVANCED_SEARCH_URL =
  "https://www.congress.gov/index.php/advanced-search/legislation";
export const CONGRESS_PAGE_SIZE = 250;
export const CONGRESS_PAGE_SORT = "documentNumber:asc";
export const STRICT_ZERO_SURFACE = "strict-main-v2";

const LEGISLATION_TYPE_IDS = {
  hr: "legislationTypehr",
  hres: "legislationTypehres",
  hjres: "legislationTypehjres",
  hconres: "legislationTypehconres",
  hamdt: "legislationTypehamdt",
  s: "legislationTypes",
  sres: "legislationTypesres",
  sjres: "legislationTypesjres",
  sconres: "legislationTypesconres",
  samdt: "legislationTypesamdt",
};
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

export function buildAdvancedSearchQuery(entry) {
  if (entry.advanced_search_query) return entry.advanced_search_query;
  return entry.query_terms.map((term) => `(${term.query})`).join(" OR ");
}

export function buildAdvancedSearchPageUrl(entry, contract, pageNumber) {
  if (!Number.isInteger(pageNumber) || pageNumber < 1) {
    throw new Error("pageNumber must be a positive integer");
  }
  const searchUrl = new URL(ADVANCED_SEARCH_URL);
  [...contract.congresses]
    .sort((left, right) => right - left)
    .forEach((congress, index) => {
      searchUrl.searchParams.append(`congresses[${index}]`, String(congress));
    });
  searchUrl.searchParams.set("legislationNumbers", "");
  searchUrl.searchParams.set("restrictionType", "includeBillText");
  searchUrl.searchParams.append("restrictionFields[0]", "allBillTitles");
  searchUrl.searchParams.append("restrictionFields[1]", "summary");
  searchUrl.searchParams.set("summaryField", "billSummary");
  searchUrl.searchParams.set("enterTerms", buildAdvancedSearchQuery(entry));
  searchUrl.searchParams.set("wordVariants", "true");
  contract.legislation_types.forEach((type, index) => {
    if (!Object.hasOwn(LEGISLATION_TYPE_IDS, type)) {
      throw new Error(`Unsupported legislation type in contract: ${type}`);
    }
    searchUrl.searchParams.append(`legislationTypes[${index}]`, type);
  });
  searchUrl.searchParams.set("public", "true");
  searchUrl.searchParams.set("private", "true");
  searchUrl.searchParams.set("actionTerms", "");
  searchUrl.searchParams.set("legislativeActionWordVariants", "true");
  searchUrl.searchParams.set("actionDateBoxCount", "2");
  searchUrl.searchParams.set("actionText", "");
  searchUrl.searchParams.set("actionText2", "");
  searchUrl.searchParams.set("dateOfActionToggle", "multi");
  searchUrl.searchParams.set("legislativeAction", "Any");
  searchUrl.searchParams.set("sponsorState", "One");
  searchUrl.searchParams.set("member", "");
  searchUrl.searchParams.append("sponsorTypes[0]", "sponsor");
  searchUrl.searchParams.set("sponsorTypeBool", "OR");
  searchUrl.searchParams.set("dateOfSponsorshipOperator", "equal");
  searchUrl.searchParams.set("dateOfSponsorshipStartDate", "");
  searchUrl.searchParams.set("dateOfSponsorshipEndDate", "");
  searchUrl.searchParams.set("dateOfSponsorshipIsOptions", "yesterday");
  [0, 3, 11, 23, 12, 4, 2, 5, 9].forEach((activity, index) => {
    searchUrl.searchParams.append(
      `committeeActivity[${index}]`,
      String(activity),
    );
  });
  searchUrl.searchParams.set("satellite", "[]");
  searchUrl.searchParams.set("search", "");
  searchUrl.searchParams.set("submitted", "Submitted");
  searchUrl.searchParams.set("pageSize", String(CONGRESS_PAGE_SIZE));
  searchUrl.searchParams.set("pageSort", CONGRESS_PAGE_SORT);
  searchUrl.searchParams.set("page", String(pageNumber));
  return searchUrl.href;
}

export function parseResultRange(value) {
  const text = compact(value);
  const range = text.match(
    /Search Results\s+([\d,]+)-([\d,]+) of ([\d,]+)/i,
  );
  if (range) {
    return {
      start: Number(range[1].replaceAll(",", "")),
      end: Number(range[2].replaceAll(",", "")),
      total: Number(range[3].replaceAll(",", "")),
      header: range[0],
    };
  }
  if (
    /no results found|no search results|0 results|did not match any documents/i.test(
      text,
    )
  ) {
    return { start: 0, end: 0, total: 0, header: "Search Results 0 of 0" };
  }
  throw new Error(
    `Could not parse Congress.gov result range from: ${text.slice(0, 700)}`,
  );
}

export function parseCanonicalUrl(rawUrl) {
  const url = new URL(rawUrl);
  const match = url.pathname.match(
    /\/(?:index\.php\/)?(bill|amendment)\/(\d+)(?:st|nd|rd|th)-congress\/([^/]+)\/(\d+)\/?$/i,
  );
  if (!match) {
    throw new Error(`Unrecognized Congress.gov legislation URL: ${rawUrl}`);
  }
  const [, recordKind, congressText, typeSlug, numberText] = match;
  const type = TYPE_SLUG_TO_CODE[typeSlug.toLowerCase()];
  if (!type) throw new Error(`Unrecognized Congress.gov type slug: ${typeSlug}`);
  const congress = Number(congressText);
  const number = Number(numberText);
  return {
    record_kind: recordKind.toLowerCase(),
    congress,
    type,
    number,
    canonical_key: `${congress}:${type}:${number}`,
    canonical_url:
      `https://www.congress.gov/${recordKind.toLowerCase()}/` +
      `${congress}th-congress/${typeSlug.toLowerCase()}/${number}`,
  };
}

function normalizeDate(value) {
  const match = String(value ?? "").match(
    /^(\d{2})\/(\d{2})\/(\d{4})$/,
  );
  return match ? `${match[3]}-${match[1]}-${match[2]}` : null;
}

function windowStatus(value, contract) {
  const isoDate = normalizeDate(value);
  if (!isoDate) return "date_unknown";
  if (isoDate < contract.required_window_start) {
    return "boundary_extension_before_window";
  }
  if (isoDate > contract.required_window_end) {
    return "after_required_window";
  }
  return "within_required_window";
}

export function normalizePageRecords(
  rawRecords,
  entry,
  pageNumber,
  contract,
) {
  return rawRecords.map((record, index) => {
    if (!record.result_url) {
      throw new Error(
        `Missing result URL for ${entry.id} page ${pageNumber} row ${index + 1}`,
      );
    }
    const canonical = parseCanonicalUrl(record.result_url);
    const headingCongress = Number(
      record.heading.match(
        /(?:\u2014|-)\s*(\d+)(?:st|nd|rd|th) Congress/i,
      )?.[1],
    );
    if (headingCongress !== canonical.congress) {
      throw new Error(
        `Heading/URL Congress mismatch for ${record.result_url}: ` +
          `${headingCongress} vs ${canonical.congress}`,
      );
    }
    return {
      ...canonical,
      display_number: record.display_number,
      heading: record.heading,
      title: record.title,
      purpose: record.purpose,
      sponsor: record.sponsor,
      sponsor_url: record.sponsor_url,
      sponsor_line: record.sponsor_line,
      initial_action: record.initial_action,
      initial_action_date: record.initial_action_date,
      initial_action_date_iso: normalizeDate(record.initial_action_date),
      window_status: windowStatus(record.initial_action_date, contract),
      candidate_status: "included_unreviewed_candidate",
      cosponsor_count: record.cosponsor_count,
      committees: record.committees,
      latest_action: record.latest_action,
      latest_action_date: record.latest_action_date,
      amends: record.amends,
      tracker_status: record.tracker_status,
      result_url: record.result_url,
      raw_text: record.raw_text,
      source_query_id: entry.id,
      source_page: pageNumber,
      source_rank: (pageNumber - 1) * CONGRESS_PAGE_SIZE + index + 1,
    };
  });
}

function pageSnapshotEvaluator({ html, loadedUrl }) {
  const clean = (value) =>
    String(value ?? "").replace(/\s+/g, " ").trim();
  const document = new DOMParser().parseFromString(html, "text/html");
  const bodyText =
    document.body?.innerText ?? document.body?.textContent ?? "";
  const mainTexts = [...document.querySelectorAll("#main")]
    .map((node) => node.innerText ?? node.textContent ?? "")
    .filter((value) => clean(value));
  const cards = [
    ...document.querySelectorAll(
      "ol.basic-search-results-lists > li.expanded",
    ),
  ];
  const records = cards.map((card) => {
    const heading = card.querySelector(".result-heading");
    const resultLink = heading?.querySelector("a");
    const itemTexts = [...card.querySelectorAll(":scope > .result-item")].map(
      (node) => clean(node.textContent),
    );
    const itemWithPrefix = (prefix) =>
      itemTexts.find((value) =>
        value.toLowerCase().startsWith(prefix.toLowerCase()),
      ) ?? null;
    const sponsorLine = itemWithPrefix("Sponsor:");
    const sponsorItem = [...card.querySelectorAll(":scope > .result-item")].find(
      (node) => clean(node.textContent).toLowerCase().startsWith("sponsor:"),
    );
    const sponsorLink = sponsorItem?.querySelector('a[href*="/member/"]');
    const actionDateMatch = sponsorLine?.match(
      /\((Introduced|Submitted|Offered|Proposed)\s+(\d{2}\/\d{2}\/\d{4})\)/i,
    );
    const latestActionLine = itemWithPrefix("Latest Action:");
    const latestActionDateMatch = latestActionLine?.match(
      /Latest Action:\s*.*?\s-\s(\d{2}\/\d{2}\/\d{4})\s/i,
    );
    const trackerText =
      card.querySelector(".result-tracker .hide_fromsighted")?.textContent ??
      "";
    const absolute = (raw) => {
      if (!raw) return null;
      try {
        return new URL(raw, loadedUrl).href;
      } catch {
        return null;
      }
    };
    return {
      indicator: clean(card.querySelector(".visualIndicator")?.textContent),
      heading: clean(heading?.textContent),
      display_number: clean(resultLink?.textContent),
      result_url: absolute(resultLink?.getAttribute("href")),
      title: clean(card.querySelector(".result-title")?.textContent) || null,
      purpose: itemWithPrefix("Purpose:"),
      sponsor_line: sponsorLine,
      sponsor:
        sponsorLine
          ?.replace(/^Sponsor:\s*/i, "")
          .replace(
            /\s+\((?:Introduced|Submitted|Offered|Proposed)\s+\d{2}\/\d{2}\/\d{4}\).*$/i,
            "",
          )
          .trim() || null,
      sponsor_url: absolute(sponsorLink?.getAttribute("href")),
      initial_action: actionDateMatch?.[1] ?? null,
      initial_action_date: actionDateMatch?.[2] ?? null,
      cosponsor_count: Number(
        sponsorLine?.match(/Cosponsors:\s*\((\d+)\)/i)?.[1] ?? 0,
      ),
      committees: itemWithPrefix("Committees:"),
      latest_action: latestActionLine,
      latest_action_date: latestActionDateMatch?.[1] ?? null,
      amends: itemWithPrefix("Amends "),
      tracker_status:
        clean(trackerText).replace(/^This bill has the status\s*/i, "") ||
        null,
      raw_text: clean(card.textContent),
    };
  });
  const sponsorLabel = [...document.querySelectorAll("th,dt,strong,b,span")]
    .find((node) => /^Sponsor:\s*$/i.test(clean(node.textContent)));
  let sponsorContainer = sponsorLabel?.parentElement ?? null;
  for (
    let depth = 0;
    sponsorContainer && depth < 5;
    depth += 1, sponsorContainer = sponsorContainer.parentElement
  ) {
    if (sponsorContainer.querySelector('a[href*="/member/"]')) break;
  }
  const detailSponsor =
    sponsorContainer?.querySelector('a[href*="/member/"]') ??
    document.querySelector('a[href*="/member/"]');
  const absoluteSponsor = (() => {
    const raw = detailSponsor?.getAttribute("href");
    if (!raw) return null;
    try {
      return new URL(raw, loadedUrl).href;
    } catch {
      return null;
    }
  })();
  return {
    title: document.title,
    body_text: bodyText,
    main_texts: mainTexts,
    records,
    detail_sponsor_url: absoluteSponsor,
  };
}

function validateLoadedSearchUrl(requestedUrl, loadedUrl, pageNumber) {
  let requested;
  let loaded;
  try {
    requested = new URL(requestedUrl);
    loaded = new URL(loadedUrl);
  } catch {
    return {
      loaded_search_origin: false,
      query_binding_matches: false,
      page_binding_matches: false,
      page_size_binding_matches: false,
      page_sort_binding_matches: false,
    };
  }
  return {
    loaded_search_origin:
      loaded.origin === "https://www.congress.gov" &&
      /advanced-search\/legislation/i.test(loaded.pathname),
    query_binding_matches:
      loaded.searchParams.get("enterTerms") ===
      requested.searchParams.get("enterTerms"),
    page_binding_matches:
      loaded.searchParams.get("page") === String(pageNumber),
    page_size_binding_matches:
      loaded.searchParams.get("pageSize") === String(CONGRESS_PAGE_SIZE),
    page_sort_binding_matches:
      loaded.searchParams.get("pageSort") === CONGRESS_PAGE_SORT,
  };
}

export async function parseAdvancedSearchHttpBody(
  page,
  {
    requestedUrl,
    loadedUrl,
    html,
    entry,
    contract,
    pageNumber,
    expectedTotal = null,
    dualTransportRedirectMatch = false,
  },
) {
  const snapshot = await page.evaluate(pageSnapshotEvaluator, {
    html,
    loadedUrl,
  });
  const query = buildAdvancedSearchQuery(entry);
  const single = classifySingleResultRedirect({
    requestedUrl,
    loadedUrl,
    title: snapshot.title,
    bodyText: snapshot.body_text,
    query,
    congresses: contract.congresses,
    legislationTypes: contract.legislation_types,
    dualTransportRedirectMatch,
  });
  if (single) {
    if (pageNumber !== 1 || (expectedTotal !== null && expectedTotal !== 1)) {
      throw new Error("Unexpected single-result redirect outside page 1");
    }
    const extracted = parseSingleResultPageSnapshot({
      loadedUrl,
      title: snapshot.title,
      bodyText: snapshot.body_text,
      sponsorUrl: snapshot.detail_sponsor_url,
    });
    const canonical = parseCanonicalUrl(extracted.result_url);
    const expectedDisplay = displayNumberForCanonical(single.canonical);
    const extractionValidation = {
      canonical_key_matches_redirect:
        canonical.canonical_key === single.canonical.canonical_key,
      display_number_matches_redirect:
        compact(extracted.display_number)
          .replace(/[^a-z0-9]/gi, "")
          .toLowerCase() ===
        expectedDisplay.replace(/[^a-z0-9]/gi, "").toLowerCase(),
      congress_term_matches_redirect: new RegExp(
        `^${canonical.congress}(?:st|nd|rd|th) Congress\\s+\\([^)]+\\)$`,
        "i",
      ).test(extracted.congress_term ?? ""),
      title_present: Boolean(compact(extracted.title)),
      sponsor_line_present: Boolean(compact(extracted.sponsor_line)),
      initial_action_date_present: Boolean(extracted.initial_action_date),
    };
    if (Object.values(extractionValidation).some((value) => value !== true)) {
      throw new Error(
        `Single-result extraction failed: ${JSON.stringify(extractionValidation)}`,
      );
    }
    const raw = {
      ...extracted,
      heading: `${expectedDisplay} \u2014 ${extracted.congress_term}`,
      extraction_validation: extractionValidation,
    };
    return {
      result_transport: "single_result_redirect",
      range: { start: 1, end: 1, total: 1, header: "Single result 1 of 1" },
      records: normalizePageRecords([raw], entry, 1, contract),
      single_result_redirect: {
        proof: single.proof,
        surface_observations: single.surface_observations,
        cardinality_proof: single.cardinality_proof,
        canonical: single.canonical,
        highlighted_query: single.highlighted_query,
        result_rank: single.result_rank,
        extraction_validation: extractionValidation,
      },
      surface_validation: {
        single_result_redirect_proven: true,
      },
    };
  }

  const mainText = selectStrictMainText(snapshot.main_texts, parseResultRange);
  if (resultSummaryIsInProgress(mainText)) {
    throw new Error(
      "Congress.gov result summary is still in progress; no result accepted",
    );
  }
  const range = parseResultRange(mainText);
  const total = expectedTotal ?? range.total;
  const expectedStart =
    total === 0 ? 0 : (pageNumber - 1) * CONGRESS_PAGE_SIZE + 1;
  const expectedEnd =
    total === 0 ? 0 : Math.min(pageNumber * CONGRESS_PAGE_SIZE, total);
  const expectedCount =
    total === 0 ? 0 : expectedEnd - expectedStart + 1;
  const records = normalizePageRecords(
    snapshot.records,
    entry,
    pageNumber,
    contract,
  );
  const uniqueKeys = new Set(records.map((record) => record.canonical_key));
  const validation = {
    ...validateLoadedSearchUrl(requestedUrl, loadedUrl, pageNumber),
    total_matches_query: range.total === total,
    range_start_matches: range.start === expectedStart,
    range_end_matches: range.end === expectedEnd,
    exact_page_count: records.length === expectedCount,
    unique_within_page: uniqueKeys.size === records.length,
    strict_zero_surface:
      total !== 0 ||
      (pageNumber === 1 &&
        snapshot.records.length === 0 &&
        range.start === 0 &&
        range.end === 0),
  };
  if (Object.values(validation).some((value) => value !== true)) {
    throw new Error(
      `Advanced Search HTTP surface validation failed: ${JSON.stringify({
        validation,
        range,
        expectedStart,
        expectedEnd,
        expectedCount,
        observedCount: records.length,
      })}`,
    );
  }
  return {
    result_transport: "result_list",
    range,
    records,
    single_result_redirect: null,
    surface_validation: validation,
  };
}
