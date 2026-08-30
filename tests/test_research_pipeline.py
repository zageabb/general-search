import unittest

from search import best_passages, clean_queries, evidence_ledger, rank_candidates


class ResearchPipelineTest(unittest.TestCase):
    def test_rank_candidates_prefers_relevance_and_authority(self):
        candidates = [
            {"title": "Generic home page", "url": "https://example.com/", "snippet": "Welcome to our site", "query": "battery safety report"},
            {"title": "Battery safety annual report", "url": "https://agency.gov.uk/research/battery-safety", "snippet": "Official battery incident statistics and safety findings", "query": "battery safety report"},
        ]

        ranked = rank_candidates(candidates, "What do official reports say about battery safety?", [], [])

        self.assertEqual(ranked[0]["url"], "https://agency.gov.uk/research/battery-safety")
        self.assertEqual([row["rank"] for row in ranked], [1, 2])

    def test_best_passages_finds_relevant_text_beyond_page_start(self):
        content = "\n".join([
            "This introductory material discusses the organisation and its history in broad terms.",
            "Navigation information and general contact details are available elsewhere on the website.",
            "The 2026 battery safety study recorded a 24 percent reduction in thermal incidents after the new standard.",
            "An unrelated closing paragraph describes office opening hours and mailing addresses.",
        ])

        passages = best_passages(content, "2026 battery safety thermal incident reduction", limit=2)

        self.assertTrue(any("24 percent reduction" in passage for passage in passages))

    def test_evidence_ledger_preserves_claims_passages_and_ids(self):
        ledger = evidence_ledger([{
            "source_id": 1,
            "title": "Official report",
            "url": "https://example.gov/report",
            "query": "official report",
            "claims": ["Incidents declined in 2026."],
            "passages": ["The report recorded fewer incidents in 2026."],
        }])

        self.assertIn("[1] Official report", ledger)
        self.assertIn("Incidents declined in 2026.", ledger)
        self.assertIn("Passage 1:", ledger)

    def test_clean_queries_rejects_malformed_model_output(self):
        self.assertEqual(clean_queries("not a JSON list"), [])


if __name__ == "__main__":
    unittest.main()
