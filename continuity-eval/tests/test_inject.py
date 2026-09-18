# -*- coding: utf-8 -*-
"""Tests for the contradiction injector.

The load-bearing properties, in order of importance:

1. The source project is never modified. Everything else is worthless if this
   fails, because a mutating "injector" would silently corrupt the corpus it is
   supposed to measure against.
2. A run is reproducible from its seed.
3. Labels describe the text that was actually written -- a label that disagrees
   with the scratch file makes every downstream metric a lie.
4. Ordinal contexts (第二天) do not leak into the count dataset.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

EVAL_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL_DIR))

import inject  # noqa: E402
import detect  # noqa: E402
import attributes  # noqa: E402
import corpus_snapshot  # noqa: E402
import numeral  # noqa: E402
import run_eval  # noqa: E402


def tree_hashes(root: pathlib.Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class NumeralTests(unittest.TestCase):
    def test_parse_known_values(self):
        cases = {
            "四十七": 47, "二十五": 25, "三十三": 33, "一万": 10000, "两百": 200,
            "六百": 600, "一千九百零三": 1903, "一百七十五": 175, "十": 10,
            "十五": 15, "廿五": 25, "三十": 30, "两千": 2000, "两万": 20000,
            "二十二": 22, "一百": 100, "零": 0,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(numeral.parse_cn(text), expected)

    def test_parse_rejects_non_numerals(self):
        for text in ("", "他", "的的", "一二三他"):
            with self.subTest(text=text):
                self.assertIsNone(numeral.parse_cn(text))

    def test_render_then_parse_round_trips(self):
        # Rendering Chinese numerals is easy to get subtly wrong, so verify the
        # codec against itself across a wide range before trusting a mutation.
        for value in list(range(0, 1200)) + [1999, 2000, 2001, 1903, 9999, 10000,
                                             10001, 20000, 47, 470, 4700, 47000, 99999]:
            with self.subTest(value=value):
                rendered = numeral.render_cn(value)
                self.assertIsNotNone(rendered)
                self.assertEqual(numeral.parse_cn(rendered), value, f"{value} -> {rendered}")

    def test_render_style_conventions(self):
        self.assertEqual(numeral.render_cn(10), "十")        # not 一十
        self.assertEqual(numeral.render_cn(15), "十五")      # not 一十五
        self.assertEqual(numeral.render_cn(200), "二百")
        self.assertEqual(numeral.render_cn(200, use_liang=True), "两百")
        self.assertEqual(numeral.render_cn(1903), "一千九百零三")  # 零 is not dropped
        self.assertEqual(numeral.render_cn(20), "二十")

    def test_nearby_values_are_close_and_valid(self):
        for value in (47, 33, 25, 1903, 200):
            for candidate in numeral.nearby_values(value):
                self.assertNotEqual(candidate, value)
                self.assertTrue(numeral.round_trips(candidate))


class RewriteFileTests(unittest.TestCase):
    def test_multiple_replacements_with_length_changes_keep_spans_valid(self):
        # The regression this guards against: a replacement that shrinks the
        # text shifts every later span, so spans computed per-replacement go
        # stale. One pass from original coordinates is the fix; this asserts it.
        text = "AAAA-BB-CCCCCC"
        new_text, starts = inject.rewrite_file(
            text, [(0, 4, "X"), (5, 7, "YYY"), (8, 14, "Z")])
        self.assertEqual(new_text, "X-YYY-Z")
        self.assertEqual(starts, [0, 2, 6])
        for start, token in zip(starts, ["X", "YYY", "Z"]):
            self.assertEqual(new_text[start:start + len(token)], token)

    def test_overlapping_replacements_are_rejected(self):
        with self.assertRaises(inject.OverlappingReplacement):
            inject.rewrite_file("abcdef", [(0, 4, "X"), (2, 6, "Y")])

    def test_adjacent_replacements_are_allowed(self):
        new_text, starts = inject.rewrite_file("abcdef", [(0, 3, "XY"), (3, 6, "Z")])
        self.assertEqual(new_text, "XYZ")
        self.assertEqual(starts, [0, 2])


    def test_zip_order_survives_out_of_order_plans(self):
        # A regression guard for the injector's pairing bug: rewrite_file reports
        # offsets in start order, so the caller must sort its replacements the
        # same way or the final_start of one span gets attributed to another.
        text = "AA......BB........CC"
        replacements = [(14, 16, "Z"), (0, 2, "X"), (9, 11, "Y")]
        new_text, starts = inject.rewrite_file(text, replacements)
        # (9,11) covers the second B, so it lands mid-string once sorted
        self.assertEqual(new_text, "X......BY...Z..CC")
        self.assertEqual(starts, [0, 8, 12])
        for start, token in zip(starts, ["X", "Y", "Z"]):
            self.assertEqual(new_text[start:start + len(token)], token)


def spans_overlap(a: list[int], b: list[int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ceval-det-"))
        self.source = self.tmp / "source"
        build_fixture(self.source)
        self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_fixture_yields_zero_findings(self):
        # The false-positive guard: on a corpus with no injected contradictions
        # -- only legitimate coexisting values and enumerations -- the detector
        # must stay silent.
        self.assertEqual(detect.detect_project(self.source), [])

    def test_injected_fixture_is_detected(self):
        _, labels = inject.inject_project(self.source, self.out, 1, 50, None, None)
        findings = detect.detect_project(self.out / "source")
        self.assertTrue(labels)
        self.assertTrue(findings)
        detected = set()
        for label in labels:
            for finding in findings:
                if (finding["file"] == label["file"]
                        and spans_overlap(finding["span"], label["span"])):
                    detected.add(label["label_id"])
        self.assertGreater(len(detected), 0, "no injected label was detected at all")
        rules = {f["rule"] for f in findings}
        # both the declared-fact channel and a prose-only channel must
        # contribute, or the rule set is redundant
        self.assertIn("canon_fact", rules)
        self.assertTrue(rules & {"slot_match", "rare_near_common"})

    def test_detection_is_deterministic(self):
        inject.inject_project(self.source, self.out, 1, 50, None, None)
        first = detect.detect_project(self.out / "source")
        second = detect.detect_project(self.out / "source")
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))

    def test_rare_near_common_baseline_still_fires(self):
        # The frequency-only configuration is the trivial baseline the
        # evaluation compares against; it must be runnable on its own.
        inject.inject_project(self.source, self.out, 1, 50, None, None)
        findings = detect.detect_project(self.out / "source", rules={"rare_near_common"})
        self.assertTrue(all(f["rule"] == "rare_near_common" for f in findings))


    def test_canon_fact_findings_inside_a_progression_are_downgraded(self):
        # 第十四次 vs the declared 第十二次 is a ledger walking forward, not an
        # error: when both values sit inside an established progression the
        # finding must be tagged and downgraded so the author's triage list
        # leads with real suspects.
        root = self.tmp / "prog"
        (root / "manuscript" / "chapters").mkdir(parents=True)
        (root / "continuity").mkdir(parents=True)
        (root / "novel.json").write_text("{}", encoding="utf-8")
        (root / "continuity" / "canon-facts.jsonl").write_text(
            json.dumps({"schema_version": 1, "fact_id": "F-SEQ", "category": "canon_truth",
                        "subject": "台账", "predicate": "报备", "object": "第十二次检修后累计三十三件",
                        "status": "active", "significance": "normal", "valid_from_chapter": 1,
                        "source": {"path": "manuscript/chapters/0001-a.md", "location": "1",
                                   "quote": "第十二次"}}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (root / "manuscript" / "chapters" / "0001-a.md").write_text(
            "第九次。第十次。第十一次。第十二次。第十三次。第十四次。\n", encoding="utf-8")
        findings = detect.detect_project(root)
        tagged = [f for f in findings if f["token"] == "第十四次"]
        self.assertTrue(tagged)
        for finding in tagged:
            with self.subTest(token=finding["token"], value=finding["value"]):
                self.assertEqual(finding["rule"], "canon_fact")
                self.assertTrue(finding.get("progression_suspect"))
                self.assertEqual(finding["severity"], "warning")


class AttributeTests(unittest.TestCase):
    """The name-attribute channel: validated against a known real contradiction."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ceval-attr-"))
        self.source = self.tmp / "source"
        (self.source / "manuscript" / "chapters").mkdir(parents=True)
        (self.source / "continuity").mkdir(parents=True)
        (self.source / "novel.json").write_text(
            json.dumps({"schema_version": 1, "title": "测试书"}, ensure_ascii=False),
            encoding="utf-8")
        (self.source / "continuity" / "canon-facts.jsonl").write_text(
            json.dumps({"schema_version": 1, "fact_id": "F-PERSON",
                        "category": "canon_truth", "subject": "林某人",
                        "predicate": "生卒", "object": "1894年生，2024年12月3日过世",
                        "status": "active", "significance": "major", "valid_from_chapter": 1,
                        "source": {"path": "manuscript/chapters/0001-a.md", "location": "1",
                                   "quote": "2024年12月3日过世"}}, ensure_ascii=False) + "\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_chapter(self, name: str, body: str) -> None:
        (self.source / "manuscript" / "chapters" / name).write_text(body, encoding="utf-8")

    def test_conflicting_death_years_are_flagged(self):
        # prose says 1978, the declared fact says 2024 -- the shape of the real
        # contradiction this channel exists to catch
        self.write_chapter("0001-a.md", "户籍记载：林某人，1894年生。林某人1978年6月过世。\n")
        findings = attributes.attribute_findings(self.source)
        deaths = [f for f in findings if f["name"] == "林某人" and f["attribute"] == "death"]
        self.assertEqual(len(deaths), 1)
        self.assertEqual(deaths[0]["values"], [1978, 2024])
        sources = {c["source"] for c in deaths[0]["claims"]}
        self.assertEqual(sources, {"prose", "fact"})

    def test_consistent_years_are_silent(self):
        self.write_chapter("0001-a.md", "林某人1894年出生。林某人2024年12月过世。\n")
        self.assertEqual(attributes.attribute_findings(self.source), [])

    def test_birth_word_does_not_bind_to_death_value(self):
        # a birth claim and a death claim are different attributes
        self.write_chapter("0001-a.md", "林某人1894年出生。林某人2024年过世。\n")
        findings = attributes.attribute_findings(self.source)
        self.assertEqual([f["attribute"] for f in findings], [])

    def test_local_subject_beats_nearby_listener(self):
        # the listener's dialogue attribution must not steal the claim: the name
        # inside the quote sits directly before the date and wins the binding.
        # Two conflicting birth years make it a finding (the channel reports
        # conflicts, not single claims).
        self.write_chapter(
            "0001-a.md",
            "江某说：\u201c李某，1924年出生。\u201d\n\n"
            "江某又问：\u201c确定是李某，1950年出生？\u201d\n")
        findings = attributes.attribute_findings(self.source)
        bound = {(f["name"], f["attribute"]) for f in findings}
        self.assertIn(("李某", "birth"), bound)
        self.assertNotIn(("江某", "birth"), bound)


class RunEvalReportTests(unittest.TestCase):
    """The second column must land in the merged report -- and stay out of the
    injection scores, which measure a different channel."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ceval-report-"))
        self.source = self.tmp / "source"
        build_fixture(self.source)
        # plant a real name-attribute contradiction in the original corpus:
        # prose says 1978, the declared fact says 2024 (the 张某 shape)
        (self.source / "manuscript" / "chapters" / "0040-生卒.md").write_text(
            "户籍记载：林某人1978年6月过世。\n", encoding="utf-8")
        with (self.source / "continuity" / "canon-facts.jsonl").open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema_version": 1, "fact_id": "F-PERSON",
                                 "category": "canon_truth", "subject": "林某人",
                                 "predicate": "生卒", "object": "1894年生，2024年12月3日过世",
                                 "status": "active", "significance": "major",
                                 "valid_from_chapter": 1,
                                 "source": {"path": "manuscript/chapters/0001-开篇.md",
                                            "location": "1", "quote": "2024年12月3日过世"}},
                                ensure_ascii=False) + "\n")
        self.out = self.tmp / "out"
        self.assertEqual(inject.main(["--project", str(self.source), "--out", str(self.out),
                                      "--seed", "1", "--per-cell", "3"]), 0)
        self.report_path = self.tmp / "report.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_eval(self, extra: list[str]) -> dict:
        # run_eval joins --projects-root with the project id from the labels
        # filename, so the projects root is the fixture's parent directory
        argv = ["--projects-root", str(self.tmp), "--injected-root", str(self.out),
                "--output", str(self.report_path)] + extra
        self.assertEqual(run_eval.main(argv), 0)
        return json.loads(self.report_path.read_text(encoding="utf-8"))

    def test_report_carries_the_second_column(self):
        report = self.run_eval([])
        self.assertIn("attribute_channel", report)
        self.assertEqual(report["attribute_channel"]["corpus"], "original")
        entry = report["projects"]["source"]["attributes"]
        self.assertEqual(entry["corpus"], "original")
        deaths = [f for f in entry["findings"]
                  if f["name"] == "林某人" and f["attribute"] == "death"]
        self.assertEqual(len(deaths), 1)
        self.assertEqual(deaths[0]["values"], [1978, 2024])
        self.assertEqual({c["source"] for c in deaths[0]["claims"]}, {"prose", "fact"})

    def test_attribute_findings_never_join_injection_scores(self):
        # the second column is an adjudication input, not benchmark output: the
        # precision/recall numbers must be identical with and without it
        with_attr = self.run_eval([])
        without_attr = self.run_eval(["--no-attributes"])
        self.assertNotIn("attribute_channel", without_attr)
        self.assertNotIn("attributes", without_attr["projects"]["source"])
        self.assertEqual(with_attr["aggregate"]["full"], without_attr["aggregate"]["full"])
        self.assertEqual(with_attr["aggregate"]["baseline"],
                         without_attr["aggregate"]["baseline"])

    def snapshot_manifest(self) -> pathlib.Path:
        manifest = corpus_snapshot.snapshot_corpus(self.tmp)
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    def test_pinned_snapshot_is_verified_and_recorded(self):
        report = self.run_eval(["--snapshot", str(self.snapshot_manifest())])
        pin = report["corpus_snapshot"]
        self.assertIn("source", pin["projects"])
        self.assertEqual(len(pin["projects"]["source"]), 64)

    def test_corpus_drift_refuses_to_run(self):
        snapshot = self.snapshot_manifest()
        # the corpus the snapshot pinned is no longer the corpus on disk
        chapter = self.source / "manuscript" / "chapters" / "0001-开篇.md"
        chapter.write_text(chapter.read_text(encoding="utf-8") + "改了一行。\n",
                           encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_eval(["--snapshot", str(snapshot)])


class SplitTokenTests(unittest.TestCase):
    def test_splits_marker_numeral_unit(self):
        cases = {
            "四十七个": ("", "四十七", "个"),
            "民国三十四年": ("民国", "三十四", "年"),
            "第18轮": ("第", "18", "轮"),
            "3 秒": ("", "3", "秒"),
            "十七个月": ("", "十七", "个月"),
            "两千": ("", "两千", ""),
        }
        for token, expected in cases.items():
            with self.subTest(token=token):
                self.assertEqual(inject.split_token(token), expected)

    def test_longest_unit_wins(self):
        # 个月 must beat 月, otherwise the numeral text is corrupted.
        self.assertEqual(inject.split_token("十七个月"), ("", "十七", "个月"))
        self.assertEqual(inject.split_token("三十三秒钟"), ("", "三十三秒钟", ""))


class ClassifyTests(unittest.TestCase):
    def test_ordinal_is_not_a_count(self):
        self.assertEqual(inject.classify("第", "二", "天", "cn"), "ordinal")
        self.assertEqual(inject.classify("第", "18", "轮", "ar"), "ordinal")

    def test_generic_quantifiers_are_skipped(self):
        for numeral_text, unit in (("一", "个"), ("一", "次"), ("两", "个"), ("一", "条")):
            with self.subTest(token=numeral_text + unit):
                self.assertIsNone(inject.classify("", numeral_text, unit, "cn"))

    def test_era_and_year(self):
        self.assertEqual(inject.classify("民国", "三十四", "年", "cn"), "era_time")
        self.assertEqual(inject.classify("", "1946", "年", "ar"), "year")

    def test_unit_groups(self):
        self.assertEqual(inject.classify("", "四十七", "个", "cn"), "count")
        self.assertEqual(inject.classify("", "三十三", "秒", "cn"), "duration")
        self.assertEqual(inject.classify("", "两", "万", "cn"), "amount")
        self.assertEqual(inject.classify("", "三", "月", "cn"), "date_part")


def build_fixture(root: pathlib.Path) -> None:
    """A small synthetic project with known token placements and distances."""
    (root / "manuscript" / "chapters").mkdir(parents=True)
    (root / "memory" / "chapters").mkdir(parents=True)
    (root / "continuity").mkdir(parents=True)
    (root / "novel.json").write_text(
        json.dumps({"schema_version": 1, "title": "测试书", "current_chapter": 30},
                   ensure_ascii=False), encoding="utf-8")

    chapters = {
        # intra-chapter repeat (四十七户 x2) + a token shared with 0002/0005/0025.
        # 0002/0005 share a verbatim refrain ("账上：§，年化计息") so the
        # detector's slot_match rule has a real cross-chapter slot to fire on.
        "0001-开篇.md": "四十七户在滞纳。四十七户。三十三秒。九百九十九个。民国三十四年冬。\n",
        "0002-第二天.md": "第二天他就来了。账上：三十三秒，年化计息。\n",
        "0005-中段.md": "账上：三十三秒，年化计息。\n",
        "0025-远段.md": "还是三十三秒。\n",
        "0030-尾段.md": "九百九十九个。三百年。\n",
    }
    for name, body in chapters.items():
        (root / "manuscript" / "chapters" / name).write_text(body, encoding="utf-8")
        card = root / "memory" / "chapters" / f"{name.split('-')[0]}.md"
        card.write_text(f"# card\n\n[{name}](../../manuscript/chapters/{name})\n", encoding="utf-8")

    (root / "continuity" / "canon-facts.jsonl").write_text(
        json.dumps({"schema_version": 1, "fact_id": "F-COUNT", "category": "canon_truth",
                    "subject": "账簿", "predicate": "滞纳户数", "object": "共二千一百四十七户，其中四十七户在滞纳，账簿已传三百年",
                    "status": "active", "significance": "major", "valid_from_chapter": 1,
                    "source": {"path": "manuscript/chapters/0001-开篇.md", "location": "1",
                               "quote": "四十七户在滞纳"}}, ensure_ascii=False) + "\n",
        encoding="utf-8")


class InjectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ceval-"))
        self.source = self.tmp / "source"
        build_fixture(self.source)
        self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_injector(self, **kwargs):
        return inject.inject_project(self.source, self.out, kwargs.pop("seed", 1),
                                     kwargs.pop("per_cell", 3), None, None)

    def test_source_project_is_never_modified(self):
        before = tree_hashes(self.source)
        self.run_injector()
        self.assertEqual(before, tree_hashes(self.source))

    def test_run_is_reproducible_from_seed(self):
        _, first = self.run_injector(seed=7)
        _, second = self.run_injector(seed=7)
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))

    def test_different_seeds_can_differ_but_stay_valid(self):
        _, a = self.run_injector(seed=1)
        _, b = self.run_injector(seed=2)
        self.assertTrue(a and b)
        for label in a + b:
            self.assertNotEqual(label["original"], label["injected"])

    def test_labels_match_the_written_text(self):
        scratch, labels = self.run_injector()
        self.assertTrue(labels)
        for label in labels:
            with self.subTest(label=label["label_id"]):
                path = scratch.joinpath(*pathlib.PurePosixPath(label["file"]).parts)
                text = path.read_text(encoding="utf-8")
                start, end = label["span"]
                self.assertEqual(text[start:end], label["injected"])
                self.assertNotEqual(label["original"], label["injected"])

    def test_every_label_has_surviving_truth(self):
        scratch, labels = self.run_injector(per_cell=50)
        chapters_dir = scratch / "manuscript" / "chapters"
        self.assertTrue(labels)
        for label in labels:
            truth = label["truth"]
            with self.subTest(label=label["label_id"]):
                if truth.get("source") == "fact":
                    # fact-anchored cell: prose truth is zero BY DESIGN and the
                    # declared canon store is the counter-side
                    self.assertEqual(truth["occurrences_remaining"], 0)
                    self.assertTrue(label["canon_fact_ids"])
                    continue
                # A contradiction with no surviving truth is not a contradiction.
                self.assertGreater(truth["occurrences_remaining"], 0)
                self.assertTrue(truth["chapters"])
                total = 0
                for chapter in truth["chapters"]:
                    for f in chapters_dir.glob(f"{chapter}-*.md"):
                        total += f.read_text(encoding="utf-8").count(label["original"])
                self.assertGreaterEqual(total, truth["occurrences_remaining"])

    def test_recorded_distance_is_the_true_minimum_to_surviving_truth(self):
        _, labels = self.run_injector(per_cell=50)
        for label in labels:
            if label["truth"].get("source") == "fact":
                continue    # no prose truth, no distance by design
            truth = label["truth"]
            with self.subTest(label=label["label_id"]):
                expected = min(abs(int(label["chapter"]) - int(c)) for c in truth["chapters"])
                self.assertEqual(truth["distance_chapters"], expected)
                self.assertEqual(label["distance_bucket"], inject.bucket_for(expected))

    def test_a_run_covers_several_distance_buckets(self):
        _, labels = self.run_injector(per_cell=50)
        buckets = {label["distance_bucket"] for label in labels}
        # distance 0 (a chapter contradicting itself) and long-range
        # contradictions must both be represented, or the dataset cannot show
        # that difficulty varies with distance at all.
        self.assertIn("0", buckets)
        self.assertTrue(buckets & {"6-20", "21+"}, f"only got {buckets}")
        self.assertGreaterEqual(len(buckets), 3, f"only got {buckets}")

    def test_cross_chapter_never_consumes_a_tokens_last_chapter(self):
        _, labels = self.run_injector(per_cell=50)
        for label in labels:
            with self.subTest(label=label["label_id"]):
                if label["truth"].get("source") == "fact":
                    continue    # prose truth is zero by design in this cell
                self.assertGreater(label["truth"]["occurrences_remaining"], 0)

    def test_fact_anchor_cell_is_only_catchable_by_the_canon_channel(self):
        # 三百年 exists once (ch0030) and a declared fact asserts it; mutating it
        # wipes the prose truth, so the frequency baseline is blind by
        # construction and the declared-fact channel is the only path.
        _, labels = self.run_injector(per_cell=50)
        fact_labels = [l for l in labels if l["truth"].get("source") == "fact"]
        self.assertTrue(fact_labels, "no fact-anchored label was produced")
        for label in fact_labels:
            with self.subTest(label=label["label_id"]):
                self.assertEqual(label["distance_bucket"], "fact-only")
                self.assertEqual(label["detectable_by"], ["canon_store"])
                self.assertEqual(label["anchor"], "canon")

        scratch = self.out / "source"
        findings = detect.detect_project(scratch)
        baseline = detect.detect_project(scratch, rules={"rare_near_common"})
        for label in fact_labels:
            with self.subTest(label=label["label_id"]):
                hit = [f for f in findings
                       if f["file"] == label["file"] and spans_overlap(f["span"], label["span"])]
                self.assertTrue(hit, "full detector missed the fact-anchored label")
                self.assertIn("canon_fact", {f["rule"] for f in hit})
                blind = [f for f in baseline
                         if f["file"] == label["file"] and spans_overlap(f["span"], label["span"])]
                self.assertFalse(blind, "frequency-only baseline caught a fact-only cell")

    def test_ordinal_context_never_becomes_a_count_label(self):
        _, labels = self.run_injector()
        for label in labels:
            with self.subTest(label=label["label_id"]):
                if label["class"] == "count":
                    self.assertFalse(label["original"].startswith("第"))
                self.assertNotEqual(label["original"], "第二天")

    def test_intra_chapter_injection_reports_distance_zero(self):
        _, labels = self.run_injector(per_cell=50)
        intra = [l for l in labels if l["mode"] == "single_occurrence"]
        self.assertTrue(intra)
        for label in intra:
            with self.subTest(label=label["label_id"]):
                self.assertEqual(label["truth"]["distance_chapters"], 0)
                self.assertEqual(label["distance_bucket"], "0")
                self.assertIn("intra_chapter", label["detectable_by"])

    def test_canon_anchor_is_recorded_when_the_value_is_declared(self):
        _, labels = self.run_injector(per_cell=50)
        anchored = [l for l in labels if l["original"] == "四十七户"]
        self.assertTrue(anchored)
        for label in anchored:
            with self.subTest(label=label["label_id"]):
                self.assertEqual(label["anchor"], "canon")
                self.assertIn("F-COUNT", label["canon_fact_ids"])
                self.assertIn("canon_store", label["detectable_by"])

    def test_default_run_stays_a_small_stratified_sample(self):
        _, labels = self.run_injector(per_cell=2)
        cells: dict[tuple[str, str], int] = {}
        for label in labels:
            cell = (label["class"], label["distance_bucket"])
            cells[cell] = cells.get(cell, 0) + 1
        self.assertTrue(cells)
        for cell, count in cells.items():
            with self.subTest(cell=cell):
                self.assertLessEqual(count, 2)

    def test_bucket_boundaries(self):
        self.assertEqual(inject.bucket_for(0), "0")
        self.assertEqual(inject.bucket_for(1), "1-5")
        self.assertEqual(inject.bucket_for(5), "1-5")
        self.assertEqual(inject.bucket_for(6), "6-20")
        self.assertEqual(inject.bucket_for(20), "6-20")
        self.assertEqual(inject.bucket_for(21), "21+")


if __name__ == "__main__":
    unittest.main()