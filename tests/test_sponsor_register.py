from app.sponsor_register import SponsorMatcher, discover_csv_url, normalise_company_name


def test_normalise_company_name() -> None:
    assert normalise_company_name("The Example & Partners Limited") == "example and partners"


def test_discover_csv_url() -> None:
    page = '<a href="/csv-preview/abc/sponsors.csv">Download</a>'
    assert discover_csv_url(page).startswith("https://www.gov.uk/csv-preview/")


def test_matcher_handles_limited_suffix() -> None:
    matcher = SponsorMatcher(
        [
            {
                "organisation_name": "Example Technology Limited",
                "normalised_name": "example technology",
            }
        ]
    )
    result = matcher.match("Example Technology Ltd")
    assert result.matched is True
    assert result.score == 100


def test_matcher_accepts_one_unique_legal_name_extension() -> None:
    matcher = SponsorMatcher(
        [
            {
                "organisation_name": "Fonoa Technologies UK Ltd",
                "normalised_name": "fonoa technologies",
            },
            {
                "organisation_name": "Different Company Ltd",
                "normalised_name": "different",
            },
        ]
    )
    result = matcher.match("Fonoa")
    assert result.matched is True
    assert result.register_name == "Fonoa Technologies UK Ltd"


def test_matcher_rejects_ambiguous_short_brand() -> None:
    matcher = SponsorMatcher(
        [
            {
                "organisation_name": "Visa Europe Limited",
                "normalised_name": "visa europe",
            },
            {
                "organisation_name": "Visa Services Limited",
                "normalised_name": "visa services",
            },
        ]
    )
    assert matcher.match("Visa").matched is False
