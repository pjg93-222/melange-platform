"""v3 member brief batch staging and assembly: batch mixing, hash binding, merge, issue routing, receipt gate.

Stage tests use fake packets (only the fields staging reads).  Assemble tests reuse the synthetic
three-member ledger and the Alex Rich gold-shaped brief from test_member_briefs_v3, so the real
validator judges them.  Nothing here touches real data.
"""
import pytest

from fec.members import assemble_member_briefs_v3 as A
from fec.members import build_member_brief_packets as P
from fec.members import stage_member_brief_batches as S
from fec.members import validate_member_briefs_v3 as V
from fec.tests.members import test_member_briefs_v3 as T


def fake(name, chamber, party, ready=True):
    packet = {"member": {"display_name": name, "chamber": chamber, "bio": {"party": party}}, "evidence_tier": "rich",
              "evidence": [], "gates": {}, "appeals": None, "public_sensibilities": [], "longform": [],
              "archetype_candidates": [], "suggested_animal": None, "coin_recommended": {"value": False},
              "avoid_phrases": ["shared six word phrase one"], "ready_for_writing": ready,
              "not_ready_reasons": [] if ready else ["no speech basis counts row"]}
    packet["packet_sha256"] = P.content_sha256(packet)
    return packet


def fixture_packets(*names):
    packets, _ = T.build()
    return {n: packets[n] for n in names} if names else packets


class Env:
    """Staging dir, packets file and briefs file under tmp_path, with the stage/assemble CLIs wired to them."""

    def __init__(self, tmp_path):
        self.stg = tmp_path / "stg"
        self.packets_path = tmp_path / "packets.json"
        self.briefs_path = tmp_path / "data" / "member_briefs_v3.json"
        self.receipt = tmp_path / "data" / "member_briefs_v3.receipt.json"
        self.prompt = tmp_path / "writer_prompt.md"
        self.prompt.write_text("writer prompt v1", encoding="utf-8")
        self.packet_receipt = tmp_path / "packets.receipt.json"
        P.dump_atomic({"status": "READY", "allow_partial_longform": False}, self.packet_receipt)
        self.ledger = tmp_path / "ledger.json"
        self.ledger.write_text("{}", encoding="utf-8")
        self.exemplars = tmp_path / "exemplars.json"
        self.seed([])

    def seed(self, entries, exemplars=()):
        P.dump_atomic({"coined_registry": entries, "exemplars": list(exemplars)}, self.exemplars)

    def images(self, mapping):
        P.dump_atomic(mapping, self.stg / "registry_images.json")

    def stage(self, packets, *extra):
        P.dump_atomic({"schema": P.SCHEMA, "status": "PACKETS", "count": len(packets), "ledger_sha256": "x",
                       "members": packets}, self.packets_path)
        return S.main(["--packets", str(self.packets_path), "--staging", str(self.stg), "--exemplars", str(self.exemplars), *extra])

    def out(self, batch, mapping):
        P.dump_atomic(mapping, self.stg / "out" / (batch + ".json"))

    def assemble(self, *extra):
        return A.main(["--staging", str(self.stg), "--packets", str(self.packets_path), "--briefs", str(self.briefs_path),
                       "--prompt", str(self.prompt), "--receipt", str(self.receipt), "--packet-receipt", str(self.packet_receipt),
                       "--ledger", str(self.ledger), "--exemplars", str(self.exemplars), *extra])

    def issues(self, batch="batch_001"):
        return P.load_json(self.stg / "issues" / (batch + ".json"))

    def briefs(self):
        return P.load_json(self.briefs_path)

    def batch_item(self, batch, name):
        return next(i for i in P.load_json(self.stg / "batches" / (batch + ".json"))["items"] if i["name"] == name)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def broken(brief=None):
    """A schema failure (do needs 4 items); it stays out of the cross-member repetition check."""
    b = dict(brief or T.good_brief())
    b["do"] = b["do"][:3]
    return b


# ----------------------------------------------------------------------------------- staging ----
def test_batches_mix_chambers_and_parties_and_hold_about_fifteen(env):
    packets = {}
    for i in range(40):
        name = "M%02d" % i
        packets[name] = fake(name, "House" if i < 25 else "Senate", "Democrat" if i % 2 else "Republican")
    assert env.stage(packets) == 0
    manifest = P.load_json(env.stg / "manifest.json")
    sizes = [len(v) for v in manifest["batches"].values()]
    assert len(sizes) == 3 and max(sizes) <= 15 and max(sizes) - min(sizes) <= 1
    for names in manifest["batches"].values():
        assert {packets[n]["member"]["chamber"] for n in names} == {"House", "Senate"}
        assert {packets[n]["member"]["bio"]["party"] for n in names} == {"Democrat", "Republican"}
    assert sorted(manifest["members"]) == sorted(packets)
    for batch_id, names in manifest["batches"].items():
        doc = P.load_json(env.stg / "batches" / (batch_id + ".json"))
        assert [i["name"] for i in doc["items"]] == names
        for item in doc["items"]:
            assert item["packet_sha256"] == packets[item["name"]]["packet_sha256"] == manifest["members"][item["name"]]["packet_sha256"]
            assert manifest["members"][item["name"]]["batch"] == batch_id
            assert item["packet"]["member"]["display_name"] == item["name"] and "avoid_phrases" not in item["packet"]
    assert P.load_json(env.stg / "avoid_phrases.json") == {"avoid_phrases": ["shared six word phrase one"]}


def test_unready_packets_refuse_unless_only_ready_which_lists_the_skipped(env, capsys):
    packets = {"A": fake("A", "House", "Democrat"), "B": fake("B", "Senate", "Republican", ready=False)}
    assert env.stage(packets) == 2
    err = capsys.readouterr().err
    assert "B" in err and "no speech basis counts row" in err
    assert not (env.stg / "batches").exists() and not (env.stg / "manifest.json").exists()
    assert env.stage(packets, "--only-ready") == 0
    manifest = P.load_json(env.stg / "manifest.json")
    assert manifest["skipped"] == {"B": ["no speech basis counts row"]}
    assert list(manifest["members"]) == ["A"] and manifest["batches"] == {"batch_001": ["A"]}


def test_a_packet_whose_stored_hash_does_not_match_its_content_is_refused(env, capsys):
    packets = {"A": fake("A", "House", "Democrat")}
    packets["A"]["packet_sha256"] = "0" * 64
    assert env.stage(packets) == 2
    assert "packet_sha256" in capsys.readouterr().err and not (env.stg / "batches").exists()


# ---------------------------------------------------------------------------------- assembly ----
def test_the_hash_is_stamped_from_the_staged_input_never_from_the_writer(env):
    packets = fixture_packets("Alex Rich")
    assert env.stage(packets) == 0
    env.out("batch_001", {"Alex Rich": {**T.good_brief(), "packet_sha256": "f" * 64}})
    assert env.assemble() == 0
    doc = env.briefs()
    brief = doc["briefs"]["Alex Rich"]
    assert brief["packet_sha256"] == packets["Alex Rich"]["packet_sha256"] != "f" * 64
    assert set(brief) == set(V.KEYS) | {"packet_sha256"}            # the validator rejects any other brief key
    meta = doc["writer"]["Alex Rich"]
    assert (meta["model"], meta["effort"]) == ("claude-haiku-5-5", "high")
    assert meta["prompt_sha256"] == P.sha256_file(env.prompt) and meta["written_at"].endswith("Z")
    assert doc["schema"] == V.BRIEFS_SCHEMA
    assert env.issues()["failed"] == {} and env.issues()["passed"] == ["Alex Rich"] and env.issues()["missing"] == []
    assert not (env.stg / "backups").exists()                        # nothing existed to back up


def test_a_member_the_writer_did_not_return_is_reported_missing_and_blocks_the_receipt(env, capsys):
    assert env.stage(fixture_packets()) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief()})
    assert env.assemble("--write-receipt") == 1
    assert env.issues()["missing"] == ["Beth Thin", "Cam Floor"]
    out = capsys.readouterr().out
    assert "Beth Thin" in out and "Cam Floor" in out
    assert not env.receipt.exists()


def test_validator_failures_are_routed_to_the_issues_file_of_their_batch(env):
    assert env.stage(fixture_packets(), "--batch-size", "2") == 0
    where = {n: m["batch"] for n, m in P.load_json(env.stg / "manifest.json")["members"].items()}
    assert len(set(where.values())) == 2
    writes = {"Alex Rich": T.good_brief(), "Beth Thin": broken(), "Cam Floor": {k: v for k, v in T.good_brief().items() if k != "evidence_refs"}}
    for batch in set(where.values()):
        env.out(batch, {n: b for n, b in writes.items() if where[n] == batch})
    assert env.assemble() == 1
    failed = {b: env.issues(b)["failed"] for b in set(where.values())}
    for name in ("Beth Thin", "Cam Floor"):
        assert {i["kind"] for i in failed[where[name]][name]["issues"]} == {"schema"}
        assert all(name not in f for b, f in failed.items() if b != where[name])      # only in its own batch's file
    assert failed[where["Alex Rich"]] == {} and env.issues(where["Alex Rich"])["passed"] == ["Alex Rich"]


def test_an_existing_briefs_file_is_backed_up_before_it_is_overwritten(env):
    env.briefs_path.parent.mkdir(parents=True)
    P.dump_atomic({"schema": V.BRIEFS_SCHEMA, "briefs": {}, "note": "old"}, env.briefs_path)
    before = env.briefs_path.read_bytes()
    assert env.stage(fixture_packets("Alex Rich")) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief()})
    assert env.assemble() == 0
    backups = list((env.stg / "backups").glob("member_briefs_v3.*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert env.briefs_path.read_bytes() != before and "Alex Rich" in env.briefs()["briefs"]


def test_receipt_is_written_only_when_every_member_passes(env):
    assert env.stage(fixture_packets("Alex Rich")) == 0
    env.out("batch_001", {"Alex Rich": broken()})
    assert env.assemble("--write-receipt") == 1 and not env.receipt.exists()
    assert list(env.issues()["failed"]) == ["Alex Rich"]
    env.out("batch_001", {"Alex Rich": T.good_brief()})                # the rewrite replaces the failed brief
    assert env.assemble("--write-receipt") == 0
    receipt = P.load_json(env.receipt)
    assert receipt["status"] == "PASS" and receipt["counts"] == {"briefs": 1, "passed": 1, "failed": 0}
    assert env.issues()["failed"] == {} and len(list((env.stg / "backups").glob("*.json"))) == 1


def test_an_unreadable_output_is_reported_and_blocks_the_receipt_even_if_earlier_briefs_pass(env):
    assert env.stage(fixture_packets("Alex Rich")) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief()})
    assert env.assemble() == 0
    (env.stg / "out" / "batch_001.json").write_text('{"Alex Rich": {', encoding="utf-8")   # a writer died mid-file
    assert env.assemble("--write-receipt") == 1 and not env.receipt.exists()
    assert "batch_001.json" in env.issues()["error"] and env.issues()["passed"] == ["Alex Rich"]


def test_restaging_refuses_while_writer_output_from_an_earlier_staging_exists(env, capsys):
    packets = fixture_packets("Alex Rich")
    assert env.stage(packets) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief()})               # assembling would stamp this with the new hashes
    assert env.stage(packets) == 2
    assert "earlier staging" in capsys.readouterr().err
    assert env.stage(packets, "--force") == 0


def test_members_already_merged_are_not_missing_when_a_later_round_omits_them(env):
    assert env.stage(fixture_packets()) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief()})
    assert env.assemble() == 1
    env.out("batch_001", {"Beth Thin": broken()})
    assert env.assemble() == 1
    assert env.issues()["missing"] == ["Cam Floor"]
    assert set(env.briefs()["briefs"]) == {"Alex Rich", "Beth Thin"}


def test_a_writer_name_that_was_not_staged_is_reported_and_not_merged(env):
    assert env.stage(fixture_packets("Alex Rich")) == 0
    env.out("batch_001", {"Alex Rich": T.good_brief(), "Alex Richh": T.good_brief()})
    assert env.assemble() == 0
    assert env.issues()["unexpected_in_output"] == ["Alex Richh"] and list(env.briefs()["briefs"]) == ["Alex Rich"]


def test_phrases_shared_by_many_briefs_are_written_back_for_the_next_round(env):
    assert env.stage(fixture_packets()) == 0
    env.out("batch_001", {n: T.good_brief() for n in ("Alex Rich", "Beth Thin", "Cam Floor")})
    assert env.assemble() == 1                                         # identical text: names, pronouns and repetition fail
    phrases = P.load_json(env.stg / "avoid_phrases.json")["avoid_phrases"]
    assert phrases and phrases == P.compute_avoid_phrases(env.briefs()["briefs"])


# ------------------------------------------------------------------------- series (--batch N) ----
def coin_inputs():
    """Two thin House members with the same profile (practical, wide-ranging) that no template animal fits, so each is a coin."""
    names = ("Dana Coin", "Eli Coin")

    def rec(name):
        r = T.record(traits={"Openness": "well below most"}, behavior={"policy_breadth": "well above most"},
                     frame=T.MIXED[0], supported=T.MIXED[1])
        r["name"], r["chamber"] = name, "House"
        return r
    return dict(
        ledger_doc={"schema": "member-narrative-evidence-v4", "status": "ACTIVE_FAIL_CLOSED_INPUT", "sources_sha256": {},
                    "members": {n: rec(n) for n in names}},
        legislators=[{"bioguide_id": "D1", "gender": "F", "party": "Democrat", "last_name": "Coin"},
                     {"bioguide_id": "E1", "gender": "M", "party": "Republican", "last_name": "Coin"}],
        congestion=[{"member": "Dana Coin", "bioguide": "D1", "chamber": "House"}, {"member": "Eli Coin", "bioguide": "E1", "chamber": "House"}],
        model_bio={}, desk_src={"members": [{"name": n, "district": "Oklahoma"} for n in names]}, wiki={},
        basis_counts={n: T.COUNTS["Alex Rich"] for n in names},
        levels_doc={"status": "FINAL", "generated_at": "x", "census": {}, "members": {n: T.level(n) for n in names}})


def coin_packets(**kw):
    packets, problems = P.build_all(**{**coin_inputs(), "coined": [], **kw})
    assert problems == []
    return packets


def coin_brief(animal="Beaver"):
    """Schema-shaped, not validator-clean: the registry grows from the archetype alone."""
    b = T.good_brief()
    b["archetype"] = {"animal": animal, "descriptor": "practical and wide-ranging",
                      "justification": "She is practical and her bills cover a wide range of issues."}
    b["evidence_refs"]["archetype"] = ["BH.breadth", "TR.O"]
    return b


BEAVER_IMAGE = "a beaver that builds with others"


def series_after_batch_one(env):
    """Stage Dana (batch_001) and Eli (batch_002), have the writer return Dana's coin, assemble batch 1, restage batch 2."""
    packets = coin_packets()
    assert env.stage(packets, "--batch-size", "1") == 0
    where = {n: m["batch"] for n, m in P.load_json(env.stg / "manifest.json")["members"].items()}
    assert where == {"Dana Coin": "batch_001", "Eli Coin": "batch_002"}
    env.images({"Beaver": BEAVER_IMAGE})
    env.out("batch_001", {"Dana Coin": coin_brief()})
    env.assemble("--batch", "1")                              # the brief fails content checks; its coin still registers
    assert env.stage(packets, "--batch", "2") == 0
    return packets


def test_a_coin_in_batch_one_is_offered_as_a_registry_match_in_batch_two(env):
    packets = coin_packets()
    assert env.stage(packets, "--batch-size", "1") == 0
    before = env.batch_item("batch_002", "Eli Coin")
    assert before["packet"]["coin_recommended"]["value"] and not before["packet"]["coin_recommended"]["registry_match"]
    env.images({"Beaver": BEAVER_IMAGE})
    env.out("batch_001", {"Dana Coin": coin_brief()})
    env.assemble("--batch", "1")
    registry = P.load_json(env.stg / "registry.json")
    assert [e["animal"] for e in registry["coined_animals"]] == ["Beaver"] and registry["assembled_batches"] == ["batch_001"]
    assert registry["coined_animals"][0]["signature"] == [["BH.breadth", "HI"], ["TR.O", "LO"]] and registry["coined_animals"][0]["image"] == BEAVER_IMAGE
    batch_one = (env.stg / "batches" / "batch_001.json").read_bytes()
    assert env.stage(packets, "--batch", "2") == 0
    item = env.batch_item("batch_002", "Eli Coin")
    assert item["packet"]["coin_recommended"]["registry_match"]["animal"] == "Beaver" and item["packet"]["coin_recommended"]["value"]
    assert [e["animal"] for e in item["packet"]["coined_animals"]] == ["Beaver"]
    # the registry is not part of the hash: the staged hash is unchanged and still matches the packet content
    assert item["packet_sha256"] == before["packet_sha256"] == packets["Eli Coin"]["packet_sha256"] == P.content_sha256(item["packet"])
    assert (env.stg / "batches" / "batch_001.json").read_bytes() == batch_one        # only batch 2 was rebuilt


def test_earlier_briefs_are_not_stale_after_the_registry_grows(env):
    series_after_batch_one(env)
    briefs = env.briefs()["briefs"]
    fresh = coin_packets(finished=briefs, registry_images={"Beaver": BEAVER_IMAGE})      # what the desk recomputes: a whole build with the grown registry
    assert briefs["Dana Coin"]["packet_sha256"] == fresh["Dana Coin"]["packet_sha256"]       # not stale
    assert fresh["Eli Coin"]["coin_recommended"]["registry_match"]["animal"] == "Beaver"     # and the registry did grow
    staged = env.batch_item("batch_002", "Eli Coin")["packet"]
    assert staged["coin_recommended"] == fresh["Eli Coin"]["coin_recommended"]               # restaging equals a full rebuild
    assert staged["coined_animals"] == fresh["Eli Coin"]["coined_animals"]


def test_assemble_judges_a_batch_against_the_registry_offer_its_writer_saw(env):
    series_after_batch_one(env)
    env.out("batch_002", {"Eli Coin": coin_brief("Otter")})   # the file's packet offers no match; the staged one says Beaver
    assert env.assemble("--batch", "2") == 1
    details = [i["detail"] for i in env.issues("batch_002")["failed"]["Eli Coin"]["issues"] if i["kind"] == "archetype_misfit"]
    assert any("animal must be Beaver" in d for d in details), details


def test_batch_two_is_refused_until_batch_one_has_been_assembled(env, capsys):
    packets = coin_packets()
    assert env.stage(packets, "--batch-size", "1") == 0
    assert env.stage(packets, "--batch", "2") == 2 and "batch_001" in capsys.readouterr().err
    assert env.stage(packets, "--batch", "2", "--force") == 0
    assert env.stage(packets, "--batch", "1") == 0            # nothing precedes batch 1
    assert env.stage(packets, "--batch", "9") == 2 and "batch_009" in capsys.readouterr().err


def test_the_exemplars_seed_is_offered_from_batch_one(env):
    seed = [{"animal": "Marten", "descriptor": "practical and wide-ranging", "image": "a marten in the high branches", "first_member": "Gold",
             "signature": [["BH.breadth", "hi"], ["TR.O", "lo"]]}]
    env.seed(seed)
    packets = coin_packets(coined=P.registry_seed({"coined_registry": seed}))
    assert env.stage(packets, "--batch-size", "1") == 0 and env.stage(packets, "--batch", "1") == 0
    packet = env.batch_item("batch_001", "Dana Coin")["packet"]
    assert packet["coin_recommended"]["registry_match"]["animal"] == "Marten"
    assert [e["animal"] for e in packet["coined_animals"]] == ["Marten"]


def test_batch_mode_refuses_packets_built_with_a_registry_the_series_does_not_have(env, capsys):
    ghost = [{"animal": "Ghost", "descriptor": "x and y", "image": None, "first_member": None,
              "signature": [["TR.O", "lo"], ["BH.breadth", "lo"]]}]
    packets = coin_packets(coined=ghost)                      # e.g. the builder was run with --briefs on an older briefs file
    assert env.stage(packets, "--batch-size", "1") == 0
    assert env.stage(packets, "--batch", "1") == 2 and "Ghost" in capsys.readouterr().err


def test_series_assemble_judges_only_its_batch_and_never_writes_a_receipt(env):
    assert env.stage(fixture_packets(), "--batch-size", "2") == 0
    alex = P.load_json(env.stg / "manifest.json")["members"]["Alex Rich"]["batch"]
    env.out(alex, {"Alex Rich": T.good_brief()})              # the other batch has not been written yet
    assert env.assemble("--batch", str(int(alex[-3:]))) == 0
    assert env.issues(alex)["passed"] == ["Alex Rich"]
    assert len(list((env.stg / "issues").glob("*.json"))) == 1
    with pytest.raises(SystemExit):
        env.assemble("--batch", str(int(alex[-3:])), "--write-receipt")


# ------------------------------------------------------------------ registry images (pilot 2 review) ----
def test_a_new_coin_without_an_image_is_listed_not_offered_and_a_bad_image_fails_closed(env, capsys):
    packets = coin_packets()
    assert env.stage(packets, "--batch-size", "1") == 0
    env.out("batch_001", {"Dana Coin": coin_brief()})
    env.assemble("--batch", "1")
    registry = P.load_json(env.stg / "registry.json")
    assert registry["coined_animals"] == [] and [x["animal"] for x in registry["pending_images"]] == ["Beaver"]
    assert "PENDING IMAGE" in capsys.readouterr().out
    assert P.load_json(env.stg / "batches" / "batch_002.json")["items"][0]["packet"]["coined_animals"] == []
    env.images({"Beaver": "a beaver that builds with others, always, in every kind of weather"})
    assert env.assemble("--batch", "1") == 2 and "image" in capsys.readouterr().err
