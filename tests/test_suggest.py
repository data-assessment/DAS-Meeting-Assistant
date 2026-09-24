"""Scoring rules for the export dialogs' pre-selection.

The ordering matters more than any single rule: the user's own remembered
choice must beat a domain-based guess — that is what makes correcting a suggestion stick.
"""
import pytest

from engine import suggest
from engine.suggest import Attendee, MeetingContext

OWN = frozenset({"company.example"})

NOTEBOOKS = [
    {"id": "nb1", "name": "Contoso GmbH"},
    {"id": "nb2", "name": "Fabrikam AG"},
    {"id": "nb3", "name": "Unrelated Ltd"},
]


def _context(*emails, title="", series_id=""):
    return MeetingContext(
        title=title,
        series_id=series_id,
        attendees=[Attendee(email=e) for e in emails],
        own_domains=OWN,
    )


def test_own_and_public_domains_are_not_customers():
    context = _context(
        "me@company.example", "someone@gmail.com", "anna@contoso.com",
    )

    assert context.emails == {"anna@contoso.com"}
    assert context.domains == ["contoso.com"]


def test_domain_match_finds_the_notebook():
    context = _context("anna@contoso.com")

    winner = suggest.best(suggest.by_domain(context, NOTEBOOKS))

    assert winner.value == "nb1"
    assert "contoso.com" in winner.reason


def test_dominant_domain_leads():
    """Two attendees from one customer outweigh one from another."""
    context = _context(
        "anna@contoso.com", "arno@contoso.com", "ben@fabrikam.de",
    )

    assert context.domains[0] == "contoso.com"
    assert suggest.best(suggest.by_domain(context, NOTEBOOKS)).value == "nb1"


def test_remembered_choice_beats_every_inference():
    context = _context("anna@contoso.com")
    remembered = suggest.learned(("nb3", "series"))

    winner = suggest.best([
        *suggest.by_domain(context, NOTEBOOKS),
        remembered,
    ])

    assert winner.value == "nb3"
    assert "recurring meeting" in winner.reason


def test_learned_keys_are_ranked():
    series = suggest.learned(("x", "series"))
    group = suggest.learned(("x", "group"))
    title = suggest.learned(("x", "title"))

    assert series.score > group.score > title.score
    assert group.score > suggest.DOMAIN


def test_unavailable_values_are_skipped():
    """A remembered record that has since gone must not be pre-selected."""
    context = _context("anna@contoso.com")
    gone = suggest.learned(("deleted-id", "series"))

    winner = suggest.best(
        [gone, *suggest.by_domain(context, NOTEBOOKS)],
        allowed={"nb1", "nb2"},
    )

    assert winner.value == "nb1"


def test_no_signal_yields_nothing():
    assert suggest.best([]) is None
    assert suggest.best([None]) is None
    assert suggest.learned(None) is None
    assert suggest.configured("", "label") is None


def test_configured_default_outranks_the_last_choice():
    winner = suggest.best([
        suggest.last_choice("last"),
        suggest.configured("configured", "ONENOTE_NOTEBOOK"),
    ])

    assert winner.value == "configured"


def test_short_domain_roots_do_not_match_everything():
    """A two-letter root would appear inside almost any name."""
    context = _context("someone@xy.example")

    assert suggest.by_domain(context, NOTEBOOKS) == []


@pytest.mark.parametrize("domain,expected", [
    ("contoso.com", "contoso"),
    ("mail.contoso.co.uk", "contoso"),
    ("contoso.de", "contoso"),
    ("sub.division.fabrikam.com", "fabrikam"),
    ("localhost", "localhost"),
])
def test_domain_root_extraction(domain, expected):
    assert suggest._root(domain) == expected


@pytest.mark.parametrize("title,expected", [
    ("Weekly Sync", "weekly sync"),
    ("Weekly Sync 2026-08-14", "weekly sync"),
    ("Weekly  Sync!!", "weekly sync"),
    ("weekly sync", "weekly sync"),
    ("Jour Fixe KW 33", "jour fixe"),
    ("Review 14.08.2026", "review"),
])
def test_titles_of_one_series_normalise_together(title, expected):
    assert suggest.normalise_title(title) == expected


def test_meeting_keys_are_ordered_and_group_is_hashed():
    context = _context(
        "anna@contoso.com", title="Weekly Sync", series_id="abc",
    )

    keys = context.keys()

    assert keys["series"] == "abc"
    assert keys["title"] == "weekly sync"
    # The participant key must not carry addresses around.
    assert "anna" not in keys["group"]
    assert len(keys["group"]) == 32


def test_same_participants_produce_the_same_group_key():
    one = _context("anna@contoso.com", "ben@fabrikam.de")
    two = _context("ben@fabrikam.de", "anna@contoso.com")

    assert one.keys()["group"] == two.keys()["group"]


def test_different_participants_produce_different_group_keys():
    one = _context("anna@contoso.com")
    two = _context("ben@fabrikam.de")

    assert one.keys()["group"] != two.keys()["group"]


def test_meeting_with_no_signals_has_no_keys():
    assert MeetingContext().keys() == {}
