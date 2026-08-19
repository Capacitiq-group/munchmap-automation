import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.utils.shortcode import generate_short_code, is_valid_short_code, DEFAULT_CODE_LENGTH, _ALPHABET


def test_default_length_is_six():
    code = generate_short_code()
    assert len(code) == DEFAULT_CODE_LENGTH


def test_custom_length():
    code = generate_short_code(length=10)
    assert len(code) == 10


def test_rejects_too_short():
    try:
        generate_short_code(length=3)
        assert False, "expected ValueError for length < 4"
    except ValueError:
        pass


def test_excludes_ambiguous_characters():
    # Generate a large batch and confirm none of the excluded chars ever appear
    excluded = set("0o1il")
    for _ in range(500):
        code = generate_short_code()
        assert not (set(code) & excluded), f"code {code} contained an excluded character"


def test_alphabet_has_no_ambiguous_chars():
    assert not (set(_ALPHABET) & set("0o1il"))


def test_generated_codes_only_use_defined_alphabet():
    for _ in range(500):
        code = generate_short_code()
        assert all(ch in _ALPHABET for ch in code)


def test_is_valid_short_code_accepts_real_codes():
    for _ in range(50):
        code = generate_short_code()
        assert is_valid_short_code(code) is True


def test_is_valid_short_code_rejects_empty():
    assert is_valid_short_code("") is False


def test_is_valid_short_code_rejects_ambiguous_chars():
    assert is_valid_short_code("a7k2m0") is False  # contains 0
    assert is_valid_short_code("a7k2mI") is False  # contains I (uppercase not in alphabet anyway)
    assert is_valid_short_code("a7k2ml") is False  # contains l


def test_codes_are_reasonably_unique_across_many_generations():
    # Not a formal proof of uniqueness - just a sanity check that we are not
    # accidentally generating the same code repeatedly due to a seeding bug.
    codes = {generate_short_code() for _ in range(5000)}
    assert len(codes) > 4990  # allow for a handful of extremely unlikely collisions


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
