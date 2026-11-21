-- =====================================================================
-- 00_schemas.sql  —  layers + helper functions
--   raw   : files as published, all TEXT (created by pipeline/load_raw.py)
--   ref   : hand-curated mappings (seeds/*.csv)  <-- the manual work lives here
--   core  : typed, cleaned, one row per real-world entity/event
--   mart  : analysis-ready cubes (company x product x indication x place)
--   qa    : data-quality checks
--   geo   : optional PostGIS polygons
-- Year being processed comes from:  SET app.year = '2024';
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS qa;
CREATE SCHEMA IF NOT EXISTS geo;

-- Safe numeric cast: '' / '*' / garbage -> NULL (CMS uses blanks for suppression)
CREATE OR REPLACE FUNCTION core.num(t text) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN t ~ '^\s*-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?\s*$'
              THEN trim(t)::numeric END
$$;

-- Normalise brand / product names for matching: upper, collapse spaces
CREATE OR REPLACE FUNCTION core.norm_name(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT NULLIF(upper(regexp_replace(trim(coalesce(t,'')), '\s+', ' ', 'g')), '')
$$;

-- NDC -> 9-digit labeler+product code (5-4), the level shared by FDA,
-- Open Payments and claims. Hyphenated input is unambiguous; an unhyphenated
-- 11-digit code is already 5-4-2; 10-digit unhyphenated is ambiguous -> NULL.
CREATE OR REPLACE FUNCTION core.ndc9(t text) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE p text[]; d text;
BEGIN
  IF t IS NULL OR trim(t) = '' THEN RETURN NULL; END IF;
  IF position('-' in t) > 0 THEN
    p := string_to_array(trim(t), '-');
    RETURN lpad(p[1], 5, '0') || lpad(p[2], 4, '0');
  END IF;
  d := regexp_replace(t, '\D', '', 'g');
  IF length(d) = 11 THEN RETURN left(d, 9); END IF;
  IF length(d) = 9  THEN RETURN d; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION core.cur_year() RETURNS int
LANGUAGE sql STABLE AS $$ SELECT current_setting('app.year')::int $$;
