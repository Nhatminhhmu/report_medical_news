# Medical News Intelligence source parsers

Copy these files into `parsers/`.

Google Sheets parser values:
- himss
- modern_healthcare
- healthleaders
- hfma
- mobihealthnews
- fierce_healthcare
- healthcare_dive
- medcitynews
- healthit_gov
- fda_digital_health

Each module exposes `collect(source)` and returns:
source, title, url, published_at, excerpt.

These parsers use JSON-LD first and source-specific article URL filters,
then HTML anchor fallback. They should be smoke-tested before activating
all sources in production.
