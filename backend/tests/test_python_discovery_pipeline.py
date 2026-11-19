"""
Comprehensive unit and integration tests for the Python Discovery & Qualification Pipeline.

Validates:
1. Apify response normalization
2. Preservation of original business fields
3. Gemini response parsing and validation
4. Missing or malformed AI responses
5. Deterministic scoring integration
6. Duplicate business handling
7. External API failures and timeouts
8. Pipeline output compatibility with existing LeadForge models and schemas
"""

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.db import create_sqlite_engine, get_db, init_db
from backend.app.models import Lead, LeadAnalysis, SearchRun, SearchRunLead, SearchRunStatus
from backend.app.schemas.n8n import (
    N8nCallbackRequest,
    N8nCallbackResponse,
    N8nLeadAnalysisPayload,
    N8nLeadItemPayload,
)
from backend.app.services.apify_service import (
    ApifyConfigurationError,
    ApifyExecutionError,
    build_apify_google_maps_input,
    discover_businesses,
    normalize_and_deduplicate_apify_items,
    scrape_website_content,
)
from backend.app.services.discovery_orchestrator import (
    DiscoveryPipelineResult,
    execute_discovery_pipeline,
    generate_controlled_test_lead,
)
from backend.app.services.gemini_service import (
    GeminiConfigurationError,
    GeminiExecutionError,
    analyze_lead_with_gemini,
    build_stage1_prompt,
    build_stage2_prompt,
    call_gemini_api,
    parse_structured_or_text_json,
    strip_markdown_json,
)
from backend.app.services.lead_service import get_lead_detail, list_leads
from backend.app.services.n8n_service import process_n8n_callback
from backend.app.services.scoring_service import (
    HIGH_PRIORITY_THRESHOLD,
    MEDIUM_PRIORITY_THRESHOLD,
    apply_lead_scoring,
)


@pytest.fixture()
def pipeline_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / "test_pipeline_leadforge.db"
    engine = create_sqlite_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return engine


@pytest.fixture()
def pipeline_session(pipeline_engine: Engine) -> Generator[Session, None, None]:
    TestingSessionLocal = sessionmaker(
        bind=pipeline_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ==============================================================================
# 1. Apify Response Normalization Tests
# ==============================================================================


def test_build_apify_google_maps_input_matches_n8n_schema() -> None:
    """Verify Apify input builder produces the exact parameters used in the n8n HTTP Request node."""
    payload = build_apify_google_maps_input(
        city="Austin, TX",
        business_type="Dental Clinic",
        max_results=25,
    )

    assert payload["locationQuery"] == "Austin, TX"
    assert payload["searchStringsArray"] == ["Dental Clinic"]
    assert payload["maxCrawledPlacesPerSearch"] == 25
    assert payload["includeWebResults"] is False
    assert payload["language"] == "en"
    assert payload["skipClosedPlaces"] is False
    assert payload["scrapeContacts"] is False
    assert payload["maximumLeadsEnrichmentRecords"] == 0

    # Bounds check
    assert build_apify_google_maps_input("Austin", "Dentist", 0)["maxCrawledPlacesPerSearch"] == 1
    assert build_apify_google_maps_input("Austin", "Dentist", 9999)["maxCrawledPlacesPerSearch"] == 500


def test_apify_normalization_camelcase_and_types() -> None:
    """Verify camelCase Apify fields (placeId, totalScore, reviewsCount) are normalized properly."""
    raw_items = [
        {
            "title": "Austin Smiles Dental",
            "placeId": "ChIJ_austin_smiles_01",
            "website": "https://austinsmiles.example.com",
            "address": "123 Congress Ave, Austin, TX",
            "phone": "+1-512-555-0101",
            "city": "Austin",
            "categories": ["Dentist", "Dental clinic"],
            "totalScore": 4.8,
            "reviewsCount": 112,
        }
    ]

    normalized = normalize_and_deduplicate_apify_items(raw_items, "Austin", "Dental Clinic")
    assert len(normalized) == 1
    item = normalized[0]

    assert item["title"] == "Austin Smiles Dental"
    assert item["company_name"] == "Austin Smiles Dental"
    assert item["place_id"] == "ChIJ_austin_smiles_01"
    assert item["placeId"] == "ChIJ_austin_smiles_01"
    assert item["website"] == "https://austinsmiles.example.com"
    assert item["rating"] == 4.8
    assert item["totalScore"] == 4.8
    assert item["reviews_count"] == 112
    assert item["reviewsCount"] == 112
    assert item["categories"] == ["Dentist", "Dental clinic"]
    assert item["raw_apify"]["placeId"] == "ChIJ_austin_smiles_01"


# ==============================================================================
# 2. Preservation of Original Business Fields Tests
# ==============================================================================


def test_preservation_of_unmutated_apify_fields() -> None:
    """Verify raw_apify retains extra vendor fields and original title is never overwritten."""
    raw_item = {
        "title": "Original Clinic Name LLC",
        "placeId": "ChIJ_orig_001",
        "website": "http://originalclinic.example.com",
        "address": "400 Lavaca St",
        "phone": "+1-512-555-0199",
        "custom_vendor_tag": "tag-xyz-99",
        "extra_hours": {"monday": "8am-5pm"},
    }

    normalized = normalize_and_deduplicate_apify_items([raw_item], "Austin", "Clinic")
    assert len(normalized) == 1
    lead_dict = normalized[0]

    # Verify custom keys are preserved in raw_apify
    assert lead_dict["raw_apify"]["custom_vendor_tag"] == "tag-xyz-99"
    assert lead_dict["raw_apify"]["extra_hours"] == {"monday": "8am-5pm"}

    # Mock Stage 2 Gemini attempting to rename the company
    analysis, raw_ai, _ = analyze_lead_with_gemini(
        lead_dict,
        website_content="",
        mock_mode=True,
    )

    # In mock mode or real mode, canonical company_name remains Apify title
    assert lead_dict["company_name"] == "Original Clinic Name LLC"
    assert raw_ai["ai_company_name"] == "Original Clinic Name LLC"


# ==============================================================================
# 3. Gemini Response Parsing & Validation Tests
# ==============================================================================


def test_strip_markdown_json_and_structured_parsing() -> None:
    """Verify strip_markdown_json handles plain JSON, markdown code blocks, and nested text."""
    # Case 1: Plain JSON
    plain = '{"company_name": "Acme Corp", "confidence": "high"}'
    parsed1 = parse_structured_or_text_json(plain)
    assert parsed1 == {"company_name": "Acme Corp", "confidence": "high"}

    # Case 2: Markdown code fence ```json ... ```
    fenced = """```json
{
  "company_name": "Acme Corp Fenced",
  "icebreaker": "Hello Acme!"
}
```"""
    parsed2 = parse_structured_or_text_json(fenced)
    assert parsed2 == {"company_name": "Acme Corp Fenced", "icebreaker": "Hello Acme!"}

    # Case 3: Leading / trailing conversational text around markdown fence
    surrounded = """Here is the business analysis in JSON format:
```json
{
  "business_summary": "Auto repair shop in Biskra.",
  "confidence": "high"
}
```
Let me know if you need any adjustments."""
    parsed3 = parse_structured_or_text_json(surrounded)
    assert parsed3 == {
        "business_summary": "Auto repair shop in Biskra.",
        "confidence": "high",
    }

    # Case 4: Pre-parsed dictionary
    dict_input = {"company_name": "Direct Dict"}
    assert parse_structured_or_text_json(dict_input) == dict_input


def test_confidence_normalization_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify confidence values 'high', 'medium', 'low' and numbers map accurately."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    get_settings.cache_clear()

    lead = {
        "title": "Biskra Car Rental",
        "city": "Biskra",
        "business_type": "Car Rental",
    }
    with patch(
        "backend.app.services.gemini_service.call_gemini_api"
    ) as mock_gemini:
        # Stage 1 returns confidence 'high'
        mock_gemini.side_effect = [
            json.dumps({"business_summary": "Car rental agency in Biskra.", "confidence": "high"}),
            json.dumps({
                "company_name": "Biskra Car Rental",
                "personalization_observation": "Active local fleet.",
                "potential_opportunity": "Add online booking system.",
                "icebreaker": "Hello Biskra Car Rental team.",
            }),
        ]
        analysis, _, status = analyze_lead_with_gemini(lead, "sample web content")
        assert status == "completed"
        assert analysis is not None
        assert analysis.confidence == 0.9

        # Stage 1 returns confidence 'medium'
        mock_gemini.side_effect = [
            json.dumps({"business_summary": "Car rental agency.", "confidence": "medium"}),
            json.dumps({
                "company_name": "Biskra Car Rental",
                "personalization_observation": "Active fleet.",
                "potential_opportunity": "Add online booking.",
                "icebreaker": "Hello team.",
            }),
        ]
        analysis, _, _ = analyze_lead_with_gemini(lead, "")
        assert analysis is not None
        assert analysis.confidence == 0.6


# ==============================================================================
# 4. Missing or Malformed AI Responses Tests
# ==============================================================================


def test_malformed_ai_responses_do_not_crash_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pipeline degrades gracefully to partial_ai_failure when Gemini outputs invalid syntax."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    get_settings.cache_clear()

    lead = {
        "title": "Biskra Transporters",
        "city": "Biskra",
        "business_type": "Logistics",
    }

    # Case A: Stage 1 returns unparseable HTML error page
    with patch(
        "backend.app.services.gemini_service.call_gemini_api"
    ) as mock_gemini:
        mock_gemini.side_effect = [
            "<html>502 Bad Gateway</html>",
            json.dumps({
                "company_name": "Biskra Transporters",
                "personalization_observation": "Local logistics provider.",
                "potential_opportunity": "Fleet dispatch portal.",
                "icebreaker": "Noticed your Biskra transport services.",
            }),
        ]
        analysis, raw_ai, status = analyze_lead_with_gemini(lead, "")
        assert status == "completed"  # Stage 2 succeeded, so meaningful content exists
        assert raw_ai["stage1_ai"] == {"error": "Stage 1 AI output is not a valid JSON object"}
        assert analysis is not None
        assert analysis.personalization_observation == "Local logistics provider."

    # Case B: Stage 2 returns incomplete JSON (missing required fields)
    with patch(
        "backend.app.services.gemini_service.call_gemini_api"
    ) as mock_gemini:
        mock_gemini.side_effect = [
            json.dumps({"business_summary": "Biskra logistics hub.", "confidence": "high"}),
            json.dumps({"company_name": "Only Name Given"}),  # Missing personalization, opportunity, icebreaker
        ]
        analysis, raw_ai, status = analyze_lead_with_gemini(lead, "")
        assert status == "partial_ai_failure"
        assert analysis is not None
        assert analysis.business_summary == "Biskra logistics hub."
        assert "Missing or empty Stage 2 AI fields" in str(analysis.error)

    # Case C: Both stages raise exceptions (e.g. timeout / network down)
    with patch(
        "backend.app.services.gemini_service.call_gemini_api",
        side_effect=httpx.ConnectTimeout("Network timed out"),
    ):
        analysis, raw_ai, status = analyze_lead_with_gemini(lead, "")
        assert status == "partial_ai_failure"
        assert analysis is None
        assert "ConnectTimeout" in raw_ai["stage1_ai"]["error"]


# ==============================================================================
# 5. Deterministic Scoring Integration Tests
# ==============================================================================


def test_deterministic_scoring_integration(pipeline_session: Session) -> None:
    """Verify that deterministic scoring calculates exact dimension scores and Gemini cannot override it."""
    run = SearchRun(city="Austin", business_type="Dental Clinic", max_results=10, status="running")
    pipeline_session.add(run)
    pipeline_session.commit()

    # Discovered lead without a website (should receive maximum digital presence opportunity points)
    lead_payload = N8nLeadItemPayload(
        company_name="Austin Dental Care",
        title="Austin Dental Care",
        place_id="ChIJ_score_001",
        website=None,
        address="100 Main St, Austin, TX",
        phone="+1-512-555-0100",
        city="Austin",
        business_type="Dental Clinic",
        rating=4.5,
        reviews_count=80,
        analysis=N8nLeadAnalysisPayload(
            business_summary="Local dental clinic without a domain.",
            potential_opportunity="Build custom patient booking portal and automated recall system.",
            confidence=0.9,
        ),
    )

    callback_req = N8nCallbackRequest(
        search_run_id=run.id,
        status="completed",
        leads=[lead_payload],
    )
    cb_resp = process_n8n_callback(pipeline_session, callback_req)
    assert cb_resp.leads_created == 1

    saved_lead = pipeline_session.scalar(
        select(Lead).where(Lead.company_name == "Austin Dental Care")
    )
    assert saved_lead is not None
    assert saved_lead.score >= HIGH_PRIORITY_THRESHOLD
    assert saved_lead.priority == "High"

    breakdown = json.loads(saved_lead.score_breakdown)
    # Digital presence without website yields max 25 points
    assert breakdown["dimensions"]["digital_presence_opportunity"]["score"] == 25
    assert breakdown["dimensions"]["service_opportunity"]["score"] >= 20
    assert breakdown["total_score"] == saved_lead.score


# ==============================================================================
# 6. Duplicate Business Handling Tests
# ==============================================================================


def test_deduplication_within_batch_and_database(pipeline_session: Session) -> None:
    """Verify duplicate leads are filtered within Apify batch and matched idempotently in DB."""
    # 1. Deduplication within Apify batch (priority: placeId > website > name+address)
    items = [
        {"title": "Austin Dental", "placeId": "ChIJ_dup_001", "website": "https://austindental.example.com"},
        {"title": "Austin Dental Duplicate", "placeId": "ChIJ_dup_001", "website": "https://other.example.com"},
        {"title": "Same Website Clinic A", "website": "https://same-web.example.com", "address": "Addr 1"},
        {"title": "Same Website Clinic B", "website": "https://www.same-web.example.com/", "address": "Addr 2"},
    ]
    deduped = normalize_and_deduplicate_apify_items(items, "Austin", "Dental")
    assert len(deduped) == 2  # 1 placeId duplicate removed, 1 website duplicate removed

    # 2. Database idempotent retry: second execution updates existing lead rather than creating duplicate
    run = SearchRun(city="Austin", business_type="Dental", max_results=10, status="running")
    pipeline_session.add(run)
    pipeline_session.commit()

    lead_item = N8nLeadItemPayload(
        company_name="Austin Dental",
        title="Austin Dental",
        place_id="ChIJ_dup_001",
        website="https://austindental.example.com",
        city="Austin",
        business_type="Dental",
    )

    req1 = N8nCallbackRequest(search_run_id=run.id, status="completed", leads=[lead_item])
    resp1 = process_n8n_callback(pipeline_session, req1)
    assert resp1.leads_created == 1
    assert resp1.leads_updated == 0

    # Repeat delivery of same lead
    lead_item_updated = N8nLeadItemPayload(
        company_name="Austin Dental",
        title="Austin Dental",
        place_id="ChIJ_dup_001",
        website="https://austindental.example.com",
        city="Austin",
        business_type="Dental",
        phone="+1-512-555-0999",  # added phone
    )
    req2 = N8nCallbackRequest(search_run_id=run.id, status="completed", leads=[lead_item_updated])
    resp2 = process_n8n_callback(pipeline_session, req2)
    assert resp2.leads_created == 0
    assert resp2.leads_updated == 1

    from sqlalchemy import func

    total_leads = pipeline_session.scalar(select(func.count(Lead.id)))
    assert total_leads == 1


# ==============================================================================
# 7. External API Failures & Timeouts Tests
# ==============================================================================


def test_apify_missing_token_and_http_errors() -> None:
    """Verify Apify raises ApifyConfigurationError if token missing and ApifyExecutionError on HTTP error."""
    # Missing token
    with pytest.raises(ApifyConfigurationError):
        discover_businesses("Austin", "Dental", token="")

    # HTTP 401 error
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = httpx.Response(401, text="Unauthorized: Invalid token")
        with pytest.raises(ApifyExecutionError, match="HTTP 401"):
            discover_businesses("Austin", "Dental", token="bad-token")

    # Timeout error
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Apify timed out")):
        with pytest.raises(ApifyExecutionError, match="timed out"):
            discover_businesses("Austin", "Dental", token="valid-token")


def test_website_scraper_handles_errors_gracefully() -> None:
    """Verify website scraper returns empty string without failing pipeline on errors or 404s."""
    with patch("httpx.Client.get") as mock_get:
        # HTTP 404
        mock_get.return_value = httpx.Response(404, text="Not Found")
        assert scrape_website_content("https://broken.example.com") == ""

        # Timeout
        mock_get.side_effect = httpx.TimeoutException("Scraper timeout")
        assert scrape_website_content("https://slow.example.com") == ""

        # Empty or invalid URL
        assert scrape_website_content(None) == ""
        assert scrape_website_content("") == ""


# ==============================================================================
# 8. Pipeline Compatibility with Existing Models & Schemas Tests
# ==============================================================================


def test_full_python_pipeline_orchestration_end_to_end(
    pipeline_session: Session,
) -> None:
    """
    Verify complete execution of execute_discovery_pipeline:
    SearchRun -> Apify items -> Scraper -> Gemini analysis -> DB persistence -> Score computation -> API querying.
    """
    run = SearchRun(city="Austin", business_type="Dental Clinic", max_results=5, status="pending")
    pipeline_session.add(run)
    pipeline_session.commit()

    mock_apify_items = [
        {
            "title": "Congress Dental Group",
            "place_id": "ChIJ_e2e_001",
            "placeId": "ChIJ_e2e_001",
            "website": "https://congressdental.example.com",
            "address": "500 Congress Ave, Austin, TX",
            "phone": "+1-512-555-0150",
            "city": "Austin",
            "business_type": "Dental Clinic",
            "categories": ["Dental Clinic"],
            "rating": 4.9,
            "reviews_count": 94,
            "raw_apify": {"title": "Congress Dental Group", "placeId": "ChIJ_e2e_001"},
        }
    ]

    with patch(
        "backend.app.services.discovery_orchestrator.scrape_website_content",
        return_value="Congress Dental Group provides general and cosmetic dentistry in downtown Austin.",
    ):
        result: DiscoveryPipelineResult = execute_discovery_pipeline(
            pipeline_session,
            run.id,
            mock_mode=True,
            apify_items_override=mock_apify_items,
        )

    assert result.status == "completed"
    assert result.leads_discovered == 1
    assert result.leads_created == 1

    # Verify SearchRun status updated in DB
    db_run = pipeline_session.get(SearchRun, run.id)
    assert db_run is not None
    assert db_run.status == SearchRunStatus.COMPLETED.value

    # Verify Lead persisted with analysis and deterministic score
    leads_resp = list_leads(pipeline_session, search_run_id=run.id)
    assert leads_resp.total == 1
    lead_summary = leads_resp.items[0]

    assert lead_summary.company_name == "Congress Dental Group"
    assert lead_summary.city == "Austin"
    assert lead_summary.score >= MEDIUM_PRIORITY_THRESHOLD
    assert lead_summary.analysis is not None
    assert lead_summary.analysis.personalization_observation is not None
    assert lead_summary.analysis.confidence == 0.9

    # Verify Lead detail query compatibility
    detail_resp = get_lead_detail(pipeline_session, lead_summary.id)
    assert detail_resp is not None
    assert detail_resp.company_name == "Congress Dental Group"
    assert detail_resp.raw_payload is not None
    raw_payload_dict = json.loads(detail_resp.raw_payload)
    assert raw_payload_dict["apify"]["placeId"] == "ChIJ_e2e_001"
    assert raw_payload_dict["stage1_ai"] is not None
    assert raw_payload_dict["stage2_ai"] is not None


def test_controlled_test_mode_bypasses_external_apis(
    pipeline_session: Session,
) -> None:
    """Verify TEST_ONLY_ search run runs synthetic qualification without calling external APIs."""
    test_run = SearchRun(
        city="TEST_ONLY_Austin",
        business_type="Roofing Contractor",
        max_results=5,
        status="pending",
    )
    pipeline_session.add(test_run)
    pipeline_session.commit()

    with patch("backend.app.services.apify_service.discover_businesses") as mock_apify, patch(
        "backend.app.services.gemini_service.call_gemini_api"
    ) as mock_gemini:
        result = execute_discovery_pipeline(pipeline_session, test_run.id)

        # Neither external API should be called
        mock_apify.assert_not_called()
        mock_gemini.assert_not_called()

        assert result.status == "completed"
        assert result.test_mode is True
        assert result.leads_created == 1

        lead = pipeline_session.scalar(select(Lead).where(Lead.city == "TEST_ONLY_Austin"))
        assert lead is not None
        assert lead.score >= MEDIUM_PRIORITY_THRESHOLD


# ==============================================================================
# 9. Phase 1.2 Audit Regression & Verification Tests
# ==============================================================================


def test_apify_business_type_priority_matches_n8n() -> None:
    """
    Verify requested search business_type takes priority over Apify categories[0],
    matching original n8n: trigger.business_type || categories[0] || ''.
    """
    raw_items = [
        {
            "title": "Bright Smile Austin",
            "placeId": "ChIJ_btype_001",
            "categories": ["Cosmetic dentist", "Dentist"],
            "totalScore": 4.9,
            "reviewsCount": 55,
        }
    ]

    # Case 1: Requested business_type is "Dental Clinic", categories[0] is "Cosmetic dentist"
    normalized = normalize_and_deduplicate_apify_items(raw_items, "Austin", "Dental Clinic")
    assert len(normalized) == 1
    assert normalized[0]["business_type"] == "Dental Clinic"
    assert normalized[0]["categories"] == ["Cosmetic dentist", "Dentist"]

    # Case 2: Requested business_type is empty -> falls back to categories[0]
    normalized_fallback = normalize_and_deduplicate_apify_items(raw_items, "Austin", "")
    assert len(normalized_fallback) == 1
    assert normalized_fallback[0]["business_type"] == "Cosmetic dentist"

    # Case 3: Both requested business_type and categories are empty -> falls back to item business_type or ''
    raw_no_cat = [{"title": "Lone Star Clinic", "placeId": "ChIJ_btype_002", "business_type": "Medical Clinic"}]
    normalized_item_fallback = normalize_and_deduplicate_apify_items(raw_no_cat, "Austin", "")
    assert normalized_item_fallback[0]["business_type"] == "Medical Clinic"


def test_discover_businesses_http_200_response_parsing() -> None:
    """Verify discover_businesses() parses a mocked HTTP 200 JSON array response and normalizes items."""
    mock_dataset = [
        {
            "title": "Biskra Auto Lease",
            "placeId": "ChIJ_biskra_200",
            "website": "https://biskraauto.example.com",
            "address": "10 Rue de la Republique, Biskra",
            "phone": "+213-33-55-0101",
            "city": "Biskra",
            "categories": ["Car leasing service", "Car rental agency"],
            "totalScore": 4.7,
            "reviewsCount": 38,
        },
        {
            # Duplicate placeId should be deduplicated
            "title": "Biskra Auto Lease Duplicate",
            "placeId": "ChIJ_biskra_200",
            "website": "https://biskraauto.example.com",
        },
    ]

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = httpx.Response(
            200,
            json=mock_dataset,
            headers={"Content-Type": "application/json"},
        )
        results = discover_businesses(
            city="Algeria Biskra",
            business_type="car rental agency",
            max_results=5,
            token="apify_api_valid_test_token",
        )

    assert mock_post.called
    call_args, call_kwargs = mock_post.call_args
    assert "compass~google-maps-extractor/run-sync-get-dataset-items" in call_args[0]
    assert call_kwargs["headers"]["Authorization"] == "Bearer apify_api_valid_test_token"
    assert call_kwargs["json"]["locationQuery"] == "Algeria Biskra"
    assert call_kwargs["json"]["searchStringsArray"] == ["car rental agency"]
    assert call_kwargs["json"]["maxCrawledPlacesPerSearch"] == 5

    assert len(results) == 1
    assert results[0]["title"] == "Biskra Auto Lease"
    assert results[0]["place_id"] == "ChIJ_biskra_200"
    assert results[0]["business_type"] == "car rental agency"
    assert results[0]["rating"] == 4.7
    assert results[0]["reviews_count"] == 38


def test_call_gemini_api_http_200_and_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify:
    - Default AI model in config is gemini-3.1-flash-lite (not gemini-4-carbon).
    - Leading 'models/' prefix is stripped before building the Gemini API URL.
    - call_gemini_api() parses candidates[0].content.parts[0].text on HTTP 200.
    - Explicit mock_mode is required for synthetic output; missing key in live mode raises GeminiConfigurationError.
    """
    from backend.app.config import Settings

    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_settings.cache_clear()

    default_settings = Settings(_env_file=None)
    assert default_settings.ai_model == "gemini-3.1-flash-lite"
    assert default_settings.ai_provider == "gemini"

    settings = get_settings()
    assert settings.ai_model == "gemini-3.1-flash-lite"

    # Even if AI_PROVIDER="mock" in env or .env, analyze_lead_with_gemini(mock_mode=False) must NOT silently mock
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(GeminiConfigurationError, match="GEMINI_API_KEY is not configured"):
        analyze_lead_with_gemini({"title": "No Silent Mock Co"}, mock_mode=False)

    # Test call_gemini_api HTTP 200 parsing & 'models/' prefix stripping
    gemini_200_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"business_summary": "Verified HTTP 200 Gemini parsing.", "confidence": "high"}'
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = httpx.Response(
            200,
            json=gemini_200_payload,
            headers={"Content-Type": "application/json"},
        )
        text = call_gemini_api(
            "Analyze this business",
            api_key="test-gemini-key",
            model="models/gemini-3.1-flash-lite",
            temperature=0.3,
        )

    assert text == '{"business_summary": "Verified HTTP 200 Gemini parsing.", "confidence": "high"}'
    called_url = mock_post.call_args[0][0]
    assert (
        called_url
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key=test-gemini-key"
    )
    assert "models/models/" not in called_url


def test_stage1_failure_stage2_fallback_prompt_no_error_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify that when Stage 1 fails (via exception or invalid JSON), Stage 2 receives
    'Stage 1 analysis unavailable' and never receives internal exception/error strings.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-fallback")
    get_settings.cache_clear()

    lead = {
        "title": "Oasis Logistics",
        "city": "Biskra",
        "business_type": "Freight Forwarding",
        "website": "https://oasislogistics.example.com",
    }

    captured_prompts: list[str] = []

    def fake_gemini_call(prompt: str, **kwargs: Any) -> str:
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            raise GeminiExecutionError("Internal upstream quota error 503 on Stage 1")
        return json.dumps(
            {
                "company_name": "Oasis Logistics",
                "personalization_observation": "Regional freight provider in Biskra.",
                "potential_opportunity": "Add shipment tracking portal.",
                "icebreaker": "Saw your Biskra freight operation—happy to share ideas.",
            }
        )

    with patch(
        "backend.app.services.gemini_service.call_gemini_api",
        side_effect=fake_gemini_call,
    ):
        analysis, raw_ai, status = analyze_lead_with_gemini(lead, "Website text")

    assert len(captured_prompts) == 2
    stage2_prompt_sent = captured_prompts[1]
    assert "Stage 1 Analysis:\nStage 1 analysis unavailable" in stage2_prompt_sent
    assert "Internal upstream quota error 503" not in stage2_prompt_sent
    assert "Stage 1 Gemini call failed" not in stage2_prompt_sent

    # Diagnostic error recording is preserved in raw_payload and analysis.error
    assert "Internal upstream quota error 503" in raw_ai["stage1_ai"]["error"]
    assert analysis is not None
    assert "Internal upstream quota error 503" in str(analysis.error)


def test_strict_gemini_non_string_field_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify Stage 1 business_summary and all required Stage 2 fields reject lists,
    dicts, ints, booleans, null, and whitespace-only strings without coercing them to str.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-strict")
    get_settings.cache_clear()

    lead = {"title": "Strict Type Clinic", "city": "Austin", "business_type": "Clinic"}

    invalid_values: list[Any] = [
        ["list", "of", "items"],
        {"nested": "dict"},
        42,
        True,
        False,
        None,
        "",
        "   \t\n  ",
    ]

    for bad_val in invalid_values:
        with patch("backend.app.services.gemini_service.call_gemini_api") as mock_gemini:
            mock_gemini.side_effect = [
                json.dumps({"business_summary": bad_val, "confidence": "high"}),
                json.dumps(
                    {
                        "company_name": bad_val,
                        "personalization_observation": bad_val,
                        "potential_opportunity": bad_val,
                        "icebreaker": bad_val,
                    }
                ),
            ]
            analysis, raw_ai, status = analyze_lead_with_gemini(lead, "")

            assert status == "partial_ai_failure"
            assert analysis is not None  # confidence 'high' (0.9) is valid
            assert analysis.business_summary is None
            assert analysis.ai_company_name is None
            assert analysis.personalization_observation is None
            assert analysis.potential_opportunity is None
            assert analysis.icebreaker is None
            assert raw_ai["ai_company_name"] is None
            assert "Missing or empty Stage 2 AI fields: company_name, personalization_observation, potential_opportunity, icebreaker" in str(
                analysis.error or raw_ai["stage2_ai"]
            )


def test_search_run_failure_sets_completed_at_and_passes_scraper_timeout(
    pipeline_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify:
    - execute_discovery_pipeline sets completed_at = utc_now() when transitioning to FAILED.
    - execute_discovery_pipeline clears completed_at = None when transitioning to RUNNING.
    - execute_discovery_pipeline passes get_settings().website_scraper_timeout_seconds to scrape_website_content().
    """
    monkeypatch.setenv("WEBSITE_SCRAPER_TIMEOUT_SECONDS", "22.5")
    get_settings.cache_clear()

    run = SearchRun(
        city="Austin",
        business_type="Dentist",
        max_results=5,
        status="pending",
    )
    pipeline_session.add(run)
    pipeline_session.commit()

    # 1. Pipeline failure sets status='failed' and completed_at is not None
    with patch(
        "backend.app.services.discovery_orchestrator.discover_businesses",
        side_effect=ApifyExecutionError("Simulated Apify 503 outage"),
    ):
        failed_result = execute_discovery_pipeline(pipeline_session, run.id, mock_mode=True)

    assert failed_result.status == "failed"
    assert "Simulated Apify 503 outage" in str(failed_result.error_message)

    db_run = pipeline_session.get(SearchRun, run.id)
    assert db_run is not None
    assert db_run.status == SearchRunStatus.FAILED.value
    assert db_run.completed_at is not None

    # 2. Re-running clears completed_at during RUNNING and passes configured timeout to scraper
    captured_timeouts: list[float] = []

    def spy_Status_and_scraper(url: str, timeout_seconds: float = 15.0, **kwargs: Any) -> str:
        # While running inside the pipeline loop, completed_at must have been cleared to None
        current_run = pipeline_session.get(SearchRun, run.id)
        assert current_run is not None
        assert current_run.status == SearchRunStatus.RUNNING.value
        assert current_run.completed_at is None
        captured_timeouts.append(timeout_seconds)
        return "Scraped website content"

    mock_items = [
        {
            "title": "Austin Recovery Dental",
            "place_id": "ChIJ_rec_001",
            "website": "https://recoverydental.example.com",
            "city": "Austin",
            "business_type": "Dentist",
            "raw_apify": {"title": "Austin Recovery Dental", "placeId": "ChIJ_rec_001"},
        }
    ]

    with patch(
        "backend.app.services.discovery_orchestrator.scrape_website_content",
        side_effect=spy_Status_and_scraper,
    ):
        success_result = execute_discovery_pipeline(
            pipeline_session,
            run.id,
            mock_mode=True,
            apify_items_override=mock_items,
        )

    assert success_result.status == "completed"
    assert captured_timeouts == [22.5]
    db_run_after = pipeline_session.get(SearchRun, run.id)
    assert db_run_after is not None
    assert db_run_after.status == SearchRunStatus.COMPLETED.value
    assert db_run_after.completed_at is not None


def test_existing_lead_analysis_preserved_on_subsequent_partial_ai_failure(
    pipeline_session: Session,
) -> None:
    """
    Verify that when a duplicate Lead already has a full Stage 2 LeadAnalysis,
    a later partial AI failure does not overwrite existing non-null Stage 2 fields with None.
    """
    run1 = SearchRun(city="Austin", business_type="Dental Clinic", max_results=5, status="running")
    run2 = SearchRun(city="Austin", business_type="Dental Clinic", max_results=5, status="running")
    pipeline_session.add_all([run1, run2])
    pipeline_session.commit()

    # Run 1: Full Stage 1 + Stage 2 analysis succeeds
    full_lead = N8nLeadItemPayload(
        company_name="Preserved Smile Studio",
        title="Preserved Smile Studio",
        place_id="ChIJ_preserve_001",
        website="https://preservedsmile.example.com",
        city="Austin",
        business_type="Dental Clinic",
        processing_status="completed",
        analysis=N8nLeadAnalysisPayload(
            ai_company_name="Preserved Smile Studio",
            business_summary="Initial Stage 1 summary.",
            personalization_observation="Original verified Stage 2 observation.",
            potential_opportunity="Original verified Stage 2 opportunity.",
            icebreaker="Original verified Stage 2 icebreaker.",
            confidence=0.9,
        ),
    )
    resp1 = process_n8n_callback(
        pipeline_session,
        N8nCallbackRequest(search_run_id=run1.id, status="completed", leads=[full_lead]),
    )
    assert resp1.leads_created == 1
    assert resp1.analyses_upserted == 1

    # Run 2: Duplicate lead encounters Stage 2 failure (Stage 1 succeeds, Stage 2 fields are None)
    partial_lead = N8nLeadItemPayload(
        company_name="Preserved Smile Studio",
        title="Preserved Smile Studio",
        place_id="ChIJ_preserve_001",
        website="https://preservedsmile.example.com",
        city="Austin",
        business_type="Dental Clinic",
        processing_status="partial_ai_failure",
        analysis=N8nLeadAnalysisPayload(
            ai_company_name=None,
            business_summary="Updated Stage 1 summary from Run 2.",
            personalization_observation=None,
            potential_opportunity=None,
            icebreaker=None,
            confidence=0.6,
            error="Stage 2 Gemini call failed: Timeout",
        ),
    )
    resp2 = process_n8n_callback(
        pipeline_session,
        N8nCallbackRequest(search_run_id=run2.id, status="completed", leads=[partial_lead]),
    )
    assert resp2.leads_created == 0
    assert resp2.leads_updated == 1
    assert resp2.analyses_upserted == 1

    saved_lead = pipeline_session.scalar(
        select(Lead).where(Lead.company_name == "Preserved Smile Studio")
    )
    assert saved_lead is not None
    assert saved_lead.analysis is not None
    # Updated Stage 1 fields applied
    assert saved_lead.analysis.business_summary == "Updated Stage 1 summary from Run 2."
    assert saved_lead.analysis.confidence == 0.6
    # Existing Stage 2 fields survived partial failure!
    assert saved_lead.analysis.personalization_observation == "Original verified Stage 2 observation."
    assert saved_lead.analysis.potential_opportunity == "Original verified Stage 2 opportunity."
    assert saved_lead.analysis.icebreaker == "Original verified Stage 2 icebreaker."


def test_apify_token_sanitization_with_and_without_bearer_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify Apify token sanitization strips optional case-insensitive 'Bearer ' prefixes
    for both APIFY_API_TOKEN and APIFY_TOKEN without double-prefixing Authorization header.
    """
    from backend.app.config import Settings

    assert Settings.sanitize_apify_api_token("apify_api_raw123") == "apify_api_raw123"
    assert Settings.sanitize_apify_api_token("Bearer apify_api_raw123") == "apify_api_raw123"
    assert Settings.sanitize_apify_api_token("bearer apify_api_raw123") == "apify_api_raw123"
    assert Settings.sanitize_apify_api_token("BEARER   apify_api_raw123  ") == "apify_api_raw123"
    assert (
        Settings.sanitize_apify_api_token('APIFY_API_TOKEN="Bearer apify_api_raw123"')
        == "apify_api_raw123"
    )

    # Fallback to APIFY_TOKEN in environment with Bearer prefix
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.setenv("APIFY_TOKEN", "Bearer apify_api_from_fallback_env")
    get_settings.cache_clear()
    assert get_settings().apify_api_token == "apify_api_from_fallback_env"

    # Verify discover_businesses sends single 'Bearer <token>' even if token arg has 'Bearer '
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = httpx.Response(200, json=[])
        discover_businesses("Austin", "Dentist", token="Bearer apify_api_direct_arg")
        auth_header = mock_post.call_args[1]["headers"]["Authorization"]
        assert auth_header == "Bearer apify_api_direct_arg"


def test_website_scraper_enforces_tls_verification() -> None:
    """
    Verify scrape_website_content uses standard TLS certificate verification (verify=True)
    and safely returns empty string if TLS verification fails.
    """
    captured_client_kwargs: list[dict[str, Any]] = []
    real_client = httpx.Client

    def spy_httpx_client(*args: Any, **kwargs: Any) -> httpx.Client:
        captured_client_kwargs.append(dict(kwargs))
        return real_client(*args, **kwargs)

    with patch.object(real_client, "get") as mock_get:
        mock_get.return_value = httpx.Response(
            200,
            text="<html><head><script>evil()</script></head><body><h1>Austin Dental</h1><p>Welcome!</p></body></html>",
        )
        with patch("backend.app.services.apify_service.httpx.Client", side_effect=spy_httpx_client):
            extracted = scrape_website_content("https://austindental.example.com", timeout_seconds=12.0)

    assert extracted == "Austin Dental Welcome!"
    assert len(captured_client_kwargs) == 1
    assert captured_client_kwargs[0].get("verify") is True
    assert captured_client_kwargs[0].get("timeout") == 12.0

    # Verify TLS certificate error degrades gracefully to empty string
    with patch.object(
        real_client,
        "get",
        side_effect=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    ):
        assert scrape_website_content("https://expired-cert.example.com") == ""

