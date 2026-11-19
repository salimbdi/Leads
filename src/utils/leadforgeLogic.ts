import {
  CommercialQualificationSummary,
  LeadDetailResponse,
  LeadPriority,
  LeadQueryFilters,
  LeadSummaryResponse,
  PipelineStageKey,
  PipelineStageStep,
  SearchRunPipelineStatusResponse,
  SearchRunStatus,
  SearchRunSummaryResponse,
  ServiceFitRecommendation,
} from '../types/leadforge';

export interface SearchFormValidationResult {
  valid: boolean;
  errors: {
    businessType?: string;
    city?: string;
    maxResults?: string;
  };
  sanitized?: {
    business_type: string;
    city: string;
    max_results: number;
  };
}

export function validateSearchRunInput(
  businessType: string,
  city: string,
  maxResultsRaw: string | number
): SearchFormValidationResult {
  const errors: SearchFormValidationResult['errors'] = {};
  const trimmedBusiness = (businessType || '').trim();
  const trimmedCity = (city || '').trim();

  if (!trimmedBusiness) {
    errors.businessType = 'Enter a business category or niche (e.g., Dental Clinic, HVAC Contractor).';
  } else if (trimmedBusiness.length > 120) {
    errors.businessType = 'Business category must be 120 characters or fewer.';
  }

  if (!trimmedCity) {
    errors.city = 'Enter a target city or location (e.g., Austin, Seattle).';
  } else if (trimmedCity.length > 120) {
    errors.city = 'Target city must be 120 characters or fewer.';
  }

  const parsedMax =
    typeof maxResultsRaw === 'number'
      ? Math.trunc(maxResultsRaw)
      : Number.parseInt(String(maxResultsRaw).trim(), 10);

  if (Number.isNaN(parsedMax) || parsedMax < 1 || parsedMax > 500) {
    errors.maxResults = 'Maximum businesses must be a whole number between 1 and 500.';
  }

  const valid = Object.keys(errors).length === 0;
  return {
    valid,
    errors,
    sanitized: valid
      ? {
          business_type: trimmedBusiness,
          city: trimmedCity,
          max_results: parsedMax,
        }
      : undefined,
  };
}

export function buildLeadsQueryString(filters: LeadQueryFilters): string {
  const params = new URLSearchParams();
  params.set('limit', String(filters.limit));
  params.set('offset', String(filters.offset));
  params.set('sort_by', filters.sortBy);
  params.set('sort_order', filters.sortOrder);

  if (filters.priority !== 'all') {
    params.set('priority', filters.priority);
  }

  if (filters.minScore !== '' && !Number.isNaN(Number(filters.minScore))) {
    const clamped = Math.max(0, Math.min(100, Math.trunc(Number(filters.minScore))));
    if (clamped > 0) {
      params.set('min_score', String(clamped));
    }
  }

  const trimmedQ = filters.q.trim();
  if (trimmedQ) {
    params.set('q', trimmedQ);
  }

  const trimmedCity = filters.city.trim();
  if (trimmedCity) {
    params.set('city', trimmedCity);
  }

  const trimmedType = filters.businessType.trim();
  if (trimmedType) {
    params.set('business_type', trimmedType);
  }

  if (filters.searchRunId !== null && filters.searchRunId > 0) {
    params.set('search_run_id', String(filters.searchRunId));
  }

  if (filters.hasWebsite === 'yes') {
    params.set('has_website', 'true');
  } else if (filters.hasWebsite === 'no') {
    params.set('has_website', 'false');
  }

  return params.toString();
}

export const DIMENSION_DISPLAY_ORDER = [
  'service_opportunity',
  'digital_presence_opportunity',
  'business_relevance',
  'contactability',
  'business_evidence',
] as const;

export function formatDimensionName(key: string): string {
  switch (key) {
    case 'service_opportunity':
      return 'Service Opportunity';
    case 'digital_presence_opportunity':
      return 'Digital Presence Opportunity';
    case 'business_relevance':
      return 'Business Relevance';
    case 'contactability':
      return 'Contactability';
    case 'business_evidence':
      return 'Business Evidence';
    default:
      return key
        .split('_')
        .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : ''))
        .join(' ');
  }
}

export function formatSearchRunStatusLabel(status: SearchRunStatus): string {
  switch (status) {
    case 'pending':
      return 'Pending';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return String(status);
  }
}

export function formatPipelineStageBadge(
  status: SearchRunStatus,
  pipelineStage?: PipelineStageKey | string | null
): string {
  if (status === 'completed') return 'Completed';
  if (status === 'failed') {
    switch (pipelineStage) {
      case 'discovering_businesses':
        return 'Failed · Discovering businesses (Apify)';
      case 'analyzing_businesses':
        return 'Failed · Analyzing businesses (Gemini)';
      case 'saving_results':
        return 'Failed · Saving results';
      case 'waiting_in_queue':
        return 'Failed · Waiting in queue';
      default:
        return 'Failed';
    }
  }
  if (status === 'pending') {
    return 'Waiting in queue';
  }
  switch (pipelineStage) {
    case 'discovering_businesses':
      return 'Discovering businesses (Apify)';
    case 'analyzing_businesses':
      return 'Analyzing businesses (Gemini)';
    case 'saving_results':
      return 'Saving results';
    case 'waiting_in_queue':
      return 'Waiting in queue';
    default:
      return 'Discovering businesses (Apify)';
  }
}

/**
 * Builds the 6 factual, real-time pipeline stages required in the UI:
 * 1. Search submitted
 * 2. Waiting in queue
 * 3. Discovering businesses (Apify)
 * 4. Analyzing businesses (Gemini)
 * 5. Saving results
 * 6. Completed or failed
 *
 * Never claims a stage completed unless the backend status / pipeline_stage confirms it.
 */
export function buildPipelineStageSteps(
  run: SearchRunSummaryResponse,
  pipelineStatus?: SearchRunPipelineStatusResponse | null
): PipelineStageStep[] {
  const effectiveStatus = pipelineStatus?.status ?? run.status;
  const effectiveStage = (pipelineStatus?.pipeline_stage ||
    (effectiveStatus === 'completed'
      ? 'completed'
      : effectiveStatus === 'pending'
        ? 'waiting_in_queue'
        : effectiveStatus === 'running'
          ? 'discovering_businesses'
          : 'failed')) as PipelineStageKey;
  const leadsCount = pipelineStatus?.leads_count ?? run.leads_count;
  const maxResults = pipelineStatus?.max_results ?? run.max_results;
  const lastError = pipelineStatus?.last_error ?? null;

  const stageOrder: PipelineStageKey[] = [
    'search_submitted',
    'waiting_in_queue',
    'discovering_businesses',
    'analyzing_businesses',
    'saving_results',
    'completed',
  ];

  let activeIdx = stageOrder.indexOf(effectiveStage);
  if (activeIdx === -1) {
    // If failed without a specific stage recorded, infer from leadsCount/started_at
    if (leadsCount > 0) {
      activeIdx = 4; // saving_results
    } else if (pipelineStatus?.started_at) {
      activeIdx = 2; // discovering_businesses
    } else {
      activeIdx = 1; // waiting_in_queue
    }
  }

  const isCompleted = effectiveStatus === 'completed';
  const isFailed = effectiveStatus === 'failed';

  const getStepState = (stepIndex: number): 'done' | 'active' | 'pending' | 'failed' => {
    if (stepIndex === 0) {
      return 'done'; // Search submitted is always done once the SearchRun record exists
    }
    if (isCompleted) {
      return 'done';
    }
    if (isFailed) {
      if (stepIndex === 5) return 'failed';
      if (stepIndex < activeIdx) return 'done';
      if (stepIndex === activeIdx) return 'failed';
      return 'pending';
    }
    if (stepIndex < activeIdx) return 'done';
    if (stepIndex === activeIdx) return 'active';
    return 'pending';
  };

  return [
    {
      key: 'search_submitted',
      label: 'Search submitted',
      state: getStepState(0),
      detail: `Run #${run.id} recorded (${run.business_type} in ${run.city})`,
    },
    {
      key: 'waiting_in_queue',
      label: 'Waiting in queue',
      state: getStepState(1),
      detail:
        getStepState(1) === 'active'
          ? 'Queued in SQLite for Python durable worker claim'
          : getStepState(1) === 'done'
            ? 'Claimed by Python background worker'
            : 'Waiting for worker claim',
    },
    {
      key: 'discovering_businesses',
      label: 'Discovering businesses (Apify)',
      state: getStepState(2),
      detail:
        getStepState(2) === 'active'
          ? `Querying Google Maps extractor (up to ${maxResults} businesses)`
          : getStepState(2) === 'done'
            ? 'Apify discovery finished'
            : getStepState(2) === 'failed'
              ? lastError || 'Apify discovery stage failed'
              : 'Pending Apify discovery',
    },
    {
      key: 'analyzing_businesses',
      label: 'Analyzing businesses (Gemini)',
      state: getStepState(3),
      detail:
        getStepState(3) === 'active'
          ? `Running 2-stage Gemini qualification (${leadsCount}/${maxResults} saved)`
          : getStepState(3) === 'done'
            ? 'Gemini Stage 1 & Stage 2 analysis complete'
            : getStepState(3) === 'failed'
              ? lastError || 'Gemini analysis stage failed'
              : 'Pending Gemini qualification',
    },
    {
      key: 'saving_results',
      label: 'Saving results',
      state: getStepState(4),
      detail:
        getStepState(4) === 'active' || getStepState(4) === 'done'
          ? `${leadsCount} of ${maxResults} qualified leads persisted in SQLite`
          : getStepState(4) === 'failed'
            ? lastError || 'Database save failed'
            : 'Pending incremental lead persistence',
    },
    {
      key: 'completed_or_failed',
      label: 'Completed or failed',
      state: isCompleted ? 'done' : isFailed ? 'failed' : 'pending',
      detail: isCompleted
        ? `Completed (${leadsCount} businesses discovered & scored)`
        : isFailed
          ? `Failed: ${lastError || 'Execution interrupted'}`
          : 'Awaiting pipeline completion',
    },
  ];
}

interface ParsedRawPayloadStage1 {
  business_summary?: string;
  services?: string[];
  website_observations?: string[];
  potential_digital_needs?: string[];
  confidence?: number;
}

interface ParsedRawPayloadStage2 {
  personalization_observation?: string;
  opportunity_reasons?: string[];
  suggested_icebreaker?: string;
}

interface ParsedRawPayload {
  stage1_ai?: ParsedRawPayloadStage1;
  stage2_ai?: ParsedRawPayloadStage2;
}

function parseLeadRawPayload(rawPayload?: string | null): ParsedRawPayload {
  if (!rawPayload) return {};
  try {
    const parsed = JSON.parse(rawPayload);
    if (parsed && typeof parsed === 'object') {
      return parsed as ParsedRawPayload;
    }
  } catch {
    // Ignore malformed JSON
  }
  return {};
}

/**
 * Evaluates a discovered business lead against the user's 5 commercial services:
 * 1. Custom web applications
 * 2. Mobile applications
 * 3. Business management systems
 * 4. AI integrations and automation
 * 5. n8n automation systems
 *
 * Strictly uses available factual evidence from the lead record, website, rating/reviews,
 * deterministic score breakdown, and Gemini Stage 1/2 evidence. Never fabricates budgets,
 * company revenue, or unverified purchasing intent.
 */
export function buildCommercialQualification(
  lead: LeadSummaryResponse | LeadDetailResponse
): CommercialQualificationSummary {
  const rawPayloadStr = 'raw_payload' in lead ? lead.raw_payload : null;
  const parsedRaw = parseLeadRawPayload(rawPayloadStr);
  const stage1 = parsedRaw.stage1_ai;
  const stage2 = parsedRaw.stage2_ai;

  const extractedServices = Array.isArray(stage1?.services)
    ? stage1!.services.filter((s): s is string => typeof s === 'string' && s.trim().length > 0)
    : [];
  const websiteObservations = Array.isArray(stage1?.website_observations)
    ? stage1!.website_observations.filter(
        (s): s is string => typeof s === 'string' && s.trim().length > 0
      )
    : [];
  const digitalNeeds = Array.isArray(stage1?.potential_digital_needs)
    ? stage1!.potential_digital_needs.filter(
        (s): s is string => typeof s === 'string' && s.trim().length > 0
      )
    : [];
  const opportunityReasons = Array.isArray(stage2?.opportunity_reasons)
    ? stage2!.opportunity_reasons.filter(
        (s): s is string => typeof s === 'string' && s.trim().length > 0
      )
    : [];

  const combinedText = [
    lead.company_name,
    lead.business_type,
    lead.analysis?.business_summary || '',
    lead.analysis?.personalization_observation || '',
    lead.analysis?.potential_opportunity || '',
    ...extractedServices,
    ...websiteObservations,
    ...digitalNeeds,
    ...opportunityReasons,
  ]
    .join(' ')
    .toLowerCase();

  const reviewsCount = lead.reviews_count ?? 0;
  const rating = lead.rating ?? null;
  const hasWebsite = Boolean(normalizeExternalWebsiteUrl(lead.website));

  // 1. Operational complexity assessment from factual signals
  const complexitySignals: string[] = [];
  if (reviewsCount >= 200) {
    complexitySignals.push(
      `High customer volume (${reviewsCount.toLocaleString()} public reviews${rating ? ` at ${rating.toFixed(1)}/5.0` : ''})`
    );
  } else if (reviewsCount >= 50) {
    complexitySignals.push(
      `Established local customer base (${reviewsCount} public reviews${rating ? ` at ${rating.toFixed(1)}/5.0` : ''})`
    );
  }
  if (extractedServices.length >= 5) {
    complexitySignals.push(
      `${extractedServices.length} distinct service lines (${extractedServices.slice(0, 4).join(', ')})`
    );
  }
  if (
    combinedText.includes('multi-location') ||
    combinedText.includes('locations') ||
    combinedText.includes('multi-practitioner') ||
    combinedText.includes('multiple dentists') ||
    combinedText.includes('multi-specialty') ||
    combinedText.includes('specialists')
  ) {
    complexitySignals.push('Multi-provider or multi-specialty clinical/service operations');
  }
  if (
    combinedText.includes('insurance') ||
    combinedText.includes('financing') ||
    combinedText.includes('wellness plan') ||
    combinedText.includes('membership')
  ) {
    complexitySignals.push('In-house membership/financing or multi-carrier insurance workflows');
  }
  if (combinedText.includes('bilingual') || combinedText.includes('spanish')) {
    complexitySignals.push('Bilingual customer communication workflows');
  }

  const operationalComplexity =
    complexitySignals.length > 0
      ? complexitySignals.join(' · ')
      : `Active ${lead.business_type} operation in ${lead.city} (${reviewsCount} reviews)`;

  // 2. Digital systems & process assessment
  let digitalSystemsAssessment = '';
  if (!hasWebsite) {
    digitalSystemsAssessment =
      'No public website URL listed — relies on phone and directory listings for customer acquisition and scheduling.';
  } else if (
    combinedText.includes('online booking') ||
    combinedText.includes('appointment scheduling') ||
    combinedText.includes('patient forms')
  ) {
    digitalSystemsAssessment = `Active web presence (${formatWebsiteDisplayHost(
      lead.website
    )}) with online appointment/form entry points that can be connected to automated intake, triage, and follow-up pipelines.`;
  } else {
    digitalSystemsAssessment = `Public website (${formatWebsiteDisplayHost(
      lead.website
    )}) present; opportunity to streamline online booking, customer intake, and automated follow-up workflows.`;
  }

  // 3. Evaluate fit across the user's 5 services based on concrete evidence
  const recommendations: ServiceFitRecommendation[] = [];

  // Service A: AI integrations and automation
  if (
    combinedText.includes('ai') ||
    combinedText.includes('chatbot') ||
    combinedText.includes('triage') ||
    combinedText.includes('intake') ||
    combinedText.includes('faq') ||
    combinedText.includes('emergency') ||
    combinedText.includes('insurance') ||
    reviewsCount >= 50
  ) {
    recommendations.push({
      service: 'AI integrations and automation',
      fitLevel: 'Strong Fit',
      rationale:
        'Automate 24/7 customer/patient intake triage, service & insurance FAQ responses, and intelligent appointment routing across multiple service lines.',
      evidenceSignal:
        lead.analysis?.potential_opportunity ||
        digitalNeeds[0] ||
        `High inquiry volume implied by ${reviewsCount} reviews and multi-service catalog.`,
    });
  }

  // Service B: n8n automation systems
  if (
    combinedText.includes('reminder') ||
    combinedText.includes('follow-up') ||
    combinedText.includes('follow up') ||
    combinedText.includes('review') ||
    combinedText.includes('referral') ||
    combinedText.includes('scheduling') ||
    combinedText.includes('onboarding') ||
    combinedText.includes('workflow') ||
    reviewsCount >= 25
  ) {
    recommendations.push({
      service: 'n8n automation systems',
      fitLevel: 'Strong Fit',
      rationale:
        'Orchestrate multi-step appointment reminders, digital form-to-CRM syncing, post-visit review/referral requests, and recall campaigns.',
      evidenceSignal:
        opportunityReasons[0] ||
        websiteObservations[0] ||
        `Recurring scheduling, intake forms, and review/referral workflows (${reviewsCount} reviews).`,
    });
  }

  // Service C: Custom web applications
  if (
    !hasWebsite ||
    combinedText.includes('portal') ||
    combinedText.includes('calculator') ||
    combinedText.includes('estimator') ||
    combinedText.includes('forms') ||
    combinedText.includes('booking') ||
    combinedText.includes('wellness plan')
  ) {
    recommendations.push({
      service: 'Custom web applications',
      fitLevel: !hasWebsite || combinedText.includes('forms') || combinedText.includes('wellness plan') ? 'Strong Fit' : 'Moderate Fit',
      rationale: !hasWebsite
        ? 'Build a conversion-focused web application with integrated self-service booking and digital intake.'
        : 'Deliver interactive client/patient onboarding portals, treatment/financing estimators, or membership enrollment flows.',
      evidenceSignal: !hasWebsite
        ? 'No public website listed on business profile.'
        : websiteObservations.find((w) =>
            /form|booking|plan|schedule|financing|portal/i.test(w)
          ) || `Web presence at ${formatWebsiteDisplayHost(lead.website)} supports custom self-service web workflows.`,
    });
  }

  // Service D: Business management systems
  if (
    extractedServices.length >= 5 ||
    reviewsCount >= 150 ||
    combinedText.includes('multi-location') ||
    combinedText.includes('multi-practitioner') ||
    combinedText.includes('multi-specialty') ||
    combinedText.includes('crm') ||
    combinedText.includes('dashboard') ||
    combinedText.includes('management')
  ) {
    recommendations.push({
      service: 'Business management systems',
      fitLevel: reviewsCount >= 200 || extractedServices.length >= 6 ? 'Strong Fit' : 'Moderate Fit',
      rationale:
        'Centralize multi-provider scheduling, lead/referral tracking, treatment pipeline visibility, and operational KPI dashboards.',
      evidenceSignal:
        extractedServices.length >= 5
          ? `Manages ${extractedServices.length} service categories across ${reviewsCount} reviewed customer interactions.`
          : `Established operational volume (${reviewsCount} reviews) in ${lead.business_type}.`,
    });
  }

  // Service E: Mobile applications
  if (
    combinedText.includes('mobile') ||
    combinedText.includes('app') ||
    combinedText.includes('loyalty') ||
    combinedText.includes('wellness plan') ||
    combinedText.includes('recurring') ||
    reviewsCount >= 400
  ) {
    recommendations.push({
      service: 'Mobile applications',
      fitLevel: 'Moderate Fit',
      rationale:
        'Provide a dedicated client/patient companion mobile experience for recurring bookings, push notifications, and membership/loyalty management.',
      evidenceSignal:
        reviewsCount >= 400
          ? `Large repeat customer base (${reviewsCount.toLocaleString()} reviews) suited for mobile retention and booking.`
          : 'Recurring client relationship and membership/retention model.',
    });
  }

  // Ensure at least 2 service recommendations are always returned
  if (recommendations.length === 0) {
    recommendations.push(
      {
        service: 'Custom web applications',
        fitLevel: 'Strong Fit',
        rationale:
          'Modernize digital lead capture, interactive service quoting, and online booking conversion.',
        evidenceSignal: `Target ${lead.business_type} in ${lead.city}.`,
      },
      {
        service: 'n8n automation systems',
        fitLevel: 'Strong Fit',
        rationale:
          'Automate lead routing, customer follow-ups, and review generation workflows.',
        evidenceSignal: `Operational workflow opportunity for ${lead.company_name}.`,
      }
    );
  }

  // 4. Supporting factual evidence list
  const supportingEvidence: string[] = [];
  if (lead.analysis?.personalization_observation) {
    supportingEvidence.push(lead.analysis.personalization_observation);
  }
  for (const obs of websiteObservations.slice(0, 3)) {
    if (!supportingEvidence.includes(obs)) {
      supportingEvidence.push(obs);
    }
  }
  for (const reason of opportunityReasons.slice(0, 2)) {
    if (!supportingEvidence.includes(reason)) {
      supportingEvidence.push(reason);
    }
  }
  if (supportingEvidence.length === 0) {
    const dimReasons = [
      ...(lead.score_breakdown?.dimensions?.service_opportunity?.reasons || []),
      ...(lead.score_breakdown?.dimensions?.digital_presence_opportunity?.reasons || []),
      ...(lead.score_breakdown?.dimensions?.business_evidence?.reasons || []),
    ];
    for (const r of dimReasons.slice(0, 3)) {
      supportingEvidence.push(r);
    }
  }

  // 5. Concise explanation of why this business is a good prospect
  const topServiceNames = recommendations
    .slice(0, 3)
    .map((r) => r.service)
    .join(', ');
  const whyGoodProspect = `${lead.company_name} is an established ${lead.business_type} in ${
    lead.city
  } (${
    rating !== null ? `${rating.toFixed(1)}/5.0 across ${reviewsCount.toLocaleString()} reviews` : 'verified local operation'
  }, deterministic score ${lead.score}/100). ${
    lead.analysis?.potential_opportunity
      ? lead.analysis.potential_opportunity
      : `Observed operational volume and customer touchpoints indicate strong fit for ${topServiceNames}.`
  }`;

  return {
    whyGoodProspect,
    operationalComplexity,
    digitalSystemsAssessment,
    recommendedServices: recommendations,
    supportingEvidence,
    extractedServicesOffered: extractedServices,
  };
}

export function normalizeExternalWebsiteUrl(website: string | null | undefined): string | null {
  if (!website) return null;
  const trimmed = website.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  return `https://${trimmed}`;
}

export function formatWebsiteDisplayHost(website: string | null | undefined): string {
  const normalized = normalizeExternalWebsiteUrl(website);
  if (!normalized) return 'No website';
  try {
    const parsed = new URL(normalized);
    return parsed.hostname.replace(/^www\./i, '');
  } catch {
    return website?.trim() || 'No website';
  }
}

export function getPrioritySemanticClass(priority: LeadPriority): string {
  switch (priority) {
    case 'High':
      return 'text-emerald-700 font-semibold';
    case 'Medium':
      return 'text-amber-700 font-semibold';
    case 'Low':
      return 'text-slate-600 font-medium';
    default:
      return 'text-slate-600';
  }
}

export function formatTimestampCompact(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function resolveApiUrl(path: string, baseUrl?: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const base = (baseUrl || '').trim().replace(/\/+$/, '');
  return base ? `${base}${normalizedPath}` : normalizedPath;
}
