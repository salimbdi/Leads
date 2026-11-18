import json
import logging
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, status
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.config import get_settings
from backend.app.db.base import utc_now
from backend.app.models.lead import Lead
from backend.app.models.lead_analysis import LeadAnalysis
from backend.app.models.search_run import SearchRun, SearchRunStatus
from backend.app.models.search_run_lead import SearchRunLead
from backend.app.schemas.n8n import (
    N8nCallbackRequest,
    N8nCallbackResponse,
    N8nLeadItemPayload,
    N8nWebhookOutboundPayload,
)

logger = logging.getLogger(__name__)


def normalize_text_key(value: Optional[str]) -> Optional[str]:
    """Normalize a string for deterministic case-insensitive matching."""
    if not value or not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().lower().split())
    return cleaned if cleaned else None


def normalize_website_key(url: Optional[str]) -> Optional[str]:
    """Normalize a website URL (hostname without www. + path without trailing slash) for deduplication."""
    if not url or not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").strip().lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname or "." not in hostname:
        return None
    path = (parsed.path or "").rstrip("/").lower()
    return f"{hostname}{path}"


def parse_raw_payload_dict(raw_payload: Any) -> dict[str, Any]:
    """Parse raw_payload (JSON string or dict) into a dictionary safely."""
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    if isinstance(raw_payload, str) and raw_payload.strip():
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return {"raw_text": raw_payload}
    return {}


def extract_place_id_from_dict(data: dict[str, Any]) -> Optional[str]:
    """Extract Apify Google Maps placeId from a raw payload dictionary."""
    apify_block = data.get("apify")
    if isinstance(apify_block, dict):
        candidate = apify_block.get("placeId") or apify_block.get("place_id")
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    top_candidate = data.get("placeId") or data.get("place_id")
    if top_candidate and str(top_candidate).strip():
        return str(top_candidate).strip()
    return None


def extract_item_place_id(item: N8nLeadItemPayload, raw_dict: dict[str, Any]) -> Optional[str]:
    """Extract Apify placeId from a callback lead item or its nested raw_payload."""
    if item.place_id and item.place_id.strip():
        return item.place_id.strip()
    if item.placeId and item.placeId.strip():
        return item.placeId.strip()
    return extract_place_id_from_dict(raw_dict)


def build_preserved_raw_payload(
    item: N8nLeadItemPayload,
    *,
    search_run_id: int,
    batch_index: int,
) -> dict[str, Any]:
    """Build structured raw_payload preserving original Apify data and separate Stage 1 & Stage 2 AI outputs."""
    incoming_raw = parse_raw_payload_dict(item.raw_payload)

    existing_apify = incoming_raw.get("apify")
    if isinstance(existing_apify, dict) and existing_apify:
        apify_record = dict(existing_apify)
        if "title" not in apify_record or not apify_record.get("title"):
            apify_record["title"] = item.company_name
    else:
        apify_record = {
            "title": item.title or item.company_name,
            "placeId": item.place_id or item.placeId or incoming_raw.get("placeId"),
            "website": item.website,
            "address": item.address,
            "phone": item.phone,
            "city": item.city,
            "categories": item.categories or ([item.business_type] if item.business_type else []),
            "totalScore": item.totalScore if item.totalScore is not None else item.rating,
            "reviewsCount": item.reviewsCount if item.reviewsCount is not None else item.reviews_count,
        }
        # Preserve any extra Apify keys sent at top level of incoming_raw
        for k, v in incoming_raw.items():
            if k not in {"apify", "stage1_ai", "stage2_ai", "ai_company_name", "processing_status"}:
                apify_record.setdefault(k, v)

    stage1_ai = (
        incoming_raw.get("stage1_ai")
        or (item.analysis.stage1_analysis if item.analysis else None)
        or item.stage1_ai
    )
    stage2_ai = (
        incoming_raw.get("stage2_ai")
        or (item.analysis.stage2_analysis if item.analysis else None)
        or item.stage2_ai
    )
    ai_company_name = (
        (item.analysis.ai_company_name if item.analysis else None)
        or item.ai_company_name
        or incoming_raw.get("ai_company_name")
        or (stage2_ai.get("company_name") if isinstance(stage2_ai, dict) else None)
    )

    return {
        "apify": apify_record,
        "stage1_ai": stage1_ai,
        "stage2_ai": stage2_ai,
        "ai_company_name": ai_company_name,
        "processing_status": item.processing_status,
        "_last_search_run_id": search_run_id,
        "_last_batch_index": batch_index,
    }


def find_existing_lead_for_dedup(
    existing_leads: list[Lead],
    *,
    place_id: Optional[str],
    website_key: Optional[str],
    norm_company_name: Optional[str],
    norm_address: Optional[str],
    search_run_id: int,
    batch_index: int,
    linked_lead_ids_for_run: set[int],
) -> Optional[Lead]:
    """
    Find an existing Lead using strict deduplication priority:
    1. Apify placeId
    2. Normalized website
    3. Exact normalized company_name + full address
    4. If no reliable key exists, retain as a separate record (except on exact callback retry of the same search_run_id + batch_index).
    """
    # Priority 1: Apify placeId
    if place_id:
        norm_place_id = place_id.strip().lower()
        for lead in existing_leads:
            lead_raw = parse_raw_payload_dict(lead.raw_payload)
            lead_place_id = extract_place_id_from_dict(lead_raw)
            if lead_place_id and lead_place_id.lower() == norm_place_id:
                return lead

    # Priority 2: Normalized website
    if website_key:
        for lead in existing_leads:
            if normalize_website_key(lead.website) == website_key:
                return lead

    # Priority 3: Exact normalized company_name + full address
    if norm_company_name and norm_address:
        for lead in existing_leads:
            if (
                normalize_text_key(lead.company_name) == norm_company_name
                and normalize_text_key(lead.address) == norm_address
            ):
                return lead

    # Priority 4: No reliable key exists (no placeId, no website, no address).
    # Retain the record across different businesses/searches, but ensure idempotent retry
    # if the exact same callback batch for this search_run_id is delivered more than once.
    if not place_id and not website_key and not norm_address and norm_company_name:
        for lead in existing_leads:
            if lead.id is not None and lead.id in linked_lead_ids_for_run:
                lead_raw = parse_raw_payload_dict(lead.raw_payload)
                if (
                    lead_raw.get("_last_search_run_id") == search_run_id
                    and lead_raw.get("_last_batch_index") == batch_index
                    and normalize_text_key(lead.company_name) == norm_company_name
                ):
                    return lead

    return None


def dispatch_webhook_post(
    webhook_url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> httpx.Response:
    """Send an outbound HTTP POST request to the configured n8n webhook."""
    with httpx.Client(timeout=timeout_seconds) as client:
        return client.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )


def trigger_n8n_search_run(db: Session, search_run: SearchRun) -> SearchRun:
    """
    Dispatch a SearchRun to the configured n8n Webhook URL.
    - If N8N_WEBHOOK_URL is empty, leaves SearchRun in 'pending' status.
    - Validates hybrid URL constraints before dispatching.
    - Transitions SearchRun to 'running' on 2xx or 'failed' on network/validation/HTTP errors.
    """
    settings = get_settings()
    webhook_url = settings.n8n_webhook_url.strip()
    if not webhook_url:
        return search_run

    webhook_ok, webhook_err = settings.validate_webhook_url()
    callback_url = settings.get_trusted_callback_url()
    callback_ok, callback_err = settings.validate_callback_url_for_hybrid(callback_url)

    if not webhook_ok or not callback_ok:
        reason = webhook_err or callback_err or "Invalid n8n webhook or callback configuration."
        logger.error(
            "SearchRun id=%s failed pre-dispatch URL validation: %s",
            search_run.id,
            reason,
        )
        search_run.status = SearchRunStatus.FAILED.value
        search_run.completed_at = utc_now()
        db.add(search_run)
        db.commit()
        db.refresh(search_run)
        return search_run

    outbound_payload = N8nWebhookOutboundPayload(
        search_run_id=search_run.id,
        city=search_run.city,
        business_type=search_run.business_type,
        max_results=search_run.max_results,
        callback_url=callback_url,
    )

    # Mark as running prior to network call so synchronous callbacks see 'running'
    search_run.status = SearchRunStatus.RUNNING.value
    search_run.completed_at = None
    db.add(search_run)
    db.commit()
    db.refresh(search_run)

    try:
        response = dispatch_webhook_post(
            webhook_url=webhook_url,
            payload=outbound_payload.model_dump(),
            timeout_seconds=settings.n8n_request_timeout_seconds,
        )
        if 200 <= response.status_code < 300:
            db.refresh(search_run)
            return search_run

        logger.error(
            "n8n webhook returned non-2xx status %s for SearchRun id=%s",
            response.status_code,
            search_run.id,
        )
        search_run.status = SearchRunStatus.FAILED.value
        search_run.completed_at = utc_now()
        db.add(search_run)
        db.commit()
        db.refresh(search_run)
        return search_run

    except httpx.HTTPError as exc:
        logger.error(
            "Failed to dispatch SearchRun id=%s to n8n webhook (%s)",
            search_run.id,
            exc.__class__.__name__,
        )
        search_run.status = SearchRunStatus.FAILED.value
        search_run.completed_at = utc_now()
        db.add(search_run)
        db.commit()
        db.refresh(search_run)
        return search_run



def process_n8n_callback(db: Session, payload: N8nCallbackRequest) -> N8nCallbackResponse:
    """
    Process an authenticated n8n batch callback in a single atomic database transaction.
    - Deduplicates leads using priority: Apify placeId -> normalized website -> normalized company_name + address.
    - Preserves original Apify data and Stage 1 / Stage 2 AI outputs in raw_payload.
    - Always uses original Apify title for Lead.company_name.
    - Rolls back the entire transaction if any unexpected persistence error occurs.
    """
    stmt_run = (
        select(SearchRun)
        .where(SearchRun.id == payload.search_run_id)
        .options(selectinload(SearchRun.search_run_leads))
    )
    search_run = db.scalar(stmt_run)
    if search_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SearchRun with id {payload.search_run_id} not found",
        )

    try:
        if payload.status == "failed":
            search_run.status = SearchRunStatus.FAILED.value
            search_run.completed_at = utc_now()
            db.add(search_run)
            db.commit()
            return N8nCallbackResponse(
                search_run_id=search_run.id,
                status=search_run.status,
                leads_received=len(payload.leads),
                leads_created=0,
                leads_updated=0,
                leads_linked=0,
                analyses_upserted=0,
            )

        existing_leads: list[Lead] = list(
            db.scalars(select(Lead).options(selectinload(Lead.analysis))).all()
        )
        linked_lead_ids_for_run: set[int] = {
            link.lead_id for link in search_run.search_run_leads
        }

        leads_created = 0
        leads_updated = 0
        leads_linked = 0
        analyses_upserted = 0

        for idx, item in enumerate(payload.leads):
            structured_raw = build_preserved_raw_payload(
                item,
                search_run_id=search_run.id,
                batch_index=idx,
            )
            serialized_raw = json.dumps(structured_raw, ensure_ascii=False)

            place_id = extract_item_place_id(item, structured_raw)
            website_clean = item.website.strip() if item.website and item.website.strip() else None
            address_clean = item.address.strip() if item.address and item.address.strip() else None
            phone_clean = item.phone.strip() if item.phone and item.phone.strip() else None

            city_clean = (
                item.city.strip()
                if item.city and item.city.strip()
                else search_run.city
            )
            business_type_clean = (
                item.business_type.strip()
                if item.business_type and item.business_type.strip()
                else (
                    item.categories[0].strip()
                    if item.categories and len(item.categories) > 0 and str(item.categories[0]).strip()
                    else search_run.business_type
                )
            )

            # Canonical company name is strictly the Apify title (never overwritten by AI company_name)
            canonical_company_name = str(
                structured_raw["apify"].get("title") or item.company_name
            ).strip()

            website_key = normalize_website_key(website_clean)
            norm_company_name = normalize_text_key(canonical_company_name)
            norm_address = normalize_text_key(address_clean)

            matched_lead = find_existing_lead_for_dedup(
                existing_leads,
                place_id=place_id,
                website_key=website_key,
                norm_company_name=norm_company_name,
                norm_address=norm_address,
                search_run_id=search_run.id,
                batch_index=idx,
                linked_lead_ids_for_run=linked_lead_ids_for_run,
            )

            if matched_lead is None:
                lead_record = Lead(
                    company_name=canonical_company_name,
                    website=website_clean,
                    address=address_clean,
                    phone=phone_clean,
                    city=city_clean,
                    business_type=business_type_clean,
                    rating=item.rating,
                    reviews_count=item.reviews_count,
                    raw_payload=serialized_raw,
                )
                db.add(lead_record)
                db.flush()
                existing_leads.append(lead_record)
                leads_created += 1
            else:
                lead_record = matched_lead
                lead_record.company_name = canonical_company_name
                if website_clean is not None:
                    lead_record.website = website_clean
                if address_clean is not None:
                    lead_record.address = address_clean
                if phone_clean is not None:
                    lead_record.phone = phone_clean
                lead_record.city = city_clean
                lead_record.business_type = business_type_clean
                if item.rating is not None:
                    lead_record.rating = item.rating
                if item.reviews_count is not None:
                    lead_record.reviews_count = item.reviews_count
                lead_record.raw_payload = serialized_raw
                db.add(lead_record)
                db.flush()
                leads_updated += 1

            # Upsert LeadAnalysis only when AI analysis produced valid content
            if item.analysis is not None and item.analysis.has_meaningful_content():
                if lead_record.analysis is None:
                    analysis_record = LeadAnalysis(
                        lead_id=lead_record.id,
                        business_summary=item.analysis.business_summary,
                        personalization_observation=item.analysis.personalization_observation,
                        potential_opportunity=item.analysis.potential_opportunity,
                        icebreaker=item.analysis.icebreaker,
                        confidence=item.analysis.confidence,
                    )
                    db.add(analysis_record)
                    lead_record.analysis = analysis_record
                else:
                    analysis_record = lead_record.analysis
                    analysis_record.business_summary = item.analysis.business_summary
                    analysis_record.personalization_observation = (
                        item.analysis.personalization_observation
                    )
                    analysis_record.potential_opportunity = item.analysis.potential_opportunity
                    analysis_record.icebreaker = item.analysis.icebreaker
                    analysis_record.confidence = item.analysis.confidence
                    db.add(analysis_record)
                db.flush()
                analyses_upserted += 1

            # Recompute and persist deterministic Lead score after Lead & LeadAnalysis upsert
            from backend.app.services.scoring_service import apply_lead_scoring

            apply_lead_scoring(lead_record)
            db.add(lead_record)
            db.flush()

            # Link Lead to SearchRun idempotently
            if lead_record.id not in linked_lead_ids_for_run:
                link = SearchRunLead(
                    search_run_id=search_run.id,
                    lead_id=lead_record.id,
                )
                db.add(link)
                db.flush()
                linked_lead_ids_for_run.add(lead_record.id)
                leads_linked += 1

        search_run.status = SearchRunStatus.COMPLETED.value
        search_run.completed_at = utc_now()
        db.add(search_run)
        db.commit()

        return N8nCallbackResponse(
            search_run_id=search_run.id,
            status=search_run.status,
            leads_received=len(payload.leads),
            leads_created=leads_created,
            leads_updated=leads_updated,
            leads_linked=leads_linked,
            analyses_upserted=analyses_upserted,
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Transaction rolled back while processing callback for SearchRun id=%s: %s",
            payload.search_run_id,
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist callback batch; transaction rolled back.",
        ) from exc
