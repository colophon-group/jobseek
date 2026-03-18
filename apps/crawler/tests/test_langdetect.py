"""Tests for language detection utilities."""

from __future__ import annotations

from src.batch import _build_locales
from src.shared.langdetect import detect_all_languages, detect_language

# ── detect_language (existing, smoke test) ───────────────────────────


def test_detect_language_english():
    assert detect_language("<p>This is a job posting written in English.</p>") == "en"


def test_detect_language_empty():
    assert detect_language("") is None


# ── detect_all_languages ─────────────────────────────────────────────

# Roughly 600 chars of English boilerplate + 600 chars of German body
_EN_DE_DESCRIPTION = (
    "<p>At Roche, we believe every employee makes a difference. We are passionate "
    "about transforming patients' lives. We are courageous in both decision and action. "
    "We are committed to creating an inclusive environment for everyone. "
    "Roche is an equal opportunity employer. Our global presence spans over 100 countries "
    "with a workforce of more than 100,000 employees. Together we stand for a uniquely "
    "caring culture. Join Roche, where every voice matters.</p>"
    "<h2>Stellenbeschreibung</h2>"
    "<p>Wir suchen eine engagierte Fachkraft für unser Team in Basel. "
    "In dieser Rolle sind Sie verantwortlich für die Entwicklung und Umsetzung "
    "von innovativen Lösungen im Bereich der Datenanalyse. Sie arbeiten eng mit "
    "unseren internationalen Teams zusammen und bringen Ihre Expertise in "
    "anspruchsvolle Projekte ein. Zu Ihren Aufgaben gehören die Analyse komplexer "
    "Datensätze, die Erstellung von Berichten und die Präsentation der Ergebnisse "
    "vor dem Management. Wir bieten Ihnen eine abwechslungsreiche Tätigkeit in "
    "einem dynamischen Umfeld mit hervorragenden Entwicklungsmöglichkeiten.</p>"
)

_PURE_ENGLISH = (
    "<p>We are looking for a software engineer to join our team in London. "
    "You will work on cutting-edge technology and collaborate with talented "
    "engineers across the organization. The ideal candidate has experience "
    "with distributed systems, cloud infrastructure, and modern programming "
    "languages. You should be comfortable working in an agile environment "
    "and have strong communication skills. We offer competitive compensation, "
    "excellent benefits, and flexible working arrangements. Apply now to "
    "join our growing team and make a real impact on our products.</p>"
)


def test_detect_all_dual_language():
    """EN boilerplate + DE body should detect both languages."""
    langs = detect_all_languages(_EN_DE_DESCRIPTION)
    assert "en" in langs
    assert "de" in langs


def test_detect_all_single_language():
    """Pure English text should return only English."""
    langs = detect_all_languages(_PURE_ENGLISH)
    assert "en" in langs
    assert len(langs) == 1


def test_detect_all_empty():
    assert detect_all_languages("") == []


def test_detect_all_short_text():
    """Text too short for meaningful chunks should return empty."""
    assert detect_all_languages("Hello world") == []


def test_detect_all_html_stripped():
    """HTML tags should be stripped before chunking."""
    langs = detect_all_languages(_PURE_ENGLISH)
    assert "en" in langs


def test_detect_all_minority_below_threshold():
    """A single foreign sentence in mostly-English text stays below 15%."""
    # ~90% English, one German sentence
    text = (
        "<p>We are hiring a senior engineer for our platform team. "
        "The role involves designing scalable systems and mentoring junior developers. "
        "You need five years of experience with Python and cloud platforms. "
        "Strong communication skills are essential for this position. "
        "We offer remote work options and competitive benefits. "
        "Our team is distributed across multiple time zones. "
        "You will participate in on-call rotations every six weeks. "
        "The interview process consists of four rounds. "
        "We value diversity and inclusion in our workplace. "
        "Wir freuen uns auf Ihre Bewerbung.</p>"
    )
    langs = detect_all_languages(text)
    assert "de" not in langs


# ── _build_locales integration ───────────────────────────────────────


def test_build_locales_with_detected_languages():
    result = _build_locales("en", None, detected_languages=["de", "fr"])
    assert result == ["en", "de", "fr"]


def test_build_locales_no_duplicates():
    """Detected languages already in locales should not be duplicated."""
    result = _build_locales("en", {"de": {"title": "Titel"}}, detected_languages=["en", "de", "fr"])
    assert result == ["en", "de", "fr"]


def test_build_locales_none_detected():
    """None or empty detected_languages should not change behavior."""
    assert _build_locales("en", None) == ["en"]
    assert _build_locales("en", None, detected_languages=None) == ["en"]
    assert _build_locales("en", None, detected_languages=[]) == ["en"]
