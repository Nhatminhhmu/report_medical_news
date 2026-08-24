# Medical News Intelligence — source parsers v1.1

FIXED:
- Removed the external `python-dateutil` dependency.
- Date parsing now uses only Python standard library (`email.utils` + `datetime`).
- Includes 10 source-specific parsers.
- Each parser exposes `collect(source)`.

Parser names:
himss
modern_healthcare
healthleaders
hfma
mobihealthnews
fierce_healthcare
healthcare_dive
medcitynews
healthit_gov
fda_digital_health

Important:
Fierce Healthcare is currently running as generic WEB in the user's
Google Sheet. To use the custom parser, set its `parser` field to:
fierce_healthcare
