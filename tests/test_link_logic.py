import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.link_logic import (
    LinkType,
    LinkCreationRequest,
    LinkCreationError,
    validate_link_request,
    plan_link_creation,
    build_short_url,
    is_resolvable_code,
)


def test_valid_restaurant_ordering_request_passes():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="https://munchmap.synkra.co.za/r/some-restaurant",
        restaurant_id="rest_1",
    )
    assert validate_link_request(req) is None


def test_valid_order_collection_request_passes():
    req = LinkCreationRequest(
        link_type=LinkType.ORDER_COLLECTION,
        target_url="https://munchmap.synkra.co.za/orders/abc123",
        order_id="order_1",
    )
    assert validate_link_request(req) is None


def test_missing_restaurant_id_for_ordering_link_fails():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="https://munchmap.synkra.co.za/r/some-restaurant",
        restaurant_id=None,
    )
    assert validate_link_request(req) == LinkCreationError.MISSING_REFERENCE


def test_missing_order_id_for_collection_link_fails():
    req = LinkCreationRequest(
        link_type=LinkType.ORDER_COLLECTION,
        target_url="https://munchmap.synkra.co.za/orders/abc123",
        order_id=None,
    )
    assert validate_link_request(req) == LinkCreationError.MISSING_REFERENCE


def test_non_http_url_rejected():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="ftp://not-allowed.com",
        restaurant_id="rest_1",
    )
    assert validate_link_request(req) == LinkCreationError.INVALID_TARGET_URL


def test_empty_url_rejected():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="",
        restaurant_id="rest_1",
    )
    assert validate_link_request(req) == LinkCreationError.INVALID_TARGET_URL


def test_plan_creation_produces_candidate_code_for_valid_request():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="https://munchmap.synkra.co.za/r/some-restaurant",
        restaurant_id="rest_1",
    )
    plan = plan_link_creation(req)
    assert plan.valid is True
    assert plan.candidate_code is not None
    assert len(plan.candidate_code) == 6
    assert plan.error is None


def test_plan_creation_returns_no_code_for_invalid_request():
    req = LinkCreationRequest(
        link_type=LinkType.RESTAURANT_ORDERING,
        target_url="not-a-url",
        restaurant_id="rest_1",
    )
    plan = plan_link_creation(req)
    assert plan.valid is False
    assert plan.candidate_code is None
    assert plan.error == LinkCreationError.INVALID_TARGET_URL


def test_build_short_url_strips_trailing_slash_from_base():
    url = build_short_url("https://mm.synkra.co.za/", "a7k2m9")
    assert url == "https://mm.synkra.co.za/r/a7k2m9"


def test_build_short_url_without_trailing_slash():
    url = build_short_url("https://mm.synkra.co.za", "a7k2m9")
    assert url == "https://mm.synkra.co.za/r/a7k2m9"


def test_is_resolvable_code_accepts_valid_shape():
    assert is_resolvable_code("a7k2m9") is True


def test_is_resolvable_code_rejects_ambiguous_chars():
    assert is_resolvable_code("a7k2m0") is False


def test_is_resolvable_code_rejects_empty():
    assert is_resolvable_code("") is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
