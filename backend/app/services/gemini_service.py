"""
Gemini Business Analysis & Personalization Service.

Direct Python translation of n8n workflow nodes:
- Basic LLM Chain (Stage 1: Business research & website analysis)
- Google Gemini Chat Model (Gemini model parameters & prompt execution)
- Aggregate & Message a model (Stage 2: Personalization, opportunity, icebreaker)
- Code in JavaScript1 (Safe parsing of Stage 1 & Stage 2 outputs, confidence mapping, status)
"""

import json
import logging
import re
from typing import Any, Optional

import httpx

from backend.app.config import get_settings
from backend.app.schemas.n8n import (
    CONFIDENCE_LABEL_MAP,
    N8nLeadAnalysisPayload,
    normalize_confidence_value,
)

logger = logging.getLogger(__name__)

GEMINI_API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


class GeminiConfigurationError(Exception):
    """Raised when GEMINI_API_KEY is missing or invalid."""


class GeminiExecutionError(Exception):
    """Raised when a Gemini API call or response generation fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def strip_markdown_json(raw: Optional[str]) -> str:
    """Strip markdown code fence syntax (```json ... ```) from LLM output."""
    if not raw or not isinstance(raw, str):
        return ""
    trimmed = raw.strip()
    fenced = re.sub(r"^```(?:json)?\s*", "", trimmed, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced, flags=re.IGNORECASE).strip()
    first_brace = fenced.find("{")
    last_brace = fenced.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return fenced[first_brace : last_brace + 1]
    return fenced


def parse_structured_or_text_json(candidate: Any) -> Optional[dict[str, Any]]:
    """
    Safely parse candidate data into a dictionary, supporting both direct dicts,
    text JSON, markdown-fenced JSON, and nested lists.
    Translates n8n parseStructuredOrTextJson function.
    """
    if candidate is None:
        return None
    if isinstance(candidate, dict):
        return dict(candidate)
    if isinstance(candidate, str):
        cleaned = strip_markdown_json(candidate)
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, json.JSONDecodeError):
            return None
    if isinstance(candidate, list):
        for elem in candidate:
            parsed = parse_structured_or_text_json(elem)
            if parsed is not None:
                return parsed
    return None


def build_stage1_prompt(
    business_data: dict[str, Any],
    website_content: str = "",
) -> str:
    """
    Build Stage 1 business research prompt matching n8n 'Basic LLM Chain'.
    """
    title = business_data.get("title") or business_data.get("company_name") or "Unknown"
    categories = business_data.get("categories") or [business_data.get("business_type", "Unknown")]
    city = business_data.get("city") or "Unknown"
    address = business_data.get("address") or "Unknown"
    website = business_data.get("website") or "No website"
    phone = business_data.get("phone") or "Unknown"
    rating = business_data.get("rating")
    reviews_count = business_data.get("reviews_count") or 0
    content = website_content.strip() or "No website content available"

    return f"""You are a business research analyst working for LeadForge.

Analyze the business using only the information provided below.
Do not invent facts. If information is unavailable, return null.

BUSINESS INFORMATION:
Name: {title}
Category: {json.dumps(categories)}
City: {city}
Address: {address}
Website: {website}
Phone: {phone}
Google Rating: {rating if rating is not None else 'null'}
Reviews Count: {reviews_count}

WEBSITE CONTENT:
{content}

Analyze the business and return ONLY valid JSON using this structure:

{{
  "business_summary": "Brief factual description",
  "services": [],
  "website_status": "active | missing | inaccessible",
  "website_observations": [],
  "potential_digital_needs": [],
  "opportunity_reasons": [],
  "evidence": [],
  "confidence": "low | medium | high"
}}

Rules:
- Do not assume that a missing website means the business needs one.
- Distinguish observed facts from possible opportunities.
- Do not invent website problems.
- Keep the analysis concise and evidence-based.
"""


def _extract_non_empty_string(
    obj: Optional[dict[str, Any]],
    key: str,
) -> Optional[str]:
    """
    Validate and extract a required string field matching n8n type validation:
    - Must be an actual Python str (rejects list, dict, int, float, bool, None).
    - Must contain non-whitespace text.
    """
    if not isinstance(obj, dict):
        return None
    value = obj.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def build_stage2_prompt(
    business_data: dict[str, Any],
    stage1_analysis: Any,
) -> str:
    """
    Build Stage 2 personalization prompt matching n8n 'Message a model'.
    """
    title = business_data.get("title") or business_data.get("company_name") or "Unknown"
    city = business_data.get("city") or "Unknown"
    website = business_data.get("website") or "None"

    if isinstance(stage1_analysis, dict) and stage1_analysis:
        stage1_str = json.dumps(stage1_analysis, indent=2)
    elif isinstance(stage1_analysis, str) and stage1_analysis.strip():
        stage1_str = stage1_analysis.strip()
    else:
        stage1_str = "Stage 1 analysis unavailable"

    return f"""Read the following canonical business information and Stage 1 research analysis:

Canonical Business Name: {title}
City: {city}
Website: {website}
Stage 1 Analysis:
{stage1_str}

Extract and analyze the business information.

Generate exactly these four fields:

1. company_name: The actual business name.
2. personalization_observation: One specific, verifiable observation about the company's website or public profile.
3. potential_opportunity: One realistic digital improvement opportunity supported by the provided data.
4. icebreaker: A short, personalized outreach opening written from the perspective of an independent web development and automation service provider contacting this business.

Rules:
- Use only information present in the business data.
- Do not invent website problems or missing features.
- Do not speak as if you represent the target company.
- Do not begin with "Hello from [company name]".
- Address the business naturally.
- Do not claim that an existing feature is missing.
- Present opportunities as potential improvements, not confirmed problems.
- Keep the icebreaker professional, natural, and concise.
- Never return empty fields.
- If a detail is unavailable, use another supported observation or opportunity.

Return valid JSON with exactly these four keys:
{{
  "company_name": "string",
  "personalization_observation": "string",
  "potential_opportunity": "string",
  "icebreaker": "string"
}}
"""


def call_gemini_api(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.5,
    timeout_seconds: float = 45.0,
) -> str:
    """
    Execute a generateContent call against the Gemini REST API.
    """
    from backend.app.config import Settings

    settings = get_settings()
    raw_key = api_key if api_key is not None else (settings.gemini_api_key or "")
    effective_key = (
        Settings.sanitize_gemini_api_key(raw_key)
        if api_key is not None
        else str(raw_key).strip()
    )
    raw_model = model if model is not None else (settings.ai_model or DEFAULT_GEMINI_MODEL)
    effective_model = str(raw_model).strip()
    # Normalize model prefix if passed as models/...
    while effective_model.lower().startswith("models/"):
        effective_model = effective_model[7:].strip()
    if not effective_model or effective_model.lower() == "gemini-4-carbon":
        effective_model = DEFAULT_GEMINI_MODEL

    if not effective_key or effective_key == "MY_GEMINI_API_KEY":
        raise GeminiConfigurationError(
            "GEMINI_API_KEY is not configured. Set GEMINI_API_KEY in environment or configuration."
        )

    url = f"{GEMINI_API_URL_TEMPLATE.format(model=effective_model)}?key={effective_key}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "aistudio-build",
    }
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
        },
    }

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                raise GeminiExecutionError(
                    f"Gemini API returned HTTP {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )

            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise GeminiExecutionError("Gemini returned empty candidates list.")

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts:
                raise GeminiExecutionError("Gemini response candidate contains no parts.")

            part0 = parts[0]
            text = part0.get("text") or ""
            return text

    except httpx.TimeoutException as exc:
        raise GeminiExecutionError(
            f"Gemini API request timed out after {timeout_seconds}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise GeminiExecutionError(
            f"Gemini API network error: {exc.__class__.__name__}"
        ) from exc


def generate_mock_stage1_analysis(
    business_data: dict[str, Any],
    website_content: str = "",
) -> dict[str, Any]:
    """Generate deterministic mock Stage 1 analysis for tests and offline development."""
    title = business_data.get("title") or business_data.get("company_name") or "Local Business"
    has_website = bool(business_data.get("website"))
    has_content = bool(website_content.strip())

    return {
        "business_summary": f"Established local commercial operation: {title}.",
        "services": [business_data.get("business_type", "General Services")],
        "website_status": "active" if has_content else ("missing" if not has_website else "inaccessible"),
        "website_observations": [
            "Online presence active" if has_content else "No custom domain detected"
        ],
        "potential_digital_needs": [
            "Automated booking and patient/client intake portal",
            "Mobile-responsive web interface enhancement",
        ],
        "opportunity_reasons": [
            "High customer review volume with manual appointment booking process"
        ],
        "evidence": [f"{business_data.get('reviews_count', 0)} Google customer reviews"],
        "confidence": "high" if has_content or not has_website else "medium",
    }


def generate_mock_stage2_analysis(
    business_data: dict[str, Any],
    stage1_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Generate deterministic mock Stage 2 qualification for tests and offline development."""
    title = business_data.get("title") or business_data.get("company_name") or "Local Business"
    city = business_data.get("city") or "Local Area"
    btype = business_data.get("business_type") or "Service Provider"

    return {
        "company_name": title,
        "personalization_observation": f"Prominent {btype.lower()} serving the {city} community.",
        "potential_opportunity": (
            "Implement modern online scheduling, client portal, and automated SMS/email recall workflows."
        ),
        "icebreaker": (
            f"Noticed your {city} {btype.lower()} profile—we help local businesses streamline client booking and digital presence."
        ),
    }


def analyze_lead_with_gemini(
    business_data: dict[str, Any],
    website_content: str = "",
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    mock_mode: bool = False,
) -> tuple[Optional[N8nLeadAnalysisPayload], dict[str, Any], str]:
    """
    Execute two-stage business research and personalization analysis.
    Translates n8n:
    1. Basic LLM Chain (Stage 1 research)
    2. Message a model (Stage 2 personalization)
    3. Code in JavaScript1 (Safe parsing, confidence mapping, raw_payload assembly)

    Returns:
    - analysis_payload: N8nLeadAnalysisPayload or None
    - raw_payload_ai: dict containing stage1_ai, stage2_ai, ai_company_name, processing_status
    - processing_status: 'completed' | 'partial_ai_failure'
    """
    settings = get_settings()
    use_mock = bool(mock_mode)

    if not use_mock:
        raw_key = api_key if api_key is not None else (settings.gemini_api_key or "")
        effective_key = str(raw_key).strip()
        if not effective_key or effective_key == "MY_GEMINI_API_KEY":
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Set GEMINI_API_KEY in environment or configuration."
            )

    stage1_parsed: Optional[dict[str, Any]] = None
    stage1_err: Optional[str] = None
    stage2_parsed: Optional[dict[str, Any]] = None
    stage2_err: Optional[str] = None

    # Stage 1: Business Research & Website Analysis
    if use_mock:
        stage1_parsed = generate_mock_stage1_analysis(business_data, website_content)
    else:
        try:
            stage1_prompt = build_stage1_prompt(business_data, website_content)
            stage1_raw = call_gemini_api(
                stage1_prompt,
                api_key=api_key,
                model=model,
                temperature=0.3,
            )
            stage1_parsed = parse_structured_or_text_json(stage1_raw)
            if not stage1_parsed or not isinstance(stage1_parsed, dict):
                stage1_err = "Stage 1 AI output is not a valid JSON object"
                stage1_parsed = None
            elif _extract_non_empty_string(stage1_parsed, "business_summary") is None:
                stage1_err = "Missing or empty Stage 1 AI field: business_summary"
        except GeminiConfigurationError:
            raise
        except Exception as exc:
            stage1_err = f"Stage 1 Gemini call failed: {exc.__class__.__name__}: {str(exc)}"
            logger.warning("Stage 1 analysis failed: %s", stage1_err)

    # Stage 2: Personalization & Opportunity Extraction
    if use_mock:
        stage2_parsed = generate_mock_stage2_analysis(
            business_data, stage1_parsed or {}
        )
    else:
        try:
            stage1_for_stage2 = stage1_parsed if (stage1_parsed and not stage1_err) else None
            stage2_prompt = build_stage2_prompt(
                business_data, stage1_for_stage2
            )
            stage2_raw = call_gemini_api(
                stage2_prompt,
                api_key=api_key,
                model=model,
                temperature=0.5,
            )
            candidate = parse_structured_or_text_json(stage2_raw)
            if candidate and isinstance(candidate, dict):
                required_fields = [
                    "company_name",
                    "personalization_observation",
                    "potential_opportunity",
                    "icebreaker",
                ]
                missing = [
                    f
                    for f in required_fields
                    if _extract_non_empty_string(candidate, f) is None
                ]
                if missing:
                    stage2_err = f"Missing or empty Stage 2 AI fields: {', '.join(missing)}"
                stage2_parsed = candidate
            else:
                stage2_err = "Stage 2 Gemini response is not a valid JSON object"
        except GeminiConfigurationError:
            raise
        except Exception as exc:
            stage2_err = f"Stage 2 Gemini call failed: {exc.__class__.__name__}: {str(exc)}"
            logger.warning("Stage 2 analysis failed: %s", stage2_err)

    # Confidence extraction & mapping (low: 0.3, medium: 0.6, high: 0.9)
    raw_conf = (
        stage1_parsed.get("confidence")
        if isinstance(stage1_parsed, dict)
        else None
    )
    confidence_score: Optional[float] = None
    if raw_conf is not None and not isinstance(raw_conf, (bool, list, dict)):
        try:
            confidence_score = normalize_confidence_value(raw_conf)
        except ValueError:
            confidence_score = None

    business_summary = _extract_non_empty_string(stage1_parsed, "business_summary")
    ai_company_name = _extract_non_empty_string(stage2_parsed, "company_name")
    personalization_obs = _extract_non_empty_string(
        stage2_parsed, "personalization_observation"
    )
    potential_opp = _extract_non_empty_string(
        stage2_parsed, "potential_opportunity"
    )
    icebreaker = _extract_non_empty_string(stage2_parsed, "icebreaker")

    has_meaningful_analysis = any(
        [
            business_summary,
            personalization_obs,
            potential_opp,
            icebreaker,
            confidence_score is not None,
        ]
    )

    processing_status = (
        "completed"
        if (has_meaningful_analysis and not stage2_err)
        else "partial_ai_failure"
    )

    combined_error = "; ".join(e for e in (stage1_err, stage2_err) if e) or None

    analysis_payload: Optional[N8nLeadAnalysisPayload] = None
    if has_meaningful_analysis:
        analysis_payload = N8nLeadAnalysisPayload(
            ai_company_name=ai_company_name,
            business_summary=business_summary,
            personalization_observation=personalization_obs,
            potential_opportunity=potential_opp,
            icebreaker=icebreaker,
            confidence=confidence_score,
            stage1_analysis=stage1_parsed,
            stage2_analysis=stage2_parsed,
            error=combined_error,
        )

    raw_payload_ai = {
        "stage1_ai": stage1_parsed or ({"error": stage1_err} if stage1_err else None),
        "stage2_ai": stage2_parsed or ({"error": stage2_err} if stage2_err else None),
        "ai_company_name": ai_company_name,
        "processing_status": processing_status,
    }

    return analysis_payload, raw_payload_ai, processing_status
