"""Pick the default choice for an export dialog from what the meeting tells us.

Notebook and section selection use the same ranking of meeting context,
remembered choices and configured defaults.

Sources are ranked, highest wins:

    LEARNED_SERIES   the user's own choice for this recurring meeting
    LEARNED_GROUP    the user's own choice for this set of attendees
    LEARNED_TITLE    the user's own choice for this meeting name
    DOMAIN           an attendee's mail domain resembles the record's name
    CONFIGURED       the configured default for this install
    LAST_CHOICE      whatever was picked last, regardless of meeting

A remembered choice outranks every inference, which is what makes correcting a
suggestion stick: overriding it is recorded, and that record then wins.

Nothing here decides anything on its own — a suggestion carries the reason it
was made so the dialog can show it, because a silently wrong pre-selection is
worse than none.
"""
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

# Free mail providers say nothing about which customer a meeting is with.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "gmx.de", "gmx.net", "web.de", "icloud.com", "me.com", "yahoo.com",
    "yahoo.de", "t-online.de", "aol.com", "proton.me", "protonmail.com",
})

LEARNED_SERIES = 100
LEARNED_GROUP = 80
LEARNED_TITLE = 60
DOMAIN = 50
CONFIGURED = 40
LAST_CHOICE = 30

_LEARNED_SCORES = {
    "series": LEARNED_SERIES,
    "group": LEARNED_GROUP,
    "title": LEARNED_TITLE,
}


@dataclass(frozen=True)
class Attendee:
    name: str = ""
    email: str = ""
    role: str = ""

    @property
    def domain(self) -> str:
        if "@" not in self.email:
            return ""
        return self.email.split("@")[-1].lower().strip()


@dataclass(frozen=True)
class Candidate:
    """One possible answer, with why it is being offered."""
    value: str
    score: int
    reason: str


@dataclass
class MeetingContext:
    """The signals a meeting offers, normalised once for every dialog."""
    title: str = ""
    series_id: str = ""
    attendees: list[Attendee] = field(default_factory=list)
    own_domains: frozenset[str] = frozenset()

    @property
    def external(self) -> list[Attendee]:
        """Attendees who are neither us nor on a free mail provider."""
        return [
            a for a in self.attendees
            if a.domain and a.domain not in self.own_domains
            and a.domain not in PUBLIC_DOMAINS
        ]

    @property
    def emails(self) -> set[str]:
        return {a.email.lower().strip() for a in self.external if a.email}

    @property
    def domains(self) -> list[str]:
        """External domains, most frequent first — the likeliest customer."""
        counts = Counter(a.domain for a in self.external)
        return [domain for domain, _ in counts.most_common()]

    def keys(self) -> dict[str, str]:
        """Memory keys for this meeting, strongest first.

        The series id is exact and survives a rename. The attendee group is a
        hash, so recognising a recurring set of people never means storing
        their addresses. The title is last because names repeat across
        customers.
        """
        keys: dict[str, str] = {}
        if self.series_id:
            keys["series"] = self.series_id
        emails = sorted(self.emails)
        if emails:
            digest = hashlib.sha256("\n".join(emails).encode("utf-8"))
            keys["group"] = digest.hexdigest()[:32]
        title = normalise_title(self.title)
        if title:
            keys["title"] = title
        return keys


def normalise_title(value: str) -> str:
    """Fold a meeting name so recurrences of it match each other.

    Teams appends dates and instance markers, and people retype casing and
    punctuation, so those must not split one series into many keys.
    """
    text = (value or "").casefold()
    text = re.sub(r"\b\d{1,4}[-./]\d{1,2}([-./]\d{1,4})?\b", " ", text)
    text = re.sub(r"\b(kw|cw|week|woche)\s*\d{1,2}\b", " ", text)
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _root(domain: str) -> str:
    """"contoso" from "mail.contoso.co.uk" — the part that resembles a name."""
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 1:
        return domain
    # Skip country/second-level suffixes so "contoso.co.uk" yields "contoso".
    suffixes = {"co", "com", "org", "net", "gov", "ac"}
    candidates = [p for p in parts[:-1] if p not in suffixes]
    return (candidates[-1] if candidates else parts[0]).lower()


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def learned(memory_hit: tuple[str, str] | None) -> Candidate | None:
    """Turn a ChoiceMemory hit into a candidate: `hit` is (value, key)."""
    if not memory_hit:
        return None
    value, matched_key = memory_hit
    if not value:
        return None
    labels = {
        "series": "you chose this for this recurring meeting",
        "group": "you chose this for these participants",
        "title": "you chose this for a meeting of this name",
    }
    return Candidate(
        value=value,
        score=_LEARNED_SCORES.get(matched_key, LEARNED_TITLE),
        reason=labels.get(matched_key, "remembered from your last choice"),
    )


def by_domain(
    context: MeetingContext,
    records: list[dict],
    name_key: str = "name",
    id_key: str = "id",
) -> list[Candidate]:
    """Records whose name resembles the dominant external mail domain.

    Weaker than an address match and deliberately scored below it: "contoso"
    appearing inside a name is suggestive, not proof.
    """
    found = []
    for domain in context.domains:
        root = _root(domain)
        if len(root) < 3:
            continue
        for record in records:
            name = _squash(str(record.get(name_key) or ""))
            if name and root in name:
                found.append(Candidate(
                    value=str(record.get(id_key) or ""),
                    score=DOMAIN,
                    reason=f"name resembles the domain {domain}",
                ))
    return [c for c in found if c.value]


def configured(value: str, label: str) -> Candidate | None:
    if not value:
        return None
    return Candidate(value=value, score=CONFIGURED,
                     reason=f"configured default ({label})")


def last_choice(value: str) -> Candidate | None:
    if not value:
        return None
    return Candidate(value=value, score=LAST_CHOICE,
                     reason="your last choice")


def best(candidates, allowed: set[str] | None = None) -> Candidate | None:
    """Highest-scoring candidate that is actually selectable.

    `allowed` filters to values the dialog can offer, so a remembered record
    that has since been deleted or closed falls through to the next best guess
    instead of pre-selecting something that is no longer there.
    """
    usable = [c for c in candidates if c and c.value]
    if allowed is not None:
        usable = [c for c in usable if c.value in allowed]
    if not usable:
        return None
    # max() keeps the first of equal scores, so earlier sources win ties.
    return max(usable, key=lambda c: c.score)
