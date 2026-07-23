from __future__ import annotations

import html
from typing import Any

import httpx


def parse_recipients(value: str) -> list[str]:
    recipients = [address.strip() for address in value.split(",") if address.strip()]
    if not recipients:
        raise ValueError("EMAIL_TO must contain at least one email address")
    return recipients


def _render_job_table(jobs: list[dict[str, Any]], *, possible: bool = False) -> str:
    rows = []
    for job in jobs:
        vacancy_url = job.get("resolved_vacancy_url") or job["vacancy_url"]
        reasons = job.get("fit_reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        reason_html = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons[:3])
        if possible:
            sponsorship_html = f"""
                <strong>Confirmation required</strong><br>
                The employer appears on the Skilled Worker sponsor register, but this
                vacancy does not confirm sponsorship.<br>
                <small>
                  Licence match:
                  {html.escape(str(job.get('sponsor_register_name') or ''))}
                </small>
            """
        else:
            sponsorship_html = f"""
                {html.escape(str(job.get('sponsorship_evidence') or ''))}
                <br><small>
                  Licence match:
                  {html.escape(str(job.get('sponsor_register_name') or ''))}
                </small>
            """
        rows.append(
            f"""
            <tr>
              <td>
                <a href="{html.escape(str(vacancy_url), quote=True)}">
                  {html.escape(str(job['title']))}
                </a><br>
                <strong>{html.escape(str(job['employer']))}</strong><br>
                {html.escape(str(job.get('location') or 'Location not stated'))}<br>
                <small>
                  Source:
                  {"Jobs by Adzuna" if job.get("source_name") == "Adzuna"
                  else "Reed.co.uk" if job.get("source_name") == "Reed"
                  else html.escape(str(job.get("source_name") or "Employer listing"))}
                </small>
              </td>
              <td>{html.escape(str(job.get('salary_text') or 'Not stated'))}</td>
              <td>{int(job.get('fit_score', 0))}%</td>
              <td>{sponsorship_html}</td>
              <td><ul>{reason_html}</ul></td>
            </tr>
            """
        )
    return f"""
        <table style="border-collapse:collapse;width:100%" border="1" cellpadding="8">
          <thead>
            <tr>
              <th>Vacancy</th><th>Salary</th><th>Fit</th>
              <th>Sponsorship evidence</th><th>Why it fits</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
    """


def render_digest(jobs: list[dict[str, Any]]) -> str:
    confirmed = [
        job
        for job in jobs
        if job.get("sponsorship_tier") == "confirmed"
        or job.get("qualification_status") == "qualified_confirmed"
    ]
    possible = [job for job in jobs if job not in confirmed]

    sections = []
    if confirmed:
        sections.append(
            f"""
            <h3>Confirmed sponsorship ({len(confirmed)})</h3>
            <p>
              These vacancies explicitly mention sponsorship and the employer appears
              on the Skilled Worker sponsor register.
            </p>
            {_render_job_table(confirmed)}
            """
        )
    if possible:
        sections.append(
            f"""
            <h3>Sponsorship possible — confirm before applying ({len(possible)})</h3>
            <p>
              These are strong CV matches at licensed employers, but the vacancy does
              not explicitly offer sponsorship. Contact the recruiter before spending
              time tailoring an application:
            </p>
            <blockquote>
              I am currently on a UK Graduate visa and would require Skilled Worker
              sponsorship for continued employment. Is sponsorship available for this
              specific role?
            </blockquote>
            {_render_job_table(possible, possible=True)}
            """
        )

    return f"""
    <!doctype html>
    <html>
      <body style="font-family:Arial,sans-serif;color:#17202a">
        <h2>UK sponsorship job matches</h2>
        <p>
          Results are split by the strength of the vacancy's sponsorship evidence.
          Salary and occupation-code eligibility must still be verified before applying.
        </p>
        {''.join(sections)}
        <p>
          The agent does not submit applications automatically. Select a job ID in the
          local database to generate a tailored CV and cover letter for review.
        </p>
      </body>
    </html>
    """


class ResendEmailer:
    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str, email_from: str, email_to: str) -> None:
        self.api_key = api_key
        self.email_from = email_from
        self.email_to = parse_recipients(email_to)

    def send_digest(self, jobs: list[dict[str, Any]]) -> str | None:
        if not jobs:
            return None
        response = httpx.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": self.email_from,
                "to": self.email_to,
                "subject": f"{len(jobs)} new UK sponsorship job match"
                + ("es" if len(jobs) != 1 else ""),
                "html": render_digest(jobs),
            },
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json().get("id", ""))
