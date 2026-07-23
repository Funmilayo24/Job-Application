from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

JOB_PROPERTIES: dict[str, Any] = {
    "title": {"type": "string"},
    "employer": {"type": "string"},
    "location": {"type": "string"},
    "salary_text": {"type": "string"},
    "source_name": {"type": "string"},
    "vacancy_url": {"type": "string"},
    "posted_at": {"type": "string"},
    "closing_at": {"type": "string"},
    "summary": {"type": "string"},
    "career_track": {
        "type": "string",
        "enum": ["customer_success", "product_delivery", "both"],
    },
    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
    "fit_reasons": {"type": "array", "items": {"type": "string"}},
    "missing_requirements": {"type": "array", "items": {"type": "string"}},
    "sponsorship_claim": {"type": "string"},
    "sponsorship_evidence": {"type": "string"},
    "sponsorship_evidence_url": {"type": "string"},
    "explicit_sponsorship": {"type": "boolean"},
    "sponsorship_exclusion": {"type": "boolean"},
    "sponsorship_exclusion_evidence": {"type": "string"},
    "salary_rule_status": {
        "type": "string",
        "enum": ["meets", "fails", "unclear"],
    },
    "salary_rule_reason": {"type": "string"},
}

JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": JOB_PROPERTIES,
                "required": list(JOB_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["jobs"],
    "additionalProperties": False,
}

TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        "cv_title": {"type": "string"},
        "professional_summary": {"type": "string"},
        "core_skills": {"type": "array", "items": {"type": "string"}},
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "employer": {"type": "string"},
                    "role": {"type": "string"},
                    "dates": {"type": "string"},
                    "location": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["employer", "role", "dates", "location", "bullets"],
                "additionalProperties": False,
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "qualification": {"type": "string"},
                    "institution": {"type": "string"},
                    "dates": {"type": "string"},
                },
                "required": ["qualification", "institution", "dates"],
                "additionalProperties": False,
            },
        },
        "cover_letter": {"type": "string"},
        "review_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "cv_title",
        "professional_summary",
        "core_skills",
        "experience",
        "education",
        "cover_letter",
        "review_notes",
    ],
    "additionalProperties": False,
}


def load_candidate_profile() -> dict[str, Any]:
    path = Path(__file__).with_name("candidate_profile.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_search_config() -> dict[str, Any]:
    path = Path(__file__).with_name("search_config.json")
    return json.loads(path.read_text(encoding="utf-8"))


class OpenAIJobService:
    def __init__(self, api_key: str, *, search_model: str, tailor_model: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.search_model = search_model
        self.tailor_model = tailor_model
        self.profile = load_candidate_profile()
        self.search_config = load_search_config()

    def discover_jobs(self, max_jobs: int) -> list[dict[str, Any]]:
        tracks = self.search_config["career_tracks"]
        per_track = max(1, (max_jobs + len(tracks) - 1) // len(tracks))
        jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for track in tracks:
            prompt = f"""
Find up to {per_track} currently open UK vacancies matching this career track:
{json.dumps(track, ensure_ascii=False)}

Search employer career sites and public job listings. Do not automate or access a
logged-in LinkedIn or Indeed account.

SPONSORSHIP SEARCH POLICY:
- Return two kinds of strong CV matches: vacancies that explicitly offer Skilled
  Worker/CoS sponsorship, and vacancies where sponsorship is not mentioned but the
  employer may be a licensed sponsor. Local code will verify the current sponsor
  register and assign the final tier.
- When sponsorship is explicit, quote or tightly paraphrase the wording, provide the
  evidence URL, and set explicit_sponsorship=true.
- When the vacancy is silent, set explicit_sponsorship=false and leave sponsorship
  evidence fields as empty strings. Never describe silence as an offer.
- Detect wording such as "no sponsorship", "unable to sponsor", "must have
  unrestricted/permanent right to work", or equivalent. Set sponsorship_exclusion=true
  and capture the exact evidence. Do not treat ordinary right-to-work checks alone as
  an exclusion unless the wording rules out present or future sponsorship.
- Check the advertised salary against current official GOV.UK Skilled Worker salary
  rules for the role when an occupation code and salary can be established.
- Set salary_rule_status to "meets" only when this can be supported. Otherwise use
  "unclear"; never guess.
- Exclude expired or closed vacancies.
- Prefer direct employer vacancy URLs. Return each vacancy once.
- Score fit only from facts in the candidate profile. Do not infer credentials.
- Prioritise high-quality matches and avoid generic low-similarity roles merely because
  the employer might hold a licence.

Candidate profile:
{json.dumps(self.profile, ensure_ascii=False)}

Treat configured titles as query seeds and examples rather than an exact-match
allowlist. Search common spelling, seniority and naming variants, and include
differently titled vacancies when their actual responsibilities match this career
track. Do not lower the CV-fit, sponsorship or salary evidence standards.
"""
            for job in self._request_jobs(prompt, use_web=True):
                key = str(job.get("vacancy_url") or "").strip().casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
                if len(jobs) >= max_jobs:
                    return jobs
        return jobs

    def review_candidates(
        self, candidates: list[dict[str, Any]], max_jobs: int
    ) -> list[dict[str, Any]]:
        if not candidates or max_jobs <= 0:
            return []
        prompt = f"""
Review the supplied structured UK vacancy candidates against the candidate profile.
Return up to {max_jobs} strong matches. Use the supplied source name and vacancy URL.
The descriptions may be snippets, so use web search to verify that each vacancy is
still open and to inspect the full public advert where accessible.

Apply the same strict rules:
- Use only candidate-profile facts for fit scoring; never infer credentials.
- Detect explicit Skilled Worker/CoS sponsorship and capture its evidence URL.
- Silence about sponsorship is not an offer: set explicit_sponsorship=false.
- Detect explicit no-sponsorship or unrestricted/permanent-right-to-work requirements.
- Assess salary rules only when supported; otherwise use "unclear".
- Exclude closed, expired, duplicate, contract-only, and low-similarity roles.
- Preserve each supplied source_name exactly so the result can be attributed correctly.

Candidate profile:
{json.dumps(self.profile, ensure_ascii=False)}

Vacancy candidates:
{json.dumps(candidates, ensure_ascii=False)}
"""
        return self._request_jobs(prompt, use_web=True)[:max_jobs]

    def _request_jobs(
        self, prompt: str, *, use_web: bool
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if use_web:
            tools.append(
                {
                    "type": "web_search",
                    "search_context_size": "medium",
                    "user_location": {"type": "approximate", "country": "GB"},
                }
            )
        response = self.client.responses.create(
            model=self.search_model,
            reasoning={"effort": "low"},
            tools=tools,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "uk_sponsored_job_results",
                    "strict": True,
                    "schema": JOB_SCHEMA,
                }
            },
        )
        payload = json.loads(response.output_text)
        return payload["jobs"]

    def tailor(self, job: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""
Create an ATS-friendly UK CV and cover letter for this vacancy.

Hard rules:
- Use only facts present in the candidate profile.
- Never add or change qualifications, employers, job titles, dates, tools,
  responsibilities, metrics or achievements.
- Tailoring means selecting, reordering and clearly rewriting true facts to match the
  vacancy language.
- Keep the CV concise and readable, normally two pages when rendered.
- Do not put visa status, nationality, date of birth, photograph or street address on
  the CV.
- Mention sponsorship in the cover letter only in a brief, professional way.
- Put any uncertain gap or requirement in review_notes, not in the CV.

Candidate profile:
{json.dumps(self.profile, ensure_ascii=False)}

Vacancy:
{json.dumps(job, ensure_ascii=False, default=str)}
"""
        response = self.client.responses.create(
            model=self.tailor_model,
            reasoning={"effort": "medium"},
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "tailored_application",
                    "strict": True,
                    "schema": TAILOR_SCHEMA,
                }
            },
        )
        return json.loads(response.output_text)
