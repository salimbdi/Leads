import json
import re
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

ScoringPriority = Literal["High", "Medium", "Low"]

FORMULA_VERSION = "v1.0-phase6"

# Explicit Dimension Weights (Total = 100 points)
DIMENSION_MAX_POINTS: dict[str, int] = {
    "service_opportunity": 30,
    "digital_presence_opportunity": 25,
    "business_relevance": 20,
    "contactability": 15,
    "business_evidence": 10,
}

# Clear Priority Thresholds (0-100)
HIGH_PRIORITY_THRESHOLD = 70
MEDIUM_PRIORITY_THRESHOLD = 45

# Domain patterns indicating a business uses a social page, directory, or free subdomain
# instead of owning a dedicated custom business website.
SOCIAL_OR_DIRECTORY_HOSTS = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "linktr.ee",
    "linkedin.com",
    "yelp.com",
    "tripadvisor.com",
    "yellowpages.com",
    "sites.google.com",
    "wixsite.com",
    "wordpress.com",
    "blogspot.com",
    "square.site",
    "carrd.co",
    "weebly.com",
)

# Service opportunity keyword pillars aligned with our 5 commercial services
WEBSITE_OPPORTUNITY_KEYWORDS = (
    "no website",
    "missing website",
    "without a web",
    "without a website",
    "lacks a website",
    "needs a website",
    "redesign",
    "outdated",
    "slow",
    "broken",
    "mobile-friendly",
    "not mobile",
    "landing page",
    "web presence",
    "online presence",
    "seo",
    "domain",
)

WEB_MOBILE_APP_KEYWORDS = (
    "booking",
    "appointment",
    "schedule",
    "scheduling",
    "online order",
    "ordering",
    "portal",
    "intake",
    "calculator",
    "quote",
    "reservation",
    "mobile app",
    "web app",
    "application",
    "widget",
    "checkout",
    "self-service",
    "dashboard",
)

BUSINESS_SYSTEM_KEYWORDS = (
    "management system",
    "crm",
    "erp",
    "inventory",
    "invoic",
    "dispatch",
    "fleet",
    "patient recall",
    "recall",
    "follow-up",
    "follow up",
    "records",
    "billing",
    "pos",
    "operations",
    "staff",
    "back-office",
)

AI_AUTOMATION_KEYWORDS = (
    "automat",
    "ai ",
    "artificial intelligence",
    "chatbot",
    "chat widget",
    "live chat",
    "voice agent",
    "reminder",
    "workflow",
    "manual",
    "phone call back",
    "call back",
    "lead capture",
    "auto-reply",
    "24/7",
    "after-hours",
    "intake form",
)

DIGITAL_FRICTION_KEYWORDS = (
    "outdated",
    "redesign",
    "slow",
    "broken",
    "lacks",
    "missing",
    "no online",
    "no instant",
    "requires a phone call",
    "manual",
    "widget",
    "booking",
    "schedule",
    "portal",
    "calculator",
    "quote",
    "chat",
    "ssl",
    "insecure",
)

# Business Relevance Category Tiers
TIER1_HIGH_RELEVANCE_KEYWORDS = (
    "dental",
    "dentist",
    "orthodont",
    "clinic",
    "medical",
    "med spa",
    "medspa",
    "healthcare",
    "health",
    "chiropract",
    "physical therapy",
    "optometr",
    "veterinar",
    "dermatol",
    "plastic surg",
    "law",
    "attorney",
    "legal",
    "lawyer",
    "account",
    "cpa",
    "tax",
    "financial",
    "wealth",
    "insurance",
    "real estate",
    "realtor",
    "property management",
    "mortgage",
    "hvac",
    "plumb",
    "roof",
    "electric",
    "solar",
    "contract",
    "construction",
    "remodel",
    "landscap",
    "pest control",
    "cleaning",
    "janitorial",
    "auto repair",
    "mechanic",
    "body shop",
    "towing",
    "moving",
    "logistics",
    "freight",
    "manufacturing",
    "wholesale",
    "industrial",
    "engineering",
    "architect",
    "recruiting",
    "staffing",
)

TIER2_MODERATE_RELEVANCE_KEYWORDS = (
    "restaurant",
    "cafe",
    "coffee",
    "bakery",
    "catering",
    "hotel",
    "hospitality",
    "resort",
    "salon",
    "barber",
    "spa",
    "beauty",
    "gym",
    "fitness",
    "yoga",
    "pilates",
    "martial arts",
    "studio",
    "retail",
    "store",
    "shop",
    "boutique",
    "florist",
    "jewelry",
    "furniture",
    "auto dealer",
    "car dealer",
    "event",
    "wedding",
    "photograph",
    "education",
    "tutor",
    "school",
    "training",
    "daycare",
    "childcare",
    "pet",
    "grooming",
    "boarding",
    "print",
    "sign",
)

LOW_FIT_INSTITUTIONAL_KEYWORDS = (
    "government",
    "municipality",
    "post office",
    "public park",
    "national park",
    "cemetery",
    "bus station",
    "train station",
    "subway station",
    "atm",
    "parking lot",
    "rest area",
    "police",
    "fire station",
)


def map_score_to_priority(score: int) -> ScoringPriority:
    """
    Map a deterministic numerical score (0–100) to High, Medium, or Low priority.
    - High:   70–100
    - Medium: 45–69
    - Low:    0–44
    """
    clamped = max(0, min(100, int(score)))
    if clamped >= HIGH_PRIORITY_THRESHOLD:
        return "High"
    if clamped >= MEDIUM_PRIORITY_THRESHOLD:
        return "Medium"
    return "Low"


def _safe_str(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        numeric = float(value)
        if numeric != numeric:  # NaN check
            return None
        return numeric
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _parse_raw_dict(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if isinstance(raw_payload, str) and raw_payload.strip():
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return {}
    return {}


def _extract_website_info(website: Any) -> tuple[str, str, str]:
    """
    Parse a website string safely and return (raw_clean, scheme, hostname).
    Returns ("", "", "") if website is missing or invalid.
    """
    raw = _safe_str(website)
    if not raw:
        return "", "", ""
    candidate = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urlsplit(candidate)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if not hostname or "." not in hostname:
            return "", "", ""
        return raw, scheme, hostname
    except ValueError:
        return "", "", ""


def _evaluate_digital_presence_opportunity(
    website: Any,
    corpus_lower: str,
    raw_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Dimension 2: Digital Presence Opportunity (0–25 points).
    Evaluates missing, social-only, insecure, or limited web presence.
    """
    max_pts = DIMENSION_MAX_POINTS["digital_presence_opportunity"]
    raw_url, scheme, hostname = _extract_website_info(website)

    stage1 = raw_dict.get("stage1_ai")
    stage1_status = ""
    if isinstance(stage1, dict):
        stage1_status = _safe_str(stage1.get("website_status")).lower()

    if not hostname or stage1_status in {"missing", "none", "no_website"}:
        return {
            "score": 25,
            "max_score": max_pts,
            "reasons": [
                "No dedicated business website detected (+25 pts: prime candidate for website development)."
            ],
        }

    if any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in SOCIAL_OR_DIRECTORY_HOSTS
    ):
        return {
            "score": 22,
            "max_score": max_pts,
            "reasons": [
                f"Uses third-party profile or free host ({hostname}) instead of a custom web platform (+22 pts)."
            ],
        }

    reasons: list[str] = []
    score = 10  # Baseline for having an active custom domain
    reasons.append(f"Has existing custom domain ({hostname}) (+10 pts baseline).")

    if raw_url.lower().startswith("http://") or scheme == "http":
        score += 9
        reasons.append("Website uses unencrypted HTTP scheme (+9 pts redesign/security upgrade signal).")

    matched_friction = [kw for kw in DIGITAL_FRICTION_KEYWORDS if kw in corpus_lower]
    if matched_friction:
        bonus = 6
        score += bonus
        reasons.append(
            f"Digital presence improvement signals identified ({', '.join(matched_friction[:3])}) (+{bonus} pts)."
        )

    final_score = max(0, min(max_pts, score))
    return {
        "score": final_score,
        "max_score": max_pts,
        "reasons": reasons,
    }


def _evaluate_service_opportunity(
    has_website: bool,
    business_type_lower: str,
    potential_opportunity: str,
    personalization_observation: str,
    business_summary: str,
    has_analysis: bool,
    raw_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Dimension 1: Service Opportunity (0–30 points).
    Evaluates concrete evidence of need for our 5 commercial services:
    - Website development & redesign
    - Web application development
    - Mobile application development
    - Business management systems
    - AI solutions & workflow automation
    Missing AI analysis does not automatically penalize the business to zero.
    """
    max_pts = DIMENSION_MAX_POINTS["service_opportunity"]
    reasons: list[str] = []

    stage1 = raw_dict.get("stage1_ai") if isinstance(raw_dict.get("stage1_ai"), dict) else {}
    stage2 = raw_dict.get("stage2_ai") if isinstance(raw_dict.get("stage2_ai"), dict) else {}
    combined_text = " ".join(
        filter(
            None,
            [
                potential_opportunity,
                personalization_observation,
                business_summary,
                _safe_str(stage1.get("business_summary")),
                _safe_str(stage2.get("potential_opportunity")),
                _safe_str(stage2.get("personalization_observation")),
            ],
        )
    ).lower()

    pillar_points = 0

    # Pillar 1: Website Development & Redesign (+8)
    if not has_website or any(kw in combined_text for kw in WEBSITE_OPPORTUNITY_KEYWORDS):
        pillar_points += 8
        if not has_website:
            reasons.append("Direct need for new website build (+8 pts).")
        else:
            reasons.append("Evidence of website redesign or web visibility opportunity (+8 pts).")

    # Pillar 2: Web & Mobile Application Development (+8)
    if any(kw in combined_text for kw in WEB_MOBILE_APP_KEYWORDS):
        pillar_points += 8
        reasons.append(
            "Opportunity for custom web/mobile booking, portal, or interactive application (+8 pts)."
        )

    # Pillar 3: Business Management Systems (+7)
    if any(kw in combined_text for kw in BUSINESS_SYSTEM_KEYWORDS):
        pillar_points += 7
        reasons.append("Opportunity for CRM, patient/client recall, or management system (+7 pts).")

    # Pillar 4: AI Solutions & Workflow Automation (+7)
    if any(kw in combined_text for kw in AI_AUTOMATION_KEYWORDS):
        pillar_points += 7
        reasons.append("Opportunity for AI assistant or workflow automation (+7 pts).")

    capped_pillar_points = min(22, pillar_points)

    # Actionable AI opportunity specificity bonus (0–8 pts)
    opp_clean = potential_opportunity.strip() or _safe_str(stage2.get("potential_opportunity"))
    evidence_bonus = 0
    if len(opp_clean) >= 20:
        evidence_bonus = 8
        reasons.append("Detailed service opportunity identified in analysis (+8 pts).")
    elif len(opp_clean) > 0:
        evidence_bonus = 5
        reasons.append("Concise service opportunity noted in analysis (+5 pts).")

    total_service_pts = capped_pillar_points + evidence_bonus

    # Safe fallback when AI analysis is absent or did not extract keywords:
    # Missing analysis must not automatically classify a business as a bad lead.
    if not has_analysis and not combined_text:
        is_high_affinity_category = any(
            kw in business_type_lower for kw in TIER1_HIGH_RELEVANCE_KEYWORDS
        ) or any(kw in business_type_lower for kw in TIER2_MODERATE_RELEVANCE_KEYWORDS)

        if not has_website and is_high_affinity_category:
            total_service_pts = max(total_service_pts, 20)
            reasons.append(
                "Unanalyzed lead in service-intensive industry without a website (+12 pts category service baseline)."
            )
        elif not has_website:
            total_service_pts = max(total_service_pts, 16)
            reasons.append(
                "Unanalyzed lead without a website (+8 pts baseline service opportunity)."
            )
        elif is_high_affinity_category:
            total_service_pts = max(total_service_pts, 14)
            reasons.append(
                "AI analysis pending/unavailable; awarded neutral industry service baseline (+14 pts)."
            )
        else:
            total_service_pts = max(total_service_pts, 10)
            reasons.append(
                "AI analysis pending/unavailable; awarded neutral baseline service score (+10 pts)."
            )
    elif total_service_pts == 0:
        total_service_pts = 8
        reasons.append("General commercial software/automation baseline (+8 pts).")

    final_score = max(0, min(max_pts, total_service_pts))
    return {
        "score": final_score,
        "max_score": max_pts,
        "reasons": reasons,
    }


def _evaluate_business_relevance(
    business_type: Any,
    raw_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Dimension 3: Business Relevance (0–20 points).
    Evaluates whether the business category matches target buyers for web, app,
    business management systems, and AI workflow automation.
    """
    max_pts = DIMENSION_MAX_POINTS["business_relevance"]
    btype = _safe_str(business_type)

    apify_block = raw_dict.get("apify") if isinstance(raw_dict.get("apify"), dict) else {}
    raw_categories = apify_block.get("categories") or raw_dict.get("categories") or []
    cat_list: list[str] = []
    if isinstance(raw_categories, list):
        cat_list = [_safe_str(c) for c in raw_categories if _safe_str(c)]

    combined_category = " ".join([btype, *cat_list]).strip().lower()

    if not combined_category:
        return {
            "score": 8,
            "max_score": max_pts,
            "reasons": ["Business category unspecified; assigned conservative baseline (+8 pts)."],
        }

    if any(kw in combined_category for kw in LOW_FIT_INSTITUTIONAL_KEYWORDS):
        return {
            "score": 4,
            "max_score": max_pts,
            "reasons": [
                f"Category '{btype or combined_category}' is public/institutional with low commercial fit (+4 pts)."
            ],
        }

    if any(kw in combined_category for kw in TIER1_HIGH_RELEVANCE_KEYWORDS):
        return {
            "score": 20,
            "max_score": max_pts,
            "reasons": [
                f"High-fit commercial service category '{btype or combined_category}' (+20 pts)."
            ],
        }

    if any(kw in combined_category for kw in TIER2_MODERATE_RELEVANCE_KEYWORDS):
        return {
            "score": 15,
            "max_score": max_pts,
            "reasons": [
                f"Moderate-fit commercial category '{btype or combined_category}' (+15 pts)."
            ],
        }

    return {
        "score": 12,
        "max_score": max_pts,
        "reasons": [
            f"General commercial business category '{btype or combined_category}' (+12 pts)."
        ],
    }


def _evaluate_contactability(
    phone: Any,
    address: Any,
    city: Any,
    has_website: bool,
    raw_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Dimension 4: Contactability (0–15 points).
    Evaluates availability of actionable public contact channels (phone, address, city, web/map link).
    """
    max_pts = DIMENSION_MAX_POINTS["contactability"]
    score = 0
    reasons: list[str] = []

    phone_str = _safe_str(phone)
    digits = re.sub(r"\D", "", phone_str)
    if len(digits) >= 7:
        score += 8
        reasons.append("Direct phone number available for outreach (+8 pts).")
    elif phone_str:
        score += 3
        reasons.append("Partial phone string present (+3 pts).")
    else:
        reasons.append("No phone number listed (+0 pts).")

    address_str = _safe_str(address)
    if len(address_str) >= 5:
        score += 4
        reasons.append("Physical street address verified (+4 pts).")
    else:
        reasons.append("No street address listed (+0 pts).")

    city_str = _safe_str(city)
    if city_str:
        score += 2
        reasons.append(f"Target city '{city_str}' identified (+2 pts).")

    apify_block = raw_dict.get("apify") if isinstance(raw_dict.get("apify"), dict) else {}
    has_map_or_web = (
        has_website
        or bool(_safe_str(apify_block.get("placeId") or raw_dict.get("placeId")))
        or bool(_safe_str(apify_block.get("url") or raw_dict.get("url")))
    )
    if has_map_or_web:
        score += 1
        reasons.append("Digital or Google Maps profile identifier present (+1 pt).")

    final_score = max(0, min(max_pts, score))
    return {
        "score": final_score,
        "max_score": max_pts,
        "reasons": reasons,
    }


def _evaluate_business_evidence(
    company_name: Any,
    rating: Any,
    reviews_count: Any,
    has_analysis: bool,
    raw_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Dimension 5: Business Evidence (0–10 points).
    Verifies sufficient public business legitimacy without inflating scores merely
    for having high review counts or a 5.0 rating (strictly capped at 10 points).
    """
    max_pts = DIMENSION_MAX_POINTS["business_evidence"]
    score = 0
    reasons: list[str] = []

    parsed_reviews = _safe_int(reviews_count)
    if parsed_reviews is not None and parsed_reviews < 0:
        parsed_reviews = None

    if parsed_reviews is None or parsed_reviews == 0:
        score += 2
        reasons.append("No customer review count available (+2 pts baseline legitimacy).")
    elif 5 <= parsed_reviews <= 250:
        score += 5
        reasons.append(
            f"Established local customer base ({parsed_reviews} reviews) in target SMB range (+5 pts)."
        )
    elif 1 <= parsed_reviews <= 4:
        score += 4
        reasons.append(f"Early/growing customer review footprint ({parsed_reviews} reviews) (+4 pts).")
    else:
        score += 4
        reasons.append(f"High-volume business footprint ({parsed_reviews} reviews) (+4 pts, capped).")

    parsed_rating = _safe_float(rating)
    if parsed_rating is not None and (parsed_rating < 0.0 or parsed_rating > 5.0):
        parsed_rating = None

    if parsed_rating is None:
        score += 1
        reasons.append("No public rating listed (+1 pt baseline).")
    elif 3.0 <= parsed_rating <= 4.4:
        score += 3
        reasons.append(
            f"Moderate public rating ({parsed_rating:.1f}/5.0) suggests customer-experience/system upgrade opportunity (+3 pts)."
        )
    elif parsed_rating >= 4.5:
        score += 2
        reasons.append(
            f"Strong public rating ({parsed_rating:.1f}/5.0) confirms active commercial operation (+2 pts)."
        )
    else:
        score += 1
        reasons.append(f"Low public rating ({parsed_rating:.1f}/5.0) (+1 pt).")

    apify_block = raw_dict.get("apify") if isinstance(raw_dict.get("apify"), dict) else {}
    has_rich_metadata = (
        has_analysis
        or bool(apify_block.get("placeId"))
        or bool(apify_block.get("openingHours"))
        or bool(apify_block.get("categories"))
    )
    if has_rich_metadata:
        score += 2
        reasons.append("Rich business metadata / qualitative profile available (+2 pts).")
    elif _safe_str(company_name):
        score += 1
        reasons.append("Core business identity verified (+1 pt).")

    final_score = max(0, min(max_pts, score))
    return {
        "score": final_score,
        "max_score": max_pts,
        "reasons": reasons,
    }


def calculate_lead_score(
    *,
    company_name: Any = None,
    website: Any = None,
    address: Any = None,
    phone: Any = None,
    city: Any = None,
    business_type: Any = None,
    rating: Any = None,
    reviews_count: Any = None,
    raw_payload: Any = None,
    business_summary: Any = None,
    personalization_observation: Any = None,
    potential_opportunity: Any = None,
    icebreaker: Any = None,
    confidence: Any = None,
) -> dict[str, Any]:
    """
    Pure, deterministic lead scoring engine.
    Computes an integer score in [0, 100], priority ('High' | 'Medium' | 'Low'),
    and a structured 5-dimension breakdown from stored business & analysis data.
    Never raises on missing, malformed, or out-of-range inputs.
    """
    raw_dict = _parse_raw_dict(raw_payload)
    _, _, hostname = _extract_website_info(website)
    has_website = bool(hostname)

    summary_str = _safe_str(business_summary)
    obs_str = _safe_str(personalization_observation)
    opp_str = _safe_str(potential_opportunity)
    ice_str = _safe_str(icebreaker)
    has_analysis = any([summary_str, obs_str, opp_str, ice_str, confidence is not None])

    corpus_lower = " ".join(
        filter(None, [summary_str, obs_str, opp_str, ice_str])
    ).lower()

    service_dim = _evaluate_service_opportunity(
        has_website=has_website,
        business_type_lower=_safe_str(business_type).lower(),
        potential_opportunity=opp_str,
        personalization_observation=obs_str,
        business_summary=summary_str,
        has_analysis=has_analysis,
        raw_dict=raw_dict,
    )
    digital_dim = _evaluate_digital_presence_opportunity(
        website=website,
        corpus_lower=corpus_lower,
        raw_dict=raw_dict,
    )
    relevance_dim = _evaluate_business_relevance(
        business_type=business_type,
        raw_dict=raw_dict,
    )
    contact_dim = _evaluate_contactability(
        phone=phone,
        address=address,
        city=city,
        has_website=has_website,
        raw_dict=raw_dict,
    )
    evidence_dim = _evaluate_business_evidence(
        company_name=company_name,
        rating=rating,
        reviews_count=reviews_count,
        has_analysis=has_analysis,
        raw_dict=raw_dict,
    )

    raw_total = (
        int(service_dim["score"])
        + int(digital_dim["score"])
        + int(relevance_dim["score"])
        + int(contact_dim["score"])
        + int(evidence_dim["score"])
    )
    total_score = max(0, min(100, raw_total))
    priority = map_score_to_priority(total_score)

    return {
        "total_score": total_score,
        "max_score": 100,
        "priority": priority,
        "formula_version": FORMULA_VERSION,
        "dimensions": {
            "service_opportunity": service_dim,
            "digital_presence_opportunity": digital_dim,
            "business_relevance": relevance_dim,
            "contactability": contact_dim,
            "business_evidence": evidence_dim,
        },
    }


def score_lead_model(
    lead: Any,
    analysis_override: Any = None,
    *,
    allow_lazy_analysis: bool = True,
) -> dict[str, Any]:
    """Compute the deterministic score breakdown for a Lead ORM model instance."""
    if analysis_override is not None:
        analysis = analysis_override
    elif allow_lazy_analysis:
        analysis = getattr(lead, "analysis", None)
    else:
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.orm.base import NO_VALUE

        try:
            loaded_val = sa_inspect(lead).attrs.analysis.loaded_value
            analysis = None if loaded_val is NO_VALUE else loaded_val
        except Exception:
            analysis = None

    return calculate_lead_score(
        company_name=getattr(lead, "company_name", None),
        website=getattr(lead, "website", None),
        address=getattr(lead, "address", None),
        phone=getattr(lead, "phone", None),
        city=getattr(lead, "city", None),
        business_type=getattr(lead, "business_type", None),
        rating=getattr(lead, "rating", None),
        reviews_count=getattr(lead, "reviews_count", None),
        raw_payload=getattr(lead, "raw_payload", None),
        business_summary=getattr(analysis, "business_summary", None) if analysis else None,
        personalization_observation=(
            getattr(analysis, "personalization_observation", None) if analysis else None
        ),
        potential_opportunity=getattr(analysis, "potential_opportunity", None) if analysis else None,
        icebreaker=getattr(analysis, "icebreaker", None) if analysis else None,
        confidence=getattr(analysis, "confidence", None) if analysis else None,
    )


def apply_lead_scoring(
    lead: Any,
    analysis_override: Any = None,
    *,
    allow_lazy_analysis: bool = True,
) -> dict[str, Any]:
    """
    Compute and persist `score`, `priority`, and JSON-serialized `score_breakdown`
    onto a Lead ORM instance in-place, returning the breakdown dictionary.
    """
    breakdown = score_lead_model(
        lead,
        analysis_override=analysis_override,
        allow_lazy_analysis=allow_lazy_analysis,
    )
    lead.score = int(breakdown["total_score"])
    lead.priority = str(breakdown["priority"])
    lead.score_breakdown = json.dumps(breakdown, ensure_ascii=False)
    return breakdown

