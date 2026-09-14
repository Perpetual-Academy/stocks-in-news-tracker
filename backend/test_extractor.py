import unittest

from backend.extractor import COMPANIES, extract_articles, mentions


class ExtractorTests(unittest.TestCase):
    def test_multiple_articles_and_opposing_stocks(self):
        result = extract_articles("Infosys revenue grew 12%, while State Bank of India earnings fell 8%.\n---\nTCS announces a board meeting.")
        self.assertEqual(result["article_count"], 2)
        self.assertEqual({row["symbol"]: row["influence"] for row in result["stocks"]},
                         {"INFY": "positive", "SBIN": "negative", "TCS": "neutral"})

    def test_deduplicates_within_article_only(self):
        rows = extract_articles("Infosys revenue grew. INFY wins a contract.\n---\nInfosys earnings fell.")["stocks"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])

    def test_mixed_negated_and_uncertain_news(self):
        for article in ["Infosys revenue grew but Infosys profit fell.",
                        "State Bank of India denies fraud.", "TCS may win a contract.",
                        "Infosys and TCS report revenue growth."]:
            self.assertTrue(all(row["influence"] == "neutral" for row in extract_articles(article)["stocks"]))

    def test_symbols_and_unknown_articles(self):
        result = extract_articles("NSE: 20MICRONS wins a contract.\n---\nWeather is sunny.")
        self.assertEqual(result["stocks"][0]["symbol"], "20MICRONS")
        self.assertEqual(result["unmatched_articles"], [2])

    def test_csv_universe(self):
        self.assertGreater(len(COMPANIES), 2000)
        self.assertEqual(mentions("20 Microns Limited revenue grew.")["20MICRONS"], "20 Microns Limited")
        self.assertIn("20MICRONS", mentions("20 Microns revenue grew."))
        self.assertEqual(mentions("Apple and Microsoft. NASDAQ: AAPL. $XYZQ."), {})
        self.assertIn("360ONE", mentions("360ONE wins a contract."))
        for symbol in COMPANIES:
            self.assertIn(symbol, mentions(symbol))

    def test_paragraphs_and_word_boundaries(self):
        result = extract_articles("Infosys announces a meeting.\n\nThe meeting is tomorrow.")
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(extract_articles("Titanium prices rose.")["stocks"], [])


if __name__ == "__main__":
    unittest.main()
