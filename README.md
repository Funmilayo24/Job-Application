# UK Sponsorship Job Agent

A local, human-in-the-loop job-search assistant for Oluwatosin. It searches twice
daily for UK vacancies related to:

- customer success, customer operations, service delivery and account management;
- product ownership, business analysis, project coordination and Agile delivery.

Search titles and common title variants are configured in
[`app/search_config.json`](app/search_config.json). They are semantic search seeds,
not an exact-match allowlist, so differently titled jobs remain eligible when their
responsibilities match the candidate profile.

The system stores results in PostgreSQL, checks sponsorship wording, cross-references
the official UK Skilled Worker sponsor register, applies a conservative salary-rule
gate, and emails new vacancies in two clearly labelled tiers.

Discovery combines dedicated, capacity-reserved searches of official NHS Jobs and
externally advertised Civil Service Jobs with separate general web searches for each
career track and structured, paginated Adzuna and Reed searches. Results are
source-labelled, deduplicated across job boards and employer sites, and reviewed
against the same CV and sponsorship rules.

It does **not** scrape logged-in LinkedIn or Indeed accounts, submit applications, or
invent CV facts. A person must review each vacancy and every generated document before
applying.

## Web dashboard

The local SponsorPath dashboard provides:

- searchable and filterable job cards with fit and sponsorship status;
- full vacancy analysis and recruiter-confirmation wording;
- application progress tracking;
- one-click generation of a truthful tailored CV, cover letter and review notes;
- downloads for every generated document version.

Build and start the database, scheduled worker and dashboard:

```powershell
docker compose up -d --build
```

Open [http://localhost:8000](http://localhost:8000). The dashboard is bound to the
local computer only by default. Set `WEB_PORT` if port 8000 is already in use.

## Qualification policy

A vacancy is included in the **confirmed sponsorship** section only when:

1. the vacancy itself explicitly offers Skilled Worker/CoS sponsorship;
2. the employer matches the current GOV.UK licensed-sponsor register;
3. the advertised salary can be confirmed as meeting the current applicable rule;
4. the CV fit score meets the configured threshold (65 by default); and
5. it has not already been emailed.

A vacancy can appear in **sponsorship possible — confirm before applying** when the
employer holds a Skilled Worker licence, the role is a strong CV match, no exclusion
wording is present, and the salary is viable or not stated, but the advert is silent
about sponsorship. This section is capped at 15 jobs per digest. An employer licence
does not mean that every vacancy will be sponsored, so the candidate should ask the
recruiter before tailoring or applying.

Vacancies saying that sponsorship is unavailable, or requiring an unrestricted
existing right to work, are rejected. Other ambiguous jobs remain in PostgreSQL with
a `review_*` status. This is a search aid, not immigration advice.

## Local setup

Requirements:

- Docker Desktop with Docker Compose;
- an OpenAI API key;
- a Resend API key and verified sender/domain.

The root `.env` must contain:

```dotenv
OPENAI_API_KEY=...
RESEND_API_KEY=...
EMAIL_FROM=Jobs Agent <jobs@your-verified-domain.example>
EMAIL_TO=first-recipient@example.com,second-recipient@example.com
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...
REED_API_KEY=...
```

`EMAIL_TO` accepts one address or multiple comma-separated addresses. Every job digest
will be sent to all configured recipients.

Optional configuration is documented in [.env.example](.env.example). The default
schedule is 08:00 and 18:00 in `Europe/London`.

Build and start PostgreSQL:

```powershell
docker compose up -d db
```

PostgreSQL is exposed to the host on port `5434` by default because ports 5432 and
5433 may already be in use. Containers continue to use `db:5432`. Override the host
port by setting `POSTGRES_PORT` in `.env`.

Build the worker:

```powershell
docker compose build worker
```

Refresh the official sponsor register:

```powershell
docker compose run --rm worker job-agent refresh-sponsors
```

Run one paid search manually:

```powershell
docker compose run --rm worker job-agent run
```

Start the twice-daily worker:

```powershell
docker compose up -d worker
```

Send currently queued jobs without running OpenAI or any job-site searches:

```powershell
docker compose run --rm worker job-agent send-pending
```

View worker logs:

```powershell
docker compose logs -f worker
```

Stop the application without deleting PostgreSQL data:

```powershell
docker compose down
```

## Reviewing vacancies

List confirmed sponsorship jobs:

```powershell
docker compose run --rm worker job-agent list --status qualified_confirmed
```

List licensed-employer jobs that need sponsorship confirmation:

```powershell
docker compose run --rm worker job-agent list --status qualified_possible
```

List recent jobs of every status:

```powershell
docker compose run --rm worker job-agent list --limit 50
```

Important database statuses include:

- `qualified_confirmed`
- `qualified_possible`
- `review_salary_unclear`
- `review_sponsor_not_matched`
- `rejected_sponsorship_excluded`
- `rejected_salary`
- `rejected_low_fit`

## Tailoring an application

After reviewing a stored vacancy, generate a Word CV, Word cover letter and review
notes:

```powershell
docker compose run --rm worker job-agent tailor JOB_ID
```

Documents are written under `artifacts/JOB_ID-employer-role/`. The candidate truth
profile lives in [app/candidate_profile.json](app/candidate_profile.json). Its metrics
and dates reflect the confirmed source facts:

- credit-card eligibility processing improved by 85%;
- Learning and Development ended in January 2021;
- escalation volumes reduced by approximately 70%.

The generated files intentionally omit personal contact details. Preserve the contact
header from the original CV when preparing the final submission.

The dashboard supports two private accounts through `DASHBOARD_USER_1`,
`DASHBOARD_PASSWORD_1`, `DASHBOARD_USER_2` and `DASHBOARD_PASSWORD_2`. Configure a
strong `DASHBOARD_SESSION_SECRET` and set `DASHBOARD_SECURE_COOKIES=true` before
exposing the web service.

The dashboard's **Search now** action places a request in PostgreSQL for the worker
to process. It is limited server-side to one manual request every 24 hours, requires
at least six hours since the latest search, and cannot overlap another run. Scheduled
08:00 and 18:00 searches continue independently.

Before a vacancy is emailed, the agent parses its closing date and checks the public
link while following redirects. Past dates, HTTP `404`/`410` responses and pages that
explicitly say the vacancy is closed are excluded. Access blocks, rate limits and
timeouts are labelled **unverified** rather than incorrectly rejected. The same check
runs again before paid CV tailoring.

## Cost controls

- Discovery uses GPT-5.6 Luna.
- Customer/operations and product/delivery are searched separately at medium web depth.
- Each web pass explicitly checks NHS Jobs and external Civil Service Jobs listings
  in independent, site-restricted searches without treating public-sector affiliation
  as proof of sponsorship.
- Strong public-sector matches that need sponsor-register or salary review appear in
  a separate daily-email section instead of being silently hidden.
- Six focused Adzuna queries run per search and remain within its default personal-use
  request limits at the twice-daily schedule.
- Six direct-employer Reed queries run per search using the Jobseeker API.
- Tailoring uses GPT-5.6 Terra only when manually requested.
- URL canonicalisation prevents repeat analysis and email.
- Search runs twice daily rather than every three hours.
- `MAX_DISCOVERED_JOBS` caps each run.
- Set `DRY_RUN_EMAIL=true` to suppress email while testing.
- Set `RUN_ON_STARTUP=true` only if a paid search should run whenever the worker starts.

Use the OpenAI Platform billing page to set usage notifications or limits and review
actual spend after the first week.

## Railway deployment

Create one Railway project containing PostgreSQL and two services connected to this
GitHub repository:

1. `web`: custom start command
   `sh -c 'uvicorn app.web:app --host 0.0.0.0 --port $PORT'`, healthcheck `/health`,
   a Railway-provided domain, and a volume mounted at `/app/artifacts`. Set
   `RAILWAY_RUN_UID=0` because Railway mounts volumes as root.
2. `worker`: custom start command `python -m app.worker`, no public domain and
   Serverless disabled.

Both services must reference the Railway PostgreSQL `DATABASE_URL`. Put the API,
email, search and dashboard values from `.env.example` into Railway variables rather
than uploading `.env`. The web service needs the database, OpenAI and dashboard
variables; the worker needs the database, OpenAI, Resend, Adzuna, Reed, email and
schedule variables.

Use two distinct dashboard usernames and passwords, a long random session secret, and
`DASHBOARD_SECURE_COOKIES=true`. The Railway domain is sufficient; a custom domain is
not required.
