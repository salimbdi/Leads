import { LeadPriority, LeadQueryFilters, SearchRunStatus } from '../types/leadforge';

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
