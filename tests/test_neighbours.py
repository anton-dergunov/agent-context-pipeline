import tempfile
import unittest
from pathlib import Path

from info_triage.neighbours import (
    BLOCK_BEGIN,
    BM25,
    Neighbour,
    annotate,
    apply_block,
    build_index,
    neighbours_for,
    parse_org,
    parse_vault,
    render_block,
    short,
    tokenize,
)

ORG = """\
#+TITLE: Systems
#+SUBTITLE: How the serving stack is put together

Everything about serving models to users.

* LLM inference and serving
Prose about the section.
** TODO Prefill-decode disaggregation and KV cache transfer   :ml:infra:
SCHEDULED: <2026-08-20 Thu>
:PROPERTIES:
:ID: 1234
:END:
Move the cache between instances.
* Another area
Unrelated body.
"""

VAULT = """\
Opening line of the note.

## Watermarking
Statistical watermarking of generated text, and how to detect it.

## Zero-shot detection
Curvature of the log-likelihood surface.
"""


def corpus(root: Path) -> tuple[Path, Path]:
    org_root = root / "org"
    vault_root = root / "vault"
    (org_root / "ML").mkdir(parents=True)
    (org_root / "ML" / "Systems.org").write_text(ORG, encoding="utf-8")
    (vault_root / "Notes").mkdir(parents=True)
    (vault_root / "Notes" / "Detection.md").write_text(VAULT, encoding="utf-8")
    return org_root, vault_root


def constant_scorer(scores: dict[str, float]):
    """Score a candidate by the first fragment of SCORES its text contains."""

    def score(query: str, documents):
        return [
            next((value for text, value in scores.items() if text in document), -99.0)
            for document in documents
        ]

    return score


class OrgParsingTests(unittest.TestCase):
    def parse(self) -> list:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Systems.org"
            path.write_text(ORG, encoding="utf-8")
            return parse_org(path, "ML/Systems.org")

    def test_a_task_carries_its_keyword_ancestors_and_line(self):
        task = next(unit for unit in self.parse() if unit.kind == "org-task")
        self.assertEqual(task.keyword, "TODO")
        self.assertEqual(task.title, "Prefill-decode disaggregation and KV cache transfer")
        self.assertEqual(task.path, "ML/Systems.org > LLM inference and serving")
        self.assertEqual(task.line, 8)

    def test_drawers_and_scheduling_stay_out_of_the_indexed_text(self):
        task = next(unit for unit in self.parse() if unit.kind == "org-task")
        self.assertIn("Move the cache between instances.", task.text)
        for noise in ("SCHEDULED:", ":PROPERTIES:", ":ID:", ":END:"):
            self.assertNotIn(noise, task.text)

    def test_the_charter_holds_the_subtitle_and_the_opening_prose(self):
        charter = next(unit for unit in self.parse() if unit.kind == "org-charter")
        self.assertEqual(charter.title, "Systems")
        self.assertEqual(charter.line, 1)
        self.assertIn("How the serving stack is put together", charter.text)
        self.assertIn("Everything about serving models", charter.text)

    def test_a_heading_without_a_keyword_is_a_section(self):
        kinds = {unit.title: unit.kind for unit in self.parse()}
        self.assertEqual(kinds["LLM inference and serving"], "org-section")
        self.assertEqual(kinds["Another area"], "org-section")


class VaultParsingTests(unittest.TestCase):
    def test_sections_split_on_headings_and_keep_their_breadcrumb(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Detection.md"
            path.write_text(VAULT, encoding="utf-8")
            units = parse_vault(path, "Notes/Detection.md")
        self.assertEqual(
            [(unit.title, unit.line) for unit in units],
            [("Detection", 1), ("Watermarking", 4), ("Zero-shot detection", 7)],
        )
        self.assertEqual(units[1].path, "Notes/Detection > Watermarking")

    def test_two_windows_of_one_section_get_different_line_numbers(self):
        """A real prototype bug: both halves of a long section cited one line."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Long.md"
            body = "\n".join(
                " ".join(f"w{line}x{word}" for word in range(20)) for line in range(10)
            )
            path.write_text(f"## Section\n{body}\n", encoding="utf-8")
            units = parse_vault(path, "Long.md", max_words=60)
        self.assertGreater(len(units), 1)
        self.assertEqual(len({unit.line for unit in units}), len(units))
        self.assertEqual(units[0].line, 2)


class BM25Tests(unittest.TestCase):
    def test_the_rarer_term_decides_the_ranking(self):
        documents = [
            tokenize("cache eviction in a serving system"),
            tokenize("prefill decode disaggregation cache transfer"),
            tokenize("cache invalidation naming and other hard problems"),
        ]
        scores = BM25(documents).score(tokenize("disaggregation cache"))
        self.assertEqual(int(scores.argmax()), 1)

    def test_an_absent_term_scores_nothing(self):
        scores = BM25([tokenize("serving and inference")]).score(tokenize("beekeeping"))
        self.assertEqual(list(scores), [0.0])

    def test_each_corpus_keeps_its_own_index(self):
        """Design §6b: a shared index lets vault growth move Org-side ranking."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            org_root, vault_root = corpus(root)
            query = tokenize("KV cache transfer between instances")
            before = build_index(org_root, vault_root).indices["org"].score(query)
            for number in range(50):
                (vault_root / "Notes" / f"grown-{number}.md").write_text(
                    "## Cache\nnotes about cache transfer and instances\n", encoding="utf-8"
                )
            after = build_index(org_root, vault_root).indices["org"].score(query)
        self.assertEqual(list(before), list(after))


class RankingTests(unittest.TestCase):
    def rank(self, scorer, **keywords):
        with tempfile.TemporaryDirectory() as temporary:
            org_root, vault_root = corpus(Path(temporary))
            index = build_index(org_root, vault_root)
            return neighbours_for(
                index, "KV cache transfer and watermarking", scorer=scorer, **keywords
            )

    def test_the_destination_is_the_top_plan_row_s_file(self):
        destination, rows = self.rank(constant_scorer({"Prefill-decode": 2.0, "Watermarking": 1.0}))
        self.assertEqual(destination, "ML/Systems.org")
        self.assertEqual([(row.source, row.line) for row in rows], [("plan", 8), ("vault", 4)])

    def test_a_charter_is_never_emitted_and_never_the_destination(self):
        destination, rows = self.rank(
            constant_scorer({"How the serving stack": 5.0, "Watermarking": 1.0})
        )
        self.assertIsNone(destination)
        self.assertEqual([row.title for row in rows], ["Watermarking"])

    def test_only_the_best_row_of_a_file_is_kept(self):
        _, rows = self.rank(constant_scorer({"Systems.org": 1.0}))
        self.assertEqual([row.source for row in rows], ["plan"])

    def test_nothing_below_the_cut_is_emitted(self):
        destination, rows = self.rank(constant_scorer({"Prefill-decode": -3.5}))
        self.assertIsNone(destination)
        self.assertEqual(rows, [])

    def test_at_most_two_rows_per_corpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            org_root, vault_root = corpus(root)
            for number in range(6):
                (vault_root / "Notes" / f"cache-{number}.md").write_text(
                    "## Cache\nKV cache transfer between instances\n", encoding="utf-8"
                )
            index = build_index(org_root, vault_root)
            _, rows = neighbours_for(
                index,
                "KV cache transfer between instances",
                scorer=lambda query, docs: [1.0] * len(docs),
            )
        self.assertLessEqual(sum(1 for row in rows if row.source == "vault"), 2)
        self.assertLessEqual(sum(1 for row in rows if row.source == "plan"), 2)


class ShortQueryTests(unittest.TestCase):
    def test_the_rerank_query_stops_after_the_first_sentences(self):
        query = "A title about caches. The first sentence of the lead. " + "word " * 100
        self.assertEqual(
            short(query, words=8), "A title about caches. The first sentence of the lead."
        )


class BlockTests(unittest.TestCase):
    rows = [
        Neighbour("plan", "ML/Systems.org", 324, "ML/Systems.org > LLM inference", "Prefill", 1.2),
        Neighbour("vault", "ML/Detect.md", 6, "ML/Detect > Watermarking", "Watermarking", -1.5),
    ]

    def test_the_block_names_a_destination_labels_each_row_and_stays_a_list(self):
        block = render_block("ML/Systems.org", self.rows)
        self.assertTrue(block.startswith(BLOCK_BEGIN))
        self.assertIn("Most likely destination file: `ML/Systems.org`", block)
        self.assertIn("Machine retrieval, not a finding.", block)
        self.assertIn("- plan  `ML/Systems.org:324` strong — LLM inference > Prefill", block)
        self.assertIn("- vault `ML/Detect.md:6` likely — Watermarking", block)
        self.assertNotIn("|", block)

    def test_no_rows_means_no_block_at_all(self):
        self.assertEqual(render_block("ML/Systems.org", []), "")

    def test_the_block_lands_before_the_problems_section(self):
        index = "---\nid: x\n---\n\n## Links\n\n- a\n\n## Problems\n\n- broke\n"
        applied = apply_block(index, render_block(None, self.rows))
        self.assertLess(applied.index("## Links"), applied.index(BLOCK_BEGIN))
        self.assertLess(applied.index(BLOCK_BEGIN), applied.index("## Problems"))
        self.assertTrue(applied.endswith("- broke\n"))

    def test_annotating_twice_leaves_one_block(self):
        index = "---\nid: x\n---\n\n## Links\n\n- a\n"
        block = render_block("ML/Systems.org", self.rows)
        once = apply_block(index, block)
        self.assertEqual(apply_block(once, block), once)
        self.assertEqual(once.count(BLOCK_BEGIN), 1)

    def test_an_empty_block_restores_the_index_it_was_added_to(self):
        for index in (
            "---\nid: x\n---\n\n## Links\n\n- a\n",
            "---\nid: x\n---\n\n## Links\n\n- a\n\n## Problems\n\n- broke\n",
        ):
            annotated = apply_block(index, render_block(None, self.rows))
            self.assertEqual(apply_block(annotated, ""), index)


class AnnotateTests(unittest.TestCase):
    def test_only_items_with_neighbours_get_a_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            org_root, vault_root = corpus(Path(temporary))
            reported: list[tuple[str, int, int]] = []
            blocks = annotate(
                [("2026-08-09_1", "KV cache transfer"), ("2026-08-09_2", "beekeeping in autumn")],
                org_root,
                vault_root,
                scorer=constant_scorer({"Prefill-decode": 1.0}),
                progress=lambda label, done, total: reported.append((label, done, total)),
            )
        self.assertEqual(list(blocks), ["2026-08-09_1"])
        self.assertIn("ML/Systems.org:8", blocks["2026-08-09_1"])
        self.assertIn(("2026-08-09_2", 2, 2), reported)

    def test_an_item_with_no_query_is_skipped_rather_than_searched(self):
        with tempfile.TemporaryDirectory() as temporary:
            org_root, vault_root = corpus(Path(temporary))
            blocks = annotate(
                [("2026-08-09_1", "   ")],
                org_root,
                vault_root,
                scorer=constant_scorer({"Prefill-decode": 9.0}),
            )
        self.assertEqual(blocks, {})


if __name__ == "__main__":
    unittest.main()
