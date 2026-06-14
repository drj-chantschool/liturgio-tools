# liturgio-tools

SQL schema and Python tooling for the `liturgio` MySQL database, which
backs chant assignments, liturgical-day calculations, and Mass/Office
proper texts.

## Contents

- `sql/` — DDL scripts that create the `liturgio` schema, plus
  `liturgio.png` (ER diagram). Run in the order below.
- `liturgio_tools/` — Python package:
  - `cli.py` — CLI for liturgical-day lookups, chant search, English GABC
    adaptations, and part assignments (`lookup-day`, `search-chant`,
    `get-chant`, `save-english`, `merge-groups`, `assign`)
  - `fix_missing_scores.py` — recover/regenerate `.gabc` files for chants
    missing scores in `gregobase_chants`
  - `upload_english_chants.py` — bulk-load English GABC adaptations into
    `local_chants`
  - `gregobase.py` — `load_chants(query)`: query `gregobase_chants` and
    yield [`gabc-tools`](https://github.com/drj-chantschool/gabc-tools)
    `GBChant` objects
  - `gregobase_sync/` — sync GregoBase data into `gregobase_chants` /
    `sync_state`
  - `translations/` — scrape ICEL Mass-proper translations from
    liturgies.net and load them into `lit_part_texts` (see
    `translations/HANDOFF.md`)
- `examples/melodic_similarity.py` — load chants via
  `liturgio_tools.gregobase.load_chants()`, run
  `gabc_tools.pipeline.MelodicPipeline`, and visualize melodic clusters with
  Bokeh
- `scripts/archive/` — historical one-off scripts kept for reference, not
  part of the maintained CLI

## Install

    pip install -e .
    pip install -e ".[viz]"   # adds bokeh, for examples/melodic_similarity.py

Depends on [`gabc-tools`](https://github.com/drj-chantschool/gabc-tools)
for GABC parsing (`GBChant`, `extract_gabc_body`, `parse_gabc_header`,
`build_gabc_file`).

## Database connection

Credentials come from the OS keyring (service `liturgio-mysql`):
read-only user `liturgio_ro`, read-write user `jcost`. The first
connection prompts for a password and caches it in the keyring; if a
stored password is rejected, it's deleted and you're re-prompted.

## Database setup

Run the SQL files in `sql/` in this order:

1. `make_param_tables.sql` — creates parameter and lookup tables used by
   later scripts.
2. `make-liturgical-day-name-overrides.sql` — creates and seeds
   `p_liturgical_day_slug_overrides`.
3. `make-liturgical-day.sql` — creates and populates `liturgical_day`.
4. `insert_dates_for_easter.sql` — seeds `dates_for_easter`.
5. `make-proper-of-seasons.sql` — creates and populates
   `proper_of_seasons` using `liturgical_day` and `dates_for_easter`.
6. `make-slot-candidate.sql` — creates chant and assignment tables plus the
   `v_chant_item` view.
7. `query-daily-mass-parts.sql` — run this after setup to query the parts
   for a given day's Mass or Office.

Prerequisites not created in this repo:

- `liturgical_subseasons` must already exist before running
  `make-liturgical-day.sql`.
- `dates_for_easter` must already exist before running
  `insert_dates_for_easter.sql`; that file only inserts rows.
- `gregobase_chants` must already exist before running
  `make-slot-candidate.sql`, because that script creates a foreign key to
  it and uses it in `v_chant_item`.

## CLI usage

    python -m liturgio_tools.cli lookup-day --date YYYY-MM-DD [--jurisdiction UNIVERSAL]
    python -m liturgio_tools.cli search-chant --incipit TEXT --part PART_CODE
    python -m liturgio_tools.cli get-chant --chant-group-id N [--gregobase-id N]
    python -m liturgio_tools.cli save-english --chant-group-id N --gabc-file PATH --source CODE [--source-citation TEXT] [--is-exact 0|1] [--notes TEXT] [--derived-from UID]
    python -m liturgio_tools.cli merge-groups --keep N --merge N [--force]
    python -m liturgio_tools.cli assign --part-code CODE --season SEASON --subseason SUBSEASON --wknum N --chant-group-id N --authority CODE [--jurisdiction UNIVERSAL] [--wkday 1-7] [--seq N] [--cycle-sun 0|1|2] [--cycle-wk 0|1] [--notes TEXT]

(or, after `pip install -e .`: `liturgio-tools lookup-day ...`)

## Translations pipeline

Scrape ICEL Mass-proper translations from liturgies.net into JSON, then
load the JSON into `lit_part_texts`:

1. `translations/tools/parse_propers.py`, `parse_easter.py`,
   `parse_saints.py` → write `*_propers.json` (gitignored, regenerable —
   network scrape)
2. `translations/load_propers.py`, `load_lit_part_texts.py`,
   `load_saints.py` → load the JSON into `lit_part_texts`

See `translations/HANDOFF.md` for the current status of this pipeline.
