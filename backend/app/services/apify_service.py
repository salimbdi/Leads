"""
Apify Google Maps Business Discovery & Website Scraping Service.

Direct Python translation of n8n workflow nodes:
- HTTP Request (Apify run-sync-get-dataset-items)
- Edit Fields (Apify field extraction)
- Code in JavaScript (Apify error checking, deduplication & normalization)
- IF - Has Website & scraper (Safe website content retrieval)
"""

import logging
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from backend.app.config import get_settings
from backend.app.services.n8n_service import normalize_text_key, normalize_website_key

logger = logging.getLogger(__name__)

APIFY_ACTOR_URL_TEMPLATE = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
DEFAULT_APIFY_ACTOR_ID = "compass~google-maps-extractor"


class ApifyConfigurationError(Exception):
    """Raised when Apify credentials or settings are missing or invalid."""


class ApifyExecutionError(Exception):
    """Raised when an Apify actor run or extraction fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_apify_google_maps_input(
    city: str,
    business_type: str,
    max_results: int = 20,
) -> dict[str, Any]:
    """
    Build the exact Apify actor input payload matching the n8n HTTP Request node.
    Actor: compass~google-maps-extractor.
    """
    safe_max = max(1, min(int(max_results), 500))
    clean_city = str(city or "").strip()
    clean_type = str(business_type or "").strip()

    return {
        "includeWebResults": False,
        "language": "en",
        "locationQuery": clean_city,
        "maxCrawledPlacesPerSearch": safe_max,
        "maximumLeadsEnrichmentRecords": 0,
        "scrapeContacts": False,
        "scrapeDirectories": False,
        "scrapePlaceDetailPage": False,
        "scrapeSocialMediaProfiles": {
            "facebooks": False,
            "instagrams": False,
            "tiktoks": False,
            "twitters": False,
            "youtubes": False,
        },
        "scrapeTableReservationProvider": False,
        "searchStringsArray": [clean_type],
        "skipClosedPlaces": False,
    }


def normalize_and_deduplicate_apify_items(
    items: list[dict[str, Any]],
    city: str,
    business_type: str,
) -> list[dict[str, Any]]:
    """
    Validate, normalize, and deduplicate raw items returned by Apify.
    Translates n8n 'Code in JavaScript':
    - Checks for HTTP status >= 400 or error objects in response items.
    - Preserves unmutated raw Apify record under raw_apify.
    - Deduplicates using priority: 1) placeId, 2) normalized website, 3) normalized name + address.
    """
    # 1. Error check: fail explicitly if Apify returned an error payload
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_apify = item.get("raw_apify") if isinstance(item.get("raw_apify"), dict) else {}
        status_val = (
            item.get("statusCode")
            or raw_apify.get("statusCode")
            or item.get("status")
            or raw_apify.get("status")
        )
        status_code = 0
        if status_val is not None:
            try:
                status_code = int(status_val)
            except (ValueError, TypeError):
                status_code = 0

        err_obj = (
            item.get("error")
            or item.get("errorMessage")
            or raw_apify.get("error")
            or raw_apify.get("errorMessage")
            or (item.get("data", {}).get("error") if isinstance(item.get("data"), dict) else None)
        )

        if err_obj or (status_code >= 400):
            detail = (
                str(err_obj)
                if isinstance(err_obj, str)
                else (repr(err_obj) if err_obj is not None else f"HTTP status {status_code}")
            )
            raise ApifyExecutionError(f"Apify discovery request failed: {detail}")

    seen_keys: set[str] = set()
    results: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict) or not item:
            continue

        raw_title = str(item.get("title") or item.get("company_name") or "").strip()
        if not raw_title:
            continue

        raw_apify = dict(item.get("raw_apify") if isinstance(item.get("raw_apify"), dict) else item)
        raw_apify.pop("raw_apify", None)

        place_id_raw = (
            item.get("placeId")
            or item.get("place_id")
            or raw_apify.get("placeId")
            or raw_apify.get("place_id")
            or ""
        )
        place_id = str(place_id_raw).strip() or None

        website_raw = item.get("website") or raw_apify.get("website") or ""
        website = str(website_raw).strip() or None

        address_raw = item.get("address") or raw_apify.get("address") or ""
        address = str(address_raw).strip() or None

        phone_raw = item.get("phone") or raw_apify.get("phone") or ""
        phone = str(phone_raw).strip() or None

        item_city = str(item.get("city") or raw_apify.get("city") or city).strip()

        categories_raw = item.get("categories") or raw_apify.get("categories") or []
        categories = list(categories_raw) if isinstance(categories_raw, list) else []

        clean_requested_btype = str(business_type or "").strip()
        first_category = (
            str(categories[0]).strip()
            if categories and categories[0] is not None and str(categories[0]).strip()
            else ""
        )
        fallback_item_btype = str(
            item.get("business_type") or raw_apify.get("business_type") or ""
        ).strip()
        # Original n8n priority: trigger.business_type || categories[0] || ''
        item_btype = clean_requested_btype or first_category or fallback_item_btype

        raw_score = (
            item.get("totalScore")
            if item.get("totalScore") is not None
            else item.get("rating")
            if item.get("rating") is not None
            else raw_apify.get("totalScore")
            if raw_apify.get("totalScore") is not None
            else raw_apify.get("rating")
        )
        rating: Optional[float] = None
        if raw_score is not None and str(raw_score).strip():
            try:
                parsed_rating = float(raw_score)
                if 0.0 <= parsed_rating <= 5.0:
                    rating = parsed_rating
            except (ValueError, TypeError):
                rating = None

        raw_reviews = (
            item.get("reviewsCount")
            if item.get("reviewsCount") is not None
            else item.get("reviews_count")
            if item.get("reviews_count") is not None
            else raw_apify.get("reviewsCount")
            if raw_apify.get("reviewsCount") is not None
            else raw_apify.get("reviews_count")
        )
        reviews_count: Optional[int] = None
        if raw_reviews is not None and str(raw_reviews).strip():
            try:
                parsed_reviews = int(float(raw_reviews))
                if parsed_reviews >= 0:
                    reviews_count = parsed_reviews
            except (ValueError, TypeError):
                reviews_count = None

        # Deduplication priority: 1) placeId, 2) normalized website, 3) normalized name + address
        dedup_key: Optional[str] = None
        norm_web = normalize_website_key(website)
        norm_name = normalize_text_key(raw_title)
        norm_addr = normalize_text_key(address)

        if place_id:
            dedup_key = f"place:{place_id.lower()}"
        elif norm_web:
            dedup_key = f"web:{norm_web}"
        elif norm_name and norm_addr:
            dedup_key = f"name_addr:{norm_name}|{norm_addr}"

        if dedup_key and dedup_key in seen_keys:
            continue
        if dedup_key:
            seen_keys.add(dedup_key)

        results.append(
            {
                "title": raw_title,
                "company_name": raw_title,
                "place_id": place_id,
                "placeId": place_id,
                "website": website,
                "address": address,
                "phone": phone,
                "city": item_city,
                "business_type": item_btype,
                "categories": categories,
                "rating": rating,
                "totalScore": rating,
                "reviews_count": reviews_count,
                "reviewsCount": reviews_count,
                "raw_apify": raw_apify,
            }
        )

    return results


def discover_businesses(
    city: str,
    business_type: str,
    max_results: int = 20,
    *,
    token: Optional[str] = None,
    actor_id: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> list[dict[str, Any]]:
    """
    Execute real business discovery via Apify Google Maps Extractor actor.
    Returns normalized, deduplicated business dictionaries.
    """
    from backend.app.config import Settings

    settings = get_settings()
    raw_token = token if token is not None else (settings.apify_api_token or "")
    effective_token = Settings.sanitize_apify_api_token(raw_token) if str(raw_token).strip() else ""
    effective_actor = (actor_id or settings.apify_actor_id or DEFAULT_APIFY_ACTOR_ID).strip()
    effective_timeout = timeout_seconds or settings.apify_request_timeout_seconds

    if not effective_token:
        raise ApifyConfigurationError(
            "APIFY_API_TOKEN is not configured. Set APIFY_API_TOKEN in environment or configuration."
        )

    url = APIFY_ACTOR_URL_TEMPLATE.format(actor_id=effective_actor)
    payload = build_apify_google_maps_input(city, business_type, max_results)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {effective_token}",
    }

    logger.info(
        "Dispatching Apify search for city='%s', business_type='%s', max_results=%d to actor=%s",
        city,
        business_type,
        max_results,
        effective_actor,
    )

    try:
        with httpx.Client(timeout=effective_timeout) as client:
            resp = client.post(url, json=payload, headers=headers)

            if resp.status_code >= 400:
                raise ApifyExecutionError(
                    f"Apify API returned HTTP {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )

            data = resp.json()
            if not isinstance(data, list):
                if isinstance(data, dict):
                    err = data.get("error") or data.get("errorMessage")
                    if err:
                        raise ApifyExecutionError(f"Apify actor execution error: {err}")
                    # If Apify returned an object with items list
                    data = data.get("items") or [data]
                else:
                    data = []

            return normalize_and_deduplicate_apify_items(data, city, business_type)

    except httpx.TimeoutException as exc:
        raise ApifyExecutionError(
            f"Apify request timed out after {effective_timeout}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise ApifyExecutionError(
            f"Apify HTTP connection error: {exc.__class__.__name__}"
        ) from exc


def scrape_website_content(
    url: Optional[str],
    timeout_seconds: float = 15.0,
    max_length: int = 40000,
) -> str:
    """
    Safely retrieve text content from a target business website.
    Translates n8n 'scraper' node (HTTP Request with onError: continueRegularOutput).
    Returns empty string on any network, protocol, timeout, or parsing error.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return ""

    target_url = url.strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = f"http://{target_url}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 LeadForge/0.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            verify=True,
        ) as client:
            resp = client.get(target_url, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Website scraping for %s returned HTTP %d", target_url, resp.status_code
                )
                return ""

            html_text = resp.text
            if not html_text:
                return ""

            # Lightweight plain text extraction (strip script, style, tags)
            cleaned = re.sub(
                r"<(script|style|svg|noscript)\b[^>]*>.*?</\1>",
                " ",
                html_text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

            return cleaned[:max_length]

    except Exception as exc:
        logger.warning(
            "Website scraping failed for %s (%s): %s",
            target_url,
            exc.__class__.__name__,
            str(exc),
        )
        return ""
