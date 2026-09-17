"""Tests for domain profiles: schema validity and the corrections policy.

Policy for broad field dictionaries:
- ``initial_prompt`` vocabulary is curated upfront per field (incl. slang).
- Every ``corrections`` entry must be EITHER a pure normalization
  (same letters, only casing/spacing/hyphens differ) OR a
  Cyrillic-phonetic-render -> canonical-Latin fix for terms whose written
  form is Latin (identifiers, brands, acronyms).
- No invented same-script mishearings, no "corrections" of legitimate
  Russian loanwords (баг, стендап, митинг stay untouched).
- Personal narrow profiles (scada/mes/dashboard) are exempt: they hold
  corrections observed from real mishearings.
"""

import re

from meetingnotes.profiles import list_profiles, load_profile

BROAD_FIELDS = {
    "software", "business", "medical", "legal", "finance",
    "education", "science", "marketing", "support", "hr",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s\-]", "", s.lower())


def _cyrillic_dominant(s: str) -> bool:
    cyr = sum("\u0400" <= c <= "\u04FF" for c in s)
    lat = sum(c.isascii() and c.isalpha() for c in s)
    return cyr > 0 and cyr >= lat


def _latin_only(s: str) -> bool:
    stripped = re.sub(r"[\s\-/&]", "", s)
    return len(stripped) > 0 and all(
        c.isascii() and (c.isalpha() or c.isdigit()) for c in stripped
    )


def test_all_profiles_load_with_valid_schema():
    profiles = list_profiles()
    assert len(profiles) >= 14  # generic + 3 personal + 10 broad
    for p in profiles:
        assert p.name, "profile needs a name"
        assert p.description, f"{p.name}: needs a description"
        assert p.language, f"{p.name}: needs a language"
        assert p.initial_prompt.strip(), f"{p.name}: needs a vocabulary prompt"
        assert isinstance(p.corrections, dict)
        for wrong, right in p.corrections.items():
            assert isinstance(wrong, str) and wrong.strip()
            assert isinstance(right, str) and right.strip()


def test_broad_profiles_are_full():
    """Shipping quality: every broad dictionary carries real content."""
    for name in BROAD_FIELDS:
        p = load_profile(name)
        assert len(p.initial_prompt.split()) >= 40, f"{name}: prompt too thin"
        assert len(p.corrections) >= 2, f"{name}: corrections map is empty"


def test_broad_corrections_are_normalizations_or_transliteration_fixes():
    """No fabricated mishearings: each entry must be a safe normalization
    or a Cyrillic-phonetic -> canonical-Latin fix."""
    for name in BROAD_FIELDS:
        for wrong, right in load_profile(name).corrections.items():
            is_norm = _norm(wrong) == _norm(right)
            is_translit = _cyrillic_dominant(wrong) and _latin_only(right)
            assert is_norm or is_translit, (
                f"{name}: {wrong!r} -> {right!r} looks fabricated"
            )


def test_personal_profiles_keep_observed_corrections():
    """The narrow dictionaries built from real mishearings stay intact."""
    scada = load_profile("scada")
    assert len(scada.corrections) > 0
    mes = load_profile("mes")
    assert len(mes.corrections) > 0
    dashboard = load_profile("dashboard")
    assert len(dashboard.corrections) > 0


def test_unknown_profile_falls_back_to_generic():
    assert load_profile("no-such-field").name == "generic"


EN_FIELDS = BROAD_FIELDS | {"generic"}

MULTILANG = ["es", "de", "fr", "pt"]
MULTILANG_PROFILES = ["generic", "software", "business", "support", "marketing"]


def test_english_variants_load_with_english_content():
    for name in EN_FIELDS:
        p = load_profile(name, "en")
        assert p.name == name, f"en variant lost its name: {name}"
        assert p.language == "en", f"{name}: en variant must declare language en"
        assert len(p.initial_prompt.split()) >= 5, f"{name}.en: prompt too thin"


def test_multilang_variants_load_with_matching_language():
    for lang in MULTILANG:
        for name in MULTILANG_PROFILES:
            p = load_profile(name, lang)
            assert p.name == name, f"{lang}: lost name for {name}"
            assert p.language == lang, f"{name}.{lang} must declare language {lang}"
            assert len(p.initial_prompt.split()) >= 5, f"{name}.{lang}: prompt too thin"


def test_uncovered_language_falls_back_to_english():
    """Languages without their own dicts use EN (lingua franca), not RU."""
    p = load_profile("software", "it")
    assert "pull request" in p.initial_prompt
    assert "пул" not in p.initial_prompt
    g = load_profile("generic", "it")
    assert "пул" not in g.initial_prompt and "reunión" not in g.initial_prompt


def test_ru_is_the_base_for_ru_and_unsuffixed():
    assert load_profile("software", "ru").language == "ru"
    assert load_profile("software").language == "ru"


def test_default_load_keeps_russian_content():
    """No language requested -> the original RU dictionaries (backward compat)."""
    p = load_profile("software")
    assert p.language == "ru"
    assert "пул" in p.initial_prompt or "код" in p.initial_prompt


def test_french_hallucination_guard_present():
    """Documented Whisper FR quirk must be neutralized in every fr profile."""
    for name in MULTILANG_PROFILES:
        p = load_profile(name, "fr")
        assert any("sous-titrage" in k for k in p.corrections), (
            f"{name}.fr: missing the documented sous-titrage hallucination guard"
        )


def test_unknown_language_falls_back_to_english_not_ru():
    """Unknown languages route through the EN fallback (lingua franca)."""
    p = load_profile("software", "xx")
    assert p.name == "software"
    assert p.language == "en"


def test_list_profiles_has_no_duplicate_names():
    names = [p.name for p in list_profiles()]
    assert len(names) == len(set(names)), "language variants must not duplicate dropdown entries"
    assert len(names) >= 14


def test_english_corrections_hold_the_same_policy():
    """EN maps may only contain normalizations (no Cyrillic keys possible)."""
    for name in EN_FIELDS:
        for wrong, right in load_profile(name, "en").corrections.items():
            is_norm = _norm(wrong) == _norm(right)
            assert is_norm, f"{name}.en: {wrong!r} -> {right!r} looks fabricated"
            assert wrong.isascii() and right.replace(" ", "").replace("-", "").replace("&", "").replace("/", "").isascii(), (
                f"{name}.en: non-ASCII in an English map: {wrong!r} -> {right!r}"
            )


def test_latin_language_corrections_policy():
    """Latin-script maps: normalization, phonetic misspelling of a Latin
    canonical form (truncation/substitution), or documented hallucination
    removal (empty right-hand side strips the phrase)."""
    hallucination_markers = ("sous-titrage", "miduism", "terminieren", "meia pipe")

    def _strip_ok(wrong: str, right: str) -> bool:
        return right == "" and any(m in wrong for m in hallucination_markers)

    def _phonetic(wrong: str, right: str) -> bool:
        """Latin misspelling of a Latin term: shares a long prefix or the
        consonant skeleton with the canonical form (javascrip->JavaScript,
        postgree->Postgres). Pure normalizations are handled elsewhere."""
        if right == "":
            return False
        w, r = wrong.lower().replace(" ", ""), right.lower().replace(" ", "")
        if w == r or _norm(wrong) == _norm(right):
            return False
        # common prefix >= 5 chars against the canonical form
        common = 0
        for a, b in zip(w, r):
            if a != b:
                break
            common += 1
        if common >= 5:
            return True
        # consonant skeleton identical (vowel errors)
        cons = lambda s: "".join(c for c in s if c.isalpha() and c not in "aeiouäöüéèáíóú")
        return cons(w) == cons(r)

    for lang in MULTILANG:
        for name in MULTILANG_PROFILES:
            for wrong, right in load_profile(name, lang).corrections.items():
                ok = (
                    (_norm(wrong) == _norm(right) and right != "")
                    or _strip_ok(wrong, right)
                    or _phonetic(wrong, right)
                )
                assert ok, (
                    f"{name}.{lang}: {wrong!r} -> {right!r} is not a normalization, "
                    f"phonetic misspelling, or documented hallucination guard"
                )
