import unittest
from datetime import date
from unittest.mock import Mock, patch

from search import (best_passages, clean_queries, cosine_similarity, evidence_ledger,
                    extract_html, fetch_page, freshness_score, public_url, rank_candidates)


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

    def test_html_extraction_preserves_publication_date(self):
        text, published = extract_html(b"""<html><head><meta property="article:published_time" content="2026-08-20"></head>
            <body><nav>Navigation should disappear from this page.</nav><main>A sufficiently detailed research finding remains visible here.</main></body></html>""")
        self.assertEqual(published, "2026-08-20")
        self.assertIn("research finding", text)
        self.assertNotIn("Navigation", text)

    def test_freshness_and_cosine_helpers(self):
        self.assertGreater(freshness_score(date.today().isoformat()), freshness_score("2020-01-01"))
        self.assertAlmostEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_private_and_credentialed_urls_are_rejected(self):
        self.assertFalse(public_url("http://127.0.0.1/private"))
        self.assertFalse(public_url("https://user:password@example.com/report"))

    @patch("search.requests.get")
    @patch("search.public_url", side_effect=[True, False])
    def test_redirect_destination_is_revalidated(self, _public_url, get):
        redirect = Mock(status_code=302, headers={"location": "http://127.0.0.1/private"})
        get.return_value = redirect

        result = fetch_page("https://public.example/report")

        self.assertIn("Blocked non-public", result["error"])
        self.assertEqual(get.call_count, 1)
        redirect.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
