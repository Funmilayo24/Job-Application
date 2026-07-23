from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.documents import write_application_documents
from app.emailer import ResendEmailer, parse_recipients
from app.openai_service import OpenAIJobService, load_candidate_profile
from app.pipeline import JobPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _settings() -> Settings:
    return Settings.from_env()


def run_once(_: argparse.Namespace) -> None:
    result = JobPipeline(_settings()).run()
    print(
        f"Completed: discovered={result.discovered}, "
        f"qualified={result.qualified}, emailed={result.emailed}"
    )


def send_pending(_: argparse.Namespace) -> None:
    count = JobPipeline(_settings()).send_pending()
    print(f"Sent {count} pending job{'s' if count != 1 else ''}")


def resend_qualified(args: argparse.Namespace) -> None:
    settings = _settings()
    recipients = parse_recipients(settings.email_to)
    if args.recipient_index < 1 or args.recipient_index > len(recipients):
        raise SystemExit(
            f"Recipient index must be between 1 and {len(recipients)}"
        )
    database = Database(settings.database_url)
    database.initialise()
    jobs = database.all_qualified_jobs()
    if not jobs:
        print("No qualified jobs to resend")
        return
    recipient = recipients[args.recipient_index - 1]
    emailer = ResendEmailer(settings.resend_api_key, settings.email_from, recipient)
    emailer.send_digest(jobs)
    print(
        f"Resent {len(jobs)} qualified jobs to recipient "
        f"{args.recipient_index}"
    )


def refresh_sponsors(_: argparse.Namespace) -> None:
    count = JobPipeline(_settings()).refresh_sponsors()
    print(f"Imported {count} sponsor-register rows")


def list_jobs(args: argparse.Namespace) -> None:
    settings = _settings()
    database = Database(settings.database_url)
    database.initialise()
    rows = database.list_jobs(status=args.status, limit=args.limit)
    print(json.dumps(rows, indent=2, default=str))


def tailor(args: argparse.Namespace) -> None:
    settings = _settings()
    database = Database(settings.database_url)
    database.initialise()
    job = database.get_job(args.job_id)
    if not job:
        raise SystemExit(f"No job found with ID {args.job_id}")
    ai = OpenAIJobService(
        settings.openai_api_key,
        search_model=settings.search_model,
        tailor_model=settings.tailor_model,
    )
    content = ai.tailor(job)
    profile = load_candidate_profile()
    cv_path, letter_path, notes_path = write_application_documents(
        job,
        content,
        candidate_name=profile["name"],
        output_root=Path(args.output),
    )
    database.save_documents(
        args.job_id,
        cv_path=str(cv_path),
        cover_letter_path=str(letter_path),
        review_notes_path=str(notes_path),
        model=settings.tailor_model,
    )
    print(f"CV: {cv_path}\nCover letter: {letter_path}\nReview notes: {notes_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UK sponsorship job agent")
    commands = parser.add_subparsers(required=True)

    run_parser = commands.add_parser("run", help="Run a search immediately")
    run_parser.set_defaults(func=run_once)

    pending_parser = commands.add_parser(
        "send-pending", help="Email queued jobs without running a new search"
    )
    pending_parser.set_defaults(func=send_pending)

    resend_parser = commands.add_parser(
        "resend-qualified",
        help="Resend every qualified stored job to one configured recipient",
    )
    resend_parser.add_argument("--recipient-index", type=int, required=True)
    resend_parser.set_defaults(func=resend_qualified)

    sponsor_parser = commands.add_parser(
        "refresh-sponsors", help="Refresh the official UK sponsor register"
    )
    sponsor_parser.set_defaults(func=refresh_sponsors)

    list_parser = commands.add_parser("list", help="List stored vacancies")
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=list_jobs)

    tailor_parser = commands.add_parser(
        "tailor", help="Create a truthful tailored CV and cover letter for a stored job"
    )
    tailor_parser.add_argument("job_id", type=int)
    tailor_parser.add_argument("--output", default="artifacts")
    tailor_parser.set_defaults(func=tailor)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
