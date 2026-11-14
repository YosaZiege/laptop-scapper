"""Compare current new laptop offers in France and Morocco."""

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


LDLC = "https://www.ldlc.com/informatique/ordinateur-portable/pc-portable/c4265/?sort=1"
ULTRAPC = "https://www.ultrapc.ma/19-pc-portables"
TECHSPACE = "https://www.techspace.ma/collections/pc-portable/products.json"
RATE_URL = "https://open.er-api.com/v6/latest/EUR"
HEADERS = {"User-Agent": "LaptopDeals/1.0 (personal price comparison; respectful rate limits)"}
BAD_NAMES = re.compile(r"chromebook|reconditionn|refurbish|occasion|seconde main|open.box|remis[e]?\s+[àa]\s+neuf|renewed", re.I)
CPU = re.compile(r"(?:ryzen\s*(?:ai\s*)?\d(?:\s*pro)?\s*\d{3,5}[a-z]{0,3}|(?:intel\s*)?(?:core\s*ultra\s*\d|core\s*\d|i[3579])[-\s]*\d{3,5}[a-z]{0,3}|snapdragon\s*x\s*(?:plus|elite)?)", re.I)
RAM = re.compile(r"(?<!\d)(\d{1,2})\s*(?:go|gb)\s*(?:de\s*)?(?:ram|ddr[345]|m[ée]moire\s*vive|ssd)|(?:ram|ddr[345]|m[ée]moire\s*vive)\s*(\d{1,2})\s*(?:go|gb)", re.I)
SSD = re.compile(r"(?:ssd\s*(\d{3,4})\s*(?:go|gb)|(?:ssd\s*)?([12])\s*(?:to|tb)\s*(?:ssd)?|(?<!\d)(\d{3,4})\s*(?:go|gb)\s*ssd)", re.I)


@dataclass(frozen=True)
class Offer:
    retailer: str
    country: str
    name: str
    price: Decimal
    currency: str
    url: str
    description: str = ""
    cpu: str = "unknown"
    ram_gb: int | None = None
    ssd_gb: int | None = None

    def mad(self, rate):
        return self.price * (rate if self.currency == "EUR" else Decimal(1))


def specs(offer):
    text = offer.name + " " + offer.description
    cpu = CPU.search(text)
    ram = RAM.search(text)
    ssd = SSD.search(text)
    storage = None
    if ssd:
        storage = int(ssd.group(1) or ssd.group(3) or int(ssd.group(2)) * 1024)
    return replace(offer, cpu=cpu.group(0).strip() if cpu else "unknown",
                   ram_gb=int(ram.group(1) or ram.group(2)) if ram else None,
                   ssd_gb=storage)


def eligible(offer, budget, rate):
    return (offer.price > 0 and offer.mad(rate) <= budget and
            not BAD_NAMES.search(offer.name + " " + offer.description))


def score(offer):
    """Simple, explainable balanced-use heuristic; unknown specs earn no credit."""
    cpu = offer.cpu.lower()
    cpu_points = 0
    if cpu != "unknown":
        cpu_points = 2
        if re.search(r"(?:ryzen\s*(?:ai\s*)?[57]|core\s*(?:ultra\s*)?[57]|i[57]|snapdragon\s*x)", cpu):
            cpu_points = 4
        if re.search(r"(?:ryzen\s*(?:ai\s*)?[79]|core\s*(?:ultra\s*)?[79]|i[79])", cpu):
            cpu_points = 5
        # A very old Ryzen 3000 U-series chip should not tie a newer Core 3.
        if re.search(r"ryzen\s*\d\s*3\d{3}u", cpu):
            cpu_points = max(0, cpu_points - 2)
    return (cpu_points + (4 if (offer.ram_gb or 0) >= 16 else 1 if (offer.ram_gb or 0) >= 8 else 0)
            + (3 if (offer.ssd_gb or 0) >= 512 else 1 if (offer.ssd_gb or 0) >= 256 else 0)
            - (1 if " + " in offer.name else 0))


def fetch(session, url):
    response = session.get(url, timeout=25)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def listings_ldlc(soup):
    parsed = set()
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        if data.get("@type") != "ItemList":
            continue
        for entry in data.get("itemListElement", []):
            product = entry.get("item", {})
            offer = product.get("offers", {})
            if product.get("url"):
                parsed.add(urljoin(LDLC, product["url"]))
            if (offer.get("availability", "").split("/")[-1] != "InStock" or
                    offer.get("priceCurrency") != "EUR"):
                continue
            try:
                result = Offer("LDLC", "France", product["name"].strip(),
                               Decimal(str(offer["price"])), "EUR", urljoin(LDLC, product["url"]))
                parsed.add(result.url)
                yield result
            except (KeyError, InvalidOperation):
                continue
    # Later category pages omit the ItemList and hydrate prices in the browser.
    # Carry a zero price until the product's structured data supplies the real one.
    for card in soup.select(".pdt-item"):
        link = card.select_one("h3 a[href]")
        if not link:
            continue
        stock = card.select_one(".stock-title")
        if stock and "dispo" not in stock.get_text(" ", strip=True).lower():
            continue
        url = urljoin(LDLC, link["href"])
        if url in parsed:
            continue
        description = card.select_one(".desc")
        yield Offer("LDLC", "France", link.get_text(" ", strip=True),
                    Decimal(0), "EUR", url,
                    description.get_text(" ", strip=True) if description else "")


def listings_ultrapc(soup):
    for card in soup.select("article.product-miniature"):
        link = card.select_one("h3.product-title a[href]")
        price = card.select_one('[itemprop="price"][content]')
        image = card.select_one('img[itemprop="image"][alt]')
        availability = card.select_one(".product-availability")
        if not (link and price and image and availability and
                "en stock" in availability.get_text(" ", strip=True).lower()):
            continue
        try:
            # Category titles are truncated; the image alt retains the configuration.
            name = image["alt"].split(" PC Portables ")[0].strip()
            yield Offer("UltraPC", "Morocco", name, Decimal(price["content"]),
                        "MAD", urljoin(ULTRAPC, link["href"]))
        except (KeyError, InvalidOperation):
            continue


def product_description(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        if data.get("@type") == "Product":
            offer = data.get("offers", {})
            if offer.get("availability", "").split("/")[-1] != "InStock":
                return None
            if offer.get("itemCondition", "").split("/")[-1] not in ("", "NewCondition"):
                return None
            return data
    return None


def rate_eur_mad(session):
    data = session.get(RATE_URL, timeout=15).json()
    if data.get("result") != "success" or "MAD" not in data.get("rates", {}):
        raise ValueError("EUR/MAD feed did not contain a valid MAD rate")
    return Decimal(str(data["rates"]["MAD"])), data.get("time_last_update_utc", "unknown")


def collect(session, start, parser, pages, budget, rate, errors):
    found = {}
    url = start
    for page in range(pages):
        if not url:
            break
        try:
            soup = fetch(session, url)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
            break
        cards = list(parser(soup))
        if not cards and not soup.select(".pdt-item, article.product-miniature"):
            errors.append(f"{url}: no parseable listings (layout may have changed)")
            break
        for offer in cards:
            if (eligible(offer, budget, rate) or
                    (offer.retailer == "LDLC" and offer.price == 0 and not BAD_NAMES.search(offer.name + " " + offer.description))):
                found[offer.url] = specs(offer)
        next_link = soup.select_one('a[rel="next"][href], link[rel="next"][href]')
        url = urljoin(url, next_link["href"]) if next_link else None
        if url and start == LDLC:
            url += ("&" if "?" in url else "?") + "sort=1"
        if page + 1 == pages and url:
            errors.append(f"{start}: pagination limit reached; more listings exist (raise --pages)")
        time.sleep(0.25)
    return list(found.values())


def collect_techspace(session, pages, budget, errors):
    found = {}
    for page in range(1, pages + 1):
        url = f"{TECHSPACE}?limit=250&page={page}"
        try:
            products = session.get(url, timeout=30)
            products.raise_for_status()
            products = products.json()["products"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append(f"{url}: {exc}")
            break
        for product in products:
            name = product.get("title", "")
            # This category also contains laptop accessories: require a processor.
            if not CPU.search(name) or BAD_NAMES.search(name + " " + str(product.get("tags", ""))):
                continue
            for variant in product.get("variants", []):
                if not variant.get("available"):
                    continue
                try:
                    offer = Offer("Techspace", "Morocco", name, Decimal(variant["price"]),
                                  "MAD", f"https://www.techspace.ma/products/{product['handle']}")
                    if eligible(offer, budget, Decimal(1)):
                        found[offer.url] = specs(offer)
                except (KeyError, InvalidOperation):
                    continue
        if len(products) < 250:
            break
        if page == pages:
            errors.append("Techspace: pagination limit reached; raise --pages")
        time.sleep(0.25)
    return list(found.values())


def enrich_ldlc(session, offers, limit, errors):
    # Category is sorted by increasing price; preserve this order for the detail cap.
    if len(offers) > limit:
        errors.append(f"LDLC: detail limit reached ({limit}/{len(offers)}); results are incomplete")
    result = []
    for offer in offers[:limit]:
        try:
            data = product_description(fetch(session, offer.url))
            if data is None:
                continue
            current = data.get("offers", {})
            # Some removed product pages redirect to a different product or
            # a generic listing while still returning HTTP 200.
            if (data.get("name", "").strip().casefold() != offer.name.strip().casefold() or
                    current.get("url", "").split("?")[0].rstrip("/") != offer.url.rstrip("/")):
                errors.append(f"{offer.url}: product link no longer resolves to the listed laptop")
                continue
            # Detail pages are authoritative if category prices have gone stale.
            price = Decimal(str(current["price"]))
            result.append(specs(replace(offer, price=price, description=data.get("description", ""))))
        except (requests.RequestException, KeyError, InvalidOperation) as exc:
            errors.append(f"{offer.url}: detail unavailable: {exc}")
        time.sleep(0.25)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=Decimal, default=Decimal(8000), help="budget in MAD")
    parser.add_argument("--eur-mad", type=Decimal, help="manual EUR to MAD rate (otherwise live rate)")
    parser.add_argument("--pages", type=int, default=20, help="max category pages per retailer")
    parser.add_argument("--max-details", type=int, default=200, help="max LDLC detail pages")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--csv", help="write all matching offers as CSV")
    args = parser.parse_args(argv)
    if args.budget <= 0 or args.pages < 1 or args.max_details < 1 or args.top < 1:
        parser.error("budget, pages, max-details and top must be positive")
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        rate, rate_date = (args.eur_mad, "manual") if args.eur_mad is not None else rate_eur_mad(session)
    except (requests.RequestException, ValueError, InvalidOperation) as exc:
        parser.error(f"exchange rate unavailable ({exc}); supply --eur-mad RATE")
    if rate <= 0:
        parser.error("EUR/MAD rate must be positive")
    errors = []
    french = collect(session, LDLC, listings_ldlc, args.pages, args.budget, rate, errors)
    moroccan = collect(session, ULTRAPC, listings_ultrapc, args.pages, args.budget, rate, errors)
    moroccan += collect_techspace(session, args.pages, args.budget, errors)
    french = enrich_ldlc(session, french, args.max_details, errors)
    offers = [o for o in french + moroccan if eligible(o, args.budget, rate)]
    offers.sort(key=lambda o: (-score(o), o.mad(rate), o.name))
    print(f"Checked {datetime.now(timezone.utc).isoformat(timespec='minutes')} | "
          f"budget {args.budget} MAD | 1 EUR = {rate} MAD ({rate_date})")
    print(f"Found {len(offers)} eligible offers (LDLC: {sum(o.retailer == 'LDLC' for o in offers)}, "
          f"UltraPC: {sum(o.retailer == 'UltraPC' for o in offers)}, "
          f"Techspace: {sum(o.retailer == 'Techspace' for o in offers)}). "
          "Ranked by CPU/RAM/SSD; ties by price. Prices are estimates in MAD.\n")
    for index, offer in enumerate(offers[:args.top], 1):
        print(f"{index}. [{offer.retailer}] {offer.name}\n"
              f"   {offer.price} {offer.currency} ≈ {offer.mad(rate):.0f} MAD | "
              f"CPU: {offer.cpu} | RAM: {offer.ram_gb or '?'} GB | SSD: {offer.ssd_gb or '?'} GB | "
              f"score: {score(offer)}\n   {offer.url}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(("retailer", "country", "name", "price", "currency", "estimated_mad", "cpu", "ram_gb", "ssd_gb", "score", "url"))
            for o in offers:
                writer.writerow((o.retailer, o.country, o.name, o.price, o.currency,
                                 f"{o.mad(rate):.2f}", o.cpu, o.ram_gb or "", o.ssd_gb or "", score(o), o.url))
        print(f"\nSaved {len(offers)} offers to {args.csv}")
    for error in errors:
        print(f"WARNING: {error}", file=sys.stderr)
    return 0 if offers else 1


if __name__ == "__main__":
    sys.exit(main())
