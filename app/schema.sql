CREATE TABLE IF NOT EXISTS sponsors (
    id BIGSERIAL PRIMARY KEY,
    organisation_name TEXT NOT NULL,
    normalised_name TEXT NOT NULL,
    town_city TEXT,
    county TEXT,
    type_and_rating TEXT,
    route TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sponsors_normalised_name_idx
    ON sponsors (normalised_name);

CREATE TABLE IF NOT EXISTS search_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    discovered_count INTEGER NOT NULL DEFAULT 0,
    qualified_count INTEGER NOT NULL DEFAULT 0,
    emailed_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS manual_search_requests (
    id BIGSERIAL PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS manual_search_requests_status_idx
    ON manual_search_requests (status, requested_at);

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    employer TEXT NOT NULL,
    location TEXT,
    salary_text TEXT,
    source_name TEXT,
    vacancy_url TEXT NOT NULL,
    posted_at TEXT,
    closing_at TEXT,
    summary TEXT,
    career_track TEXT,
    fit_score INTEGER NOT NULL DEFAULT 0 CHECK (fit_score BETWEEN 0 AND 100),
    fit_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    sponsorship_claim TEXT,
    sponsorship_evidence TEXT,
    sponsorship_evidence_url TEXT,
    explicit_sponsorship BOOLEAN NOT NULL DEFAULT false,
    sponsorship_exclusion BOOLEAN NOT NULL DEFAULT false,
    sponsorship_exclusion_evidence TEXT,
    sponsorship_tier TEXT NOT NULL DEFAULT 'rejected',
    sponsor_register_match BOOLEAN NOT NULL DEFAULT false,
    sponsor_register_name TEXT,
    sponsor_match_score INTEGER,
    salary_rule_status TEXT NOT NULL DEFAULT 'unclear',
    salary_rule_reason TEXT,
    qualification_status TEXT NOT NULL DEFAULT 'unqualified',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    emailed_at TIMESTAMPTZ,
    application_status TEXT NOT NULL DEFAULT 'discovered',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_run_id BIGINT REFERENCES search_runs(id)
);

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS sponsorship_exclusion BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS sponsorship_exclusion_evidence TEXT;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS sponsorship_tier TEXT NOT NULL DEFAULT 'rejected';

CREATE INDEX IF NOT EXISTS jobs_qualification_status_idx
    ON jobs (qualification_status);
CREATE INDEX IF NOT EXISTS jobs_emailed_at_idx
    ON jobs (emailed_at);
CREATE INDEX IF NOT EXISTS jobs_application_status_idx
    ON jobs (application_status);

CREATE TABLE IF NOT EXISTS tailored_documents (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    cv_path TEXT,
    cover_letter_path TEXT,
    review_notes_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model TEXT NOT NULL
);

ALTER TABLE tailored_documents
    ADD COLUMN IF NOT EXISTS review_notes_path TEXT;
