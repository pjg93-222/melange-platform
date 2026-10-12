# -*- coding: utf-8 -*-
"""Build concise, revision-bound Wikipedia biography summaries for the member desk.

Wikipedia is a display source, never a scoring source.  The producer reads the
current roster's bioguides, joins them to the maintained ``legislators.csv``
Wikipedia identifiers, downloads canonical revision wikitext in API batches,
and writes only short extractive summaries.  No generated sentence can introduce a
fact that was not present verbatim in the fetched revision.

Run::

    python fec/members/wikipedia_biographies.py
    python fec/members/wikipedia_biographies.py --member "Steve Scalise"

The single-member mode merges that refreshed row into an existing artifact.  It
is useful for debugging, but a normal production refresh should rebuild all 533
rows so the coverage receipt remains meaningful.
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as B
from moneyball_core import dump_atomic

import argparse
import concurrent.futures
import csv
import datetime
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error

API = "https://en.wikipedia.org/w/api.php"
OUT = os.path.join(B, "data", "members", "member_wikipedia_summaries.json")
USER_AGENT = "MelangeDashboard/1.0 (public congressional-profile enrichment)"
# TextExtracts only permits one full-page extract per request.  Revision content
# does not have that limitation, so fetch the canonical wikitext in modest
# batches and reduce it to plain prose locally.
BATCH_SIZE = 20

EARLY_HEADINGS = re.compile(
    r"\b(early life|education|early career|background|childhood)\b", re.I
)
PERSONAL_HEADINGS = re.compile(
    r"^(personal life|personal and family life|family|family and personal life|"
    r"private life|marriage)$", re.I
)
EXPERIENCE_HEADINGS = re.compile(
    r"\b(shooting|attack|injur|health(?!\s*care)|illness|cancer|diagnos|myeloma|"
    r"hospital|military service|military career|surviv|accident|kidnapping|"
    r"assassination)\b", re.I
)
CAREER_HEADINGS = re.compile(
    r"\b(career|legislature|house of representatives|u\.s\. house|senate|"
    r"congress|mayor|governor|attorney|business|academic|professional|"
    r"political career|tenure|committee assignments?|party leadership)\b", re.I
)
SKIP_HEADINGS = re.compile(
    r"\b(electoral history|elections?|references|external links|further reading|see also|"
    r"notes|bibliography|works cited)\b", re.I
)


def _norm_title(value):
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip().casefold()


def _clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\[[a-z0-9 ]+\]", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def _strip_balanced(value, opener, closer):
    """Remove nested template/table blocks without a wikitext dependency."""
    output = []
    index = 0
    depth = 0
    while index < len(value):
        if value.startswith(opener, index):
            depth += 1
            index += len(opener)
            continue
        if depth and value.startswith(closer, index):
            depth -= 1
            index += len(closer)
            continue
        if not depth:
            output.append(value[index])
        index += 1
    return "".join(output)


def wikitext_to_plain(value):
    """Reduce canonical Wikipedia wikitext to sectioned, citation-free prose."""
    text = str(value or "")
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<ref\b[^>]*/\s*>", "", text, flags=re.I)
    text = re.sub(r"<ref\b[^>]*>[\s\S]*?</ref\s*>", "", text, flags=re.I)
    text = re.sub(r"<(gallery|timeline|math|code)\b[^>]*>[\s\S]*?</\1\s*>", "", text, flags=re.I)
    text = _strip_balanced(text, "{{", "}}")
    text = _strip_balanced(text, "{|", "|}")
    text = re.sub(r"(?m)^={2,6}\s*(.*?)\s*={2,6}\s*$",
                  r"\n\n__WIKI_SECTION__ \1\n\n", text)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", "", text, flags=re.I)
    # Innermost links first; repeating also handles the occasional nested label.
    for _ in range(3):
        text = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not re.match(r"^[*#;:|!]", line):
            lines.append(line)
    return re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", html.unescape("\n".join(lines))).strip()


def _sentences(value):
    """Conservative sentence split for already-plain Wikipedia prose."""
    text = _clean(value)
    if not text:
        return []
    # Protect the abbreviations that most often occur in congressional leads.
    protected = {
        "U.S.": "U<S>", "D.C.": "D<C>", "St.": "S<T>",
        "Jr.": "J<R>", "Sr.": "S<R>", "Dr.": "D<R>",
        "H.R.": "H<R>", "Rep.": "R<P>", "Sen.": "S<N>",
    }
    for source, token in protected.items():
        text = text.replace(source, token)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    restored = []
    for part in parts:
        for source, token in protected.items():
            part = part.replace(token, source)
        part = part.strip()
        if len(part) >= 20:
            restored.append(part)
    return restored


def split_sections(extract):
    """Recover headings from the Extracts API's plain-section format."""
    sections = {"Lead": []}
    current = "Lead"
    paragraphs = re.split(r"\n\s*\n+", str(extract or "").strip())
    for paragraph in paragraphs:
        paragraph = _clean(paragraph)
        if not paragraph:
            continue
        explicit_heading = paragraph.startswith("__WIKI_SECTION__ ")
        # Hand-written/plain fixtures do not carry the producer marker, so retain
        # a conservative heading fallback. Balanced parentheses keep a clipped
        # template remnant such as "Lisa Ann Murkowski (" in the lead.
        is_heading = explicit_heading or (
            len(paragraph) <= 100
            and len(paragraph.split()) <= 12
            and not re.search(r"[.!?;:]$", paragraph)
            and (paragraph[0].isupper() or paragraph[0].isdigit())
            and paragraph.count("(") == paragraph.count(")")
            and not re.search(r"[,({\-]$", paragraph)
        )
        if is_heading:
            current = (paragraph[len("__WIKI_SECTION__ "):] if explicit_heading else paragraph)
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(paragraph)
    return sections


def _take_from_sections(sections, heading_rx, max_sentences, exclude=None,
                        per_section=None, seen_global=None):
    values = []
    seen = set()
    global_identities = seen_global if seen_global is not None else set()
    for heading, paragraphs in sections.items():
        if heading == "Lead" or SKIP_HEADINGS.search(heading):
            continue
        if not heading_rx.search(heading) or (exclude and exclude.search(heading)):
            continue
        section_count = 0
        for sentence in _sentences(" ".join(paragraphs)):
            key = _clean(sentence).casefold()
            if key in seen or key in global_identities:
                continue
            seen.add(key)
            global_identities.add(key)
            values.append(sentence)
            section_count += 1
            if len(values) >= max_sentences:
                return " ".join(values)
            if per_section and section_count >= per_section:
                break
    return " ".join(values)


def summarize_extract(extract):
    """Return concise extractive blocks; empty categories remain absent.

    The caps intentionally favor breadth over repetition: at most two lead and
    early-life sentences, four career sentences, two family sentences, and one
    sentence from each of up to four distinct notable-experience sections.
    """
    sections = split_sections(extract)
    lead = " ".join(_sentences(" ".join(sections.get("Lead", [])))[:2])
    if not lead:
        for heading, paragraphs in sections.items():
            if heading != "Lead" and not SKIP_HEADINGS.search(heading):
                lead = " ".join(_sentences(" ".join(paragraphs))[:2])
                if lead:
                    break
    # Process sections in display priority, sharing a sentence identity set so
    # a lead sentence repeated under a heading does not occupy that section's
    # cap or crowd out the next unique detail.
    seen_global = set()
    lead_sentences = []
    for sentence in _sentences(lead):
        identity = _clean(sentence).casefold()
        if identity not in seen_global:
            seen_global.add(identity)
            lead_sentences.append(sentence)
    lead = " ".join(lead_sentences)
    early = _take_from_sections(
        sections, EARLY_HEADINGS, 2, seen_global=seen_global
    )
    career = _take_from_sections(
        sections, CAREER_HEADINGS, 4, exclude=PERSONAL_HEADINGS,
        per_section=1, seen_global=seen_global
    )
    personal = _take_from_sections(
        sections, PERSONAL_HEADINGS, 2, seen_global=seen_global
    )
    experiences = _take_from_sections(
        sections, EXPERIENCE_HEADINGS, 4, per_section=1,
        seen_global=seen_global
    )
    result = {}
    for key, value in (
        ("overview", lead),
        ("early_life", early),
        ("career", career),
        ("family_personal", personal),
        ("life_experiences", experiences),
    ):
        value = _clean(value)
        if value:
            result[key] = value

    return result


def _post_json(fields, attempts=7):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        API,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network failures are retried, then fail loudly
            last = exc
            if attempt + 1 < attempts:
                retry_after = 0
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        retry_after = int(exc.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        retry_after = 0
                time.sleep(min(60, max(retry_after, 2 ** attempt)))
    raise RuntimeError("Wikipedia API request failed after %d attempts: %s" % (attempts, last))


def fetch_extracts(titles):
    """Fetch canonical revision pages in batches and map every requested title."""
    def fetch_batch(batch):
        payload = _post_json({
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "prop": "extracts|revisions|info",
            "exintro": "1",
            "explaintext": "1",
            "exlimit": "max",
            "rvprop": "content|ids|timestamp",
            "rvslots": "main",
            "inprop": "url",
            "titles": "|".join(batch),
        })
        query = payload.get("query") or {}
        aliases = {_norm_title(title): _norm_title(title) for title in batch}
        for group in (query.get("normalized") or [], query.get("redirects") or []):
            for alias in group:
                aliases[_norm_title(alias.get("from"))] = _norm_title(alias.get("to"))

        # Follow normalized-title -> redirect-title chains.
        pages = {_norm_title(page.get("title")): page for page in query.get("pages") or []}
        result = {}
        for requested in batch:
            key = _norm_title(requested)
            for _ in range(4):
                next_key = aliases.get(key, key)
                if next_key == key:
                    break
                key = next_key
            result[requested] = pages.get(key, {"title": requested, "missing": True})
        pause = float(os.environ.get("WIKIPEDIA_REQUEST_PAUSE", "0.10"))
        if pause:
            time.sleep(pause)
        return result

    batches = [titles[start:start + BATCH_SIZE]
               for start in range(0, len(titles), BATCH_SIZE)]
    workers = max(1, min(4, int(os.environ.get("WIKIPEDIA_WORKERS", "2"))))
    fetched = {}
    complete = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_batch, batch) for batch in batches]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            fetched.update(result)
            complete += len(result)
            if complete == len(titles) or complete % 20 == 0:
                print("  Wikipedia %d/%d" % (complete, len(titles)), flush=True)
    return fetched


def load_roster():
    with open(os.path.join(B, "data", "engine", "congestion.json"), encoding="utf-8") as handle:
        roster = json.load(handle)
    with open(os.path.join(B, "fec", "legislators.csv"), encoding="utf-8", newline="") as handle:
        legislators = {row.get("bioguide_id"): row for row in csv.DictReader(handle)}
    rows = []
    for member in roster:
        row = legislators.get(member.get("bioguide")) or {}
        rows.append({
            "member": member.get("member"),
            "bioguide": member.get("bioguide"),
            "wikipedia_id": (row.get("wikipedia_id") or "").strip(),
        })
    return rows


def _discover_missing_title(member):
    """Find an exact-name page only; ambiguous search results remain missing."""
    payload = _post_json({
        "action": "query", "format": "json", "formatversion": "2",
        "list": "search", "srlimit": "10",
        "srsearch": 'intitle:"%s" U.S. representative' % member,
    })
    wanted = _norm_title(member)
    candidates = []
    for row in ((payload.get("query") or {}).get("search") or []):
        title = row.get("title") or ""
        base = re.sub(r"\s*\([^)]*\)\s*$", "", title)
        if _norm_title(base) == wanted:
            candidates.append(title)
    return candidates[0] if len(candidates) == 1 else ""


def build(member_filter=None):
    roster = load_roster()
    if member_filter:
        roster = [row for row in roster if row["member"].casefold() == member_filter.casefold()]
        if not roster:
            raise RuntimeError("member is not in the current roster: %s" % member_filter)
    for row in roster:
        if not row["wikipedia_id"]:
            row["wikipedia_id"] = _discover_missing_title(row["member"])
    titles = list(dict.fromkeys(row["wikipedia_id"] for row in roster if row["wikipedia_id"]))
    pages = fetch_extracts(titles)
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    members = {}
    missing = []
    for row in roster:
        title = row["wikipedia_id"]
        page = pages.get(title) if title else None
        revision = ((page or {}).get("revisions") or [{}])[0]
        source_text = (((revision.get("slots") or {}).get("main") or {}).get("content")
                       or (page or {}).get("extract") or "")
        if not page or page.get("missing") or not source_text:
            missing.append(row["member"])
            members[row["member"]] = {
                "status": "missing", "bioguide": row["bioguide"],
                "wikipedia_id": title,
            }
            continue
        sections = summarize_extract(wikitext_to_plain(source_text))
        # Intro extracts may be batched and expand common templates (district,
        # office, birth-date). Prefer that clean lead while the canonical
        # revision wikitext supplies every deeper section.
        intro = " ".join(_sentences(page.get("extract"))[:2])
        if intro:
            sections["overview"] = intro
        members[row["member"]] = {
            "status": "ok",
            "bioguide": row["bioguide"],
            "wikipedia_id": page.get("title") or title,
            "url": page.get("fullurl") or (
                "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
                    str(page.get("title") or title).replace(" ", "_")
                )
            ),
            "revision_id": revision.get("revid"),
            "revision_timestamp": revision.get("timestamp"),
            "summary": sections,
        }
    existing = {}
    if member_filter and os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as handle:
            existing = (json.load(handle).get("members") or {})
        existing.update(members)
        members = existing
    artifact = {
        "schema": "member-wikipedia-biographies-v1",
        "generated_at": generated,
        "source": API,
        "method": "revision-bound Wikipedia wikitext; deterministic extractive summaries",
        "coverage": {
            "roster_members": len(load_roster()),
            "rows": len(members),
            "ok": sum(row.get("status") == "ok" for row in members.values()),
            "missing": sorted(name for name, row in members.items() if row.get("status") != "ok"),
        },
        "members": members,
    }
    dump_atomic(artifact, OUT, ensure_ascii=False, separators=(",", ":"))
    print(
        "WIKIPEDIA BIOGRAPHIES: %d/%d summarized; %d missing; wrote %s"
        % (artifact["coverage"]["ok"], artifact["coverage"]["roster_members"],
           len(artifact["coverage"]["missing"]), OUT)
    )
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--member", help="refresh one exact current-roster member")
    parser.add_argument(
        "--strict", action="store_true",
        help="require an OK, revision-bound biography for every current-roster member",
    )
    args = parser.parse_args()
    artifact = build(args.member)
    if not args.member and artifact["coverage"]["rows"] != artifact["coverage"]["roster_members"]:
        raise RuntimeError("Wikipedia artifact does not account for the full current roster")
    if not args.member and args.strict and artifact["coverage"]["missing"]:
        raise RuntimeError(
            "Wikipedia full-roster refresh left %d member(s) missing: %s"
            % (len(artifact["coverage"]["missing"]),
               ", ".join(artifact["coverage"]["missing"][:10]))
        )


if __name__ == "__main__":
    main()
