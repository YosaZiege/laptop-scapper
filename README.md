# Laptop deals (France + Morocco)

Compare **new, in-stock** laptops from LDLC (France), UltraPC and Techspace (Morocco) against a budget in Moroccan dirhams. French purchases are assumed to be made in France and carried back by a family member. The EUR/MAD conversion is an estimate, not your bank's card rate; no shipping, tax refund, or import charges are assumed.

```bash
cd /home/yosa/Projects/solo/laptop-deals
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python laptop_deals.py --budget 8000 --top 15 --csv offers.csv
```

The scraper visits category pages and (for LDLC) product pages to read the retailer's published specifications; Techspace uses its public Shopify catalog. It uses a live EUR/MAD indicative rate from open.er-api.com. If that service is unavailable, pass a rate explicitly with `--eur-mad 10.8`; it will **not** silently use a stale rate. `--pages 20` (default) covers the currently observed category pagination; increase it if the stores add more pages. `--max-details 200` caps LDLC detail requests; a warning is shown if the cap is hit. `--csv` writes all eligible offers, not just the displayed top results.

Ranking favors a verified modern processor, 16 GB RAM, and 512 GB SSD. A missing specification is displayed as unknown rather than guessed. Chromebook, refurbished/open-box, unavailable and over-budget listings are excluded. Confirm the displayed price, exact configuration, stock and French pickup/delivery options on the retailer's page before buying. Listings and exchange rates change frequently.

Retailers can temporarily block or rate-limit automated requests. The program prints a warning for each inaccessible source and never treats a failed request as a valid empty market. Retry later or reduce `--pages`/`--max-details` to make fewer requests.

```bash
.venv/bin/python -m unittest discover -s tests
```
