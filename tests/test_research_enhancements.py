import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_fetch import should_render_candidate
from research_enhancements import extract_html
from retrieval_cache import load_page, store_page


class ResearchEnhancementsTest(unittest.TestCase):
    def test_rich_html_extraction_keeps_structured_data_tables_and_visible_text(self):
        html = b'''<html><head>
        <script type="application/ld+json">{
          "@context":"https://schema.org",
          "@type":"Product",
          "name":"Example Inverter",
          "model":"INV-3000",
          "brand":{"@type":"Brand","name":"ExampleCo"},
          "offers":{"@type":"Offer","price":"599.00","priceCurrency":"GBP"},
          "dateModified":"2026-09-01"
        }</script>
        <meta name="description" content="A useful technical product page">
        </head><body>
        <h1>Example Inverter</h1>
        <table><tr><th>Power</th><th>Voltage</th></tr><tr><td>3000 W</td><td>12 V</td></tr></table>
        <main>This inverter includes a detailed specification and installation guide for system designers.</main>
        </body></html>'''

        text, published = extract_html(html)

        self.assertIn("Structured page data", text)
        self.assertIn("model=INV-3000", text)
        self.assertIn("price=599.00", text)
        self.assertIn("Power | Voltage", text)
        self.assertIn("installation guide", text)
        self.assertEqual(published, "2026-09-01")

    def test_retrieval_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            store_page("https://example.com/item", {
                "text": "Useful cached evidence",
                "url": "https://example.com/item",
                "content_type": "text/html",
                "published_at": "2026-09-01",
            }, path=path)

            cached = load_page("https://example.com/item", path=path)

            self.assertEqual(cached["text"], "Useful cached evidence")
            self.assertEqual(cached["cache_status"], "fresh")

    @patch("browser_fetch.search.public_url", return_value=True)
    def test_browser_fallback_targets_search_snippet_when_full_page_failed(self, _public_url):
        candidate = {
            "title": "Example technical product",
            "url": "https://example.com/product",
            "snippet": "Example technical product with detailed specification information.",
            "query": "example technical product specification",
            "rank": 1,
        }
        page = {
            "text": candidate["snippet"],
            "content_type": "text/search-snippet",
        }

        self.assertTrue(should_render_candidate(candidate, page))

    @patch("browser_fetch.search.public_url", return_value=True)
    def test_browser_fallback_skips_good_readable_html(self, _public_url):
        candidate = {
            "title": "Example technical product",
            "url": "https://example.com/product",
            "snippet": "Example technical product with detailed specification information.",
            "query": "example technical product specification",
            "rank": 1,
        }
        page = {
            "text": ("Example technical product specification details and useful evidence. " * 20),
            "content_type": "text/html",
        }

        self.assertFalse(should_render_candidate(candidate, page))


if __name__ == "__main__":
    unittest.main()
