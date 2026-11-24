-- =====================================================================
-- 10_ref.sql  —  curated reference tables (filled from seeds/*.csv)
-- These encode the judgement calls no public file makes for you:
-- which brand strings are one product, who sells it, which specialty
-- implies which indication, and which disease measure is its denominator.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ref.drug_class (
  class_id     text PRIMARY KEY,           -- e.g. 'T2_BIOLOGIC'
  class_name   text NOT NULL,
  description  text
);

CREATE TABLE IF NOT EXISTS ref.product (
  product_id          text PRIMARY KEY,    -- e.g. 'DUPIXENT'
  brand_name          text NOT NULL,
  generic_name        text NOT NULL,
  company             text NOT NULL,       -- marketing parent, e.g. 'Sanofi/Regeneron'
  class_id            text NOT NULL REFERENCES ref.drug_class,
  benefit             text NOT NULL CHECK (benefit IN ('D','B','D/B')),  -- Part D, Part B, both
  default_indication  text NOT NULL,       -- used when specialty gives no signal
  notes               text
);

-- Every string under which a product appears in a source.
--   source: partd_brand (Part D Brnd_Name), partb_hcpcs (HCPCS_Cd),
--           op_name (Open Payments Name_of_Drug_or_Biological_..._N)
--   match : exact | prefix  (prefix catches 'LANTUS SOLOSTAR', 'TOUJEO MAX SOLOSTAR'...)
CREATE TABLE IF NOT EXISTS ref.product_alias (
  product_id  text NOT NULL REFERENCES ref.product ON DELETE CASCADE,
  source      text NOT NULL CHECK (source IN ('partd_brand','partb_hcpcs','op_name')),
  alias       text NOT NULL,               -- stored normalised (upper)
  match       text NOT NULL DEFAULT 'exact' CHECK (match IN ('exact','prefix')),
  PRIMARY KEY (source, alias)
);

-- Specialty -> indication allocation. prscrbr_type is the CMS specialty
-- string (Part D Prscrbr_Type / Part B Rndrng_Prvdr_Type). '*' = fallback.
-- weight lets one specialty split across indications (weights per
-- product x specialty should sum to 1).
CREATE TABLE IF NOT EXISTS ref.specialty_indication (
  product_id    text NOT NULL REFERENCES ref.product ON DELETE CASCADE,
  prscrbr_type  text NOT NULL,
  indication    text NOT NULL,
  weight        numeric NOT NULL DEFAULT 1 CHECK (weight > 0 AND weight <= 1),
  PRIMARY KEY (product_id, prscrbr_type, indication)
);

-- Indication -> CDC PLACES MeasureId for the disease-burden denominator.
-- NULL measure => fall back to 65+ population.
CREATE TABLE IF NOT EXISTS ref.indication_measure (
  indication         text PRIMARY KEY,
  places_measure_id  text,
  note               text
);

-- Legal entity -> parent company, as of the data year. Open Payments names
-- the paying entity ('E.R. Squibb & Sons', 'Genzyme'); Part D Spending by Drug
-- gives a 15-char truncated manufacturer ('BMS PRIMARYCARE'). pattern is a
-- POSIX regex matched against core.norm_name(name); lowest priority wins.
-- Unmatched names are their own parent (see qa.v_unmapped_companies).
CREATE TABLE IF NOT EXISTS ref.company_parent (
  pattern   text PRIMARY KEY,
  parent    text NOT NULL,
  priority  int  NOT NULL DEFAULT 100,
  note      text
);
