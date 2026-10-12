# Official roll-call action-source contract

`CONGRESS_API_KEY` is not structurally required for accepted bills and
resolutions. The staged collector's default `auto` policy uses the official
GovInfo/Congress.gov BILLSTATUS bulk record for these eight type-aware measure
classes:

`hr`, `s`, `hres`, `sres`, `hjres`, `sjres`, `hconres`, and `sconres`.

For every accepted measure, the collector:

1. constructs the canonical GovInfo BILLSTATUS URL from
   Congress/type/number;
2. requires the XML to contain exactly the same typed identity;
3. preserves its complete actions collection and every recorded-vote
   candidate;
4. derives a canonical House Clerk or Senate XML URL only from the immutable
   Congress/chamber/session/roll event key;
5. verifies that event key and the exact typed measure again inside the
   official chamber XML; and
6. fails closed on unresolved member identity, direction, action, event, or
   measure evidence.

The accepted generation's `latest_action` and `latest_action_date` fields are
not exhaustive action history. They cannot establish that a measure had no
final-passage roll call and are never used as negative evidence.

BILLSTATUS has no top-level `hamdt` or `samdt` records. An accepted amendment
therefore requires either a populated `CONGRESS_API_KEY` or an exhaustive,
hash-bound fixture of the official Congress.gov amendment actions. The
collector does not infer an amendment vote from a parent bill.

The supplemental member map is independently optional. Official XML Bioguide
IDs are used directly; a map may supply exact official-ID-to-Bioguide links,
but missing or ambiguous identities still block acceptance.

All outputs are staging-only. Quorum and third-party vote data are prohibited,
and the collector never writes the production `vote_category_lean.json`.
