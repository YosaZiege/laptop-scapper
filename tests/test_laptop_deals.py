import json
import unittest
from decimal import Decimal

from bs4 import BeautifulSoup

from laptop_deals import Offer, eligible, enrich_ldlc, listings_ldlc, listings_ultrapc, product_description, score, specs


class ScraperTests(unittest.TestCase):
    def test_ldlc_only_in_stock_offers(self):
        products = [{"item": {"name": "Lenovo IdeaPad", "url": "/fiche/1.html",
                              "offers": {"price": "699.99", "priceCurrency": "EUR",
                                         "availability": "https://schema.org/InStock"}}},
                    {"item": {"name": "Not available", "url": "/fiche/2.html",
                              "offers": {"price": "199", "priceCurrency": "EUR",
                                         "availability": "https://schema.org/OutOfStock"}}}]
        html = '<script type="application/ld+json">' + json.dumps({"@type": "ItemList", "itemListElement": products}) + '</script>'
        offers = list(listings_ldlc(BeautifulSoup(html, "html.parser")))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("699.99"))

    def test_ultrapc_sale_price_not_struck_through_price(self):
        html = '''<article class="product-miniature"><h3 class="product-title"><a href="/laptop.html">Short</a></h3>
        <img itemprop="image" alt="Lenovo Ryzen 5 7530U/16GB DDR4/512GB SSD PC Portables Multimédia">
        <span itemprop="price" content="6990">6 990 MAD</span><span class="regular-price">8 990 MAD</span>
        <div class="product-availability">Produit en stock</div></article>'''
        offer = specs(next(listings_ultrapc(BeautifulSoup(html, "html.parser"))))
        self.assertEqual(offer.price, Decimal(6990))
        self.assertEqual((offer.ram_gb, offer.ssd_gb), (16, 512))

    def test_exchange_budget_and_unknown_specs(self):
        french = Offer("LDLC", "France", "Lenovo laptop", Decimal("750"), "EUR", "https://example.com")
        self.assertFalse(eligible(french, Decimal(8000), Decimal("10.8")))
        self.assertTrue(eligible(french, Decimal(8000), Decimal("10")))
        self.assertEqual(score(french), 0)

    def test_old_cpu_and_bundles_do_not_outscore_newer_base_laptop(self):
        older = specs(Offer("LDLC", "France", "Blackview Ryzen 3 3300U 16GB RAM 512GB SSD", Decimal(600), "EUR", "https://example.com/old"))
        newer = specs(Offer("LDLC", "France", "HP i3-1315U 16GB RAM 512GB SSD", Decimal(650), "EUR", "https://example.com/new"))
        bundle = specs(Offer("LDLC", "France", "HP i3-1315U 16GB RAM 512GB SSD + mouse", Decimal(680), "EUR", "https://example.com/bundle"))
        self.assertGreater(score(newer), score(older))
        self.assertGreater(score(newer), score(bundle))

    def test_product_requires_new_and_in_stock(self):
        def detail(condition):
            return BeautifulSoup('<script type="application/ld+json">' + json.dumps({"@type": "Product", "description": "16 Go RAM SSD 512 Go", "offers": {"price": "599", "availability": "https://schema.org/InStock", "itemCondition": condition}}) + '</script>', "html.parser")
        self.assertIsNotNone(product_description(detail("https://schema.org/NewCondition")))
        self.assertIsNone(product_description(detail("https://schema.org/RefurbishedCondition")))

    def test_redirected_product_page_is_not_reported_as_original_offer(self):
        from unittest.mock import Mock
        response = Mock()
        response.text = '<script type="application/ld+json">' + json.dumps({
            "@type": "Product", "name": "Different laptop", "description": "16 Go RAM 512 Go SSD",
            "offers": {"price": "599", "availability": "https://schema.org/InStock",
                       "itemCondition": "https://schema.org/NewCondition",
                       "url": "https://www.ldlc.com/fiche/another.html"}}) + '</script>'
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        original = Offer("LDLC", "France", "Original laptop", Decimal(0), "EUR",
                         "https://www.ldlc.com/fiche/original.html")
        errors = []
        self.assertEqual(enrich_ldlc(session, [original], 1, errors), [])
        self.assertIn("no longer resolves", errors[0])


if __name__ == "__main__":
    unittest.main()
