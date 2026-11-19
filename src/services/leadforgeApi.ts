import {
  DashboardMetricsSummary,
  LeadDetailResponse,
  LeadQueryFilters,
  PaginatedLeadsResponse,
  PaginatedSearchRunsResponse,
  SearchRunCreateRequest,
  SearchRunDetailResponse,
  SearchRunPipelineStatusResponse,
  SearchRunStatus,
  SearchRunSummaryResponse,
} from '../types/leadforge';
import { buildLeadsQueryString, resolveApiUrl } from '../utils/leadforgeLogic';

// Sticky flag: once direct calls are detected as blocked or if running in the preview gateway environment,
// route all API requests consistently through the Express gateway proxy without repeating failed direct attempts.
let directCallBlocked = false;

export function isDirectCallBlocked(): boolean {
  return directCallBlocked;
}

export function setDirectCallBlocked(blocked: boolean): void {
  directCallBlocked = blocked;
}

export function getApiBaseUrl(): string {
  if (directCallBlocked) {
    return '';
  }
  if (import.meta.env.VITE_USE_REMOTE_API !== 'true') {
    return '';
  }
  const envUrl = (import.meta.env.VITE_API_BASE_URL || '').trim();
  if (envUrl) {
    return envUrl.replace(/\/+$/, '');
  }
  return '';
}

export function apiUrl(path: string): string {
  return resolveApiUrl(path, getApiBaseUrl());
}

export async function fetchWithProxyFallback(urlPath: string, init?: RequestInit): Promise<Response> {
  const targetUrl = apiUrl(urlPath);
  const isDirectRemote = targetUrl.startsWith('http://') || targetUrl.startsWith('https://');

  if (!isDirectRemote) {
    return await fetch(targetUrl, init);
  }

  try {
    return await fetch(targetUrl, init);
  } catch (err: unknown) {
    directCallBlocked = true;
    console.warn(
      `[LeadForge] Direct fetch to ${targetUrl} failed (${
        err instanceof Error ? err.message : String(err)
      }). Switching to Express gateway proxy path for all requests.`
    );
    const gatewayUrl = resolveApiUrl(urlPath, '');
    return await fetch(gatewayUrl, init);
  }
}

async function parseJsonOrThrow<T>(response: Response, fallbackMessage: string): Promise<T> {
  if (!response.ok) {
    let errorDetail = fallbackMessage;
    try {
      const errBody = await response.json();
      if (typeof errBody?.detail === 'string') {
        errorDetail = errBody.detail;
      } else if (Array.isArray(errBody?.detail) && errBody.detail.length > 0) {
        errorDetail = errBody.detail
          .map((item: { msg?: string; loc?: (string | number)[] }) =>
            item.msg ? `${(item.loc || []).slice(1).join('.')}: ${item.msg}` : JSON.stringify(item)
          )
          .join('; ');
      } else if (typeof errBody?.error === 'string') {
        errorDetail = errBody.error;
      }
    } catch {
      errorDetail = `${fallbackMessage} (HTTP ${response.status})`;
    }
    throw new Error(errorDetail);
  }
  return (await response.json()) as T;
}

export async function fetchDashboardOverviewMetrics(): Promise<DashboardMetricsSummary> {
  const [
    allLeadsRes,
    highLeadsRes,
    mediumLeadsRes,
    lowLeadsRes,
    allRunsRes,
    runningRunsRes,
  ] = await Promise.all([
    fetchWithProxyFallback('/api/v1/leads?limit=1', { headers: { Accept: 'application/json' } }),
    fetchWithProxyFallback('/api/v1/leads?limit=1&priority=High', { headers: { Accept: 'application/json' } }),
    fetchWithProxyFallback('/api/v1/leads?limit=1&priority=Medium', { headers: { Accept: 'application/json' } }),
    fetchWithProxyFallback('/api/v1/leads?limit=1&priority=Low', { headers: { Accept: 'application/json' } }),
    fetchWithProxyFallback('/api/v1/search-runs?limit=1', { headers: { Accept: 'application/json' } }),
    fetchWithProxyFallback('/api/v1/search-runs?limit=1&status=running', { headers: { Accept: 'application/json' } }),
  ]);

  const [allLeads, highLeads, mediumLeads, lowLeads, allRuns, runningRuns] = await Promise.all([
    parseJsonOrThrow<PaginatedLeadsResponse>(allLeadsRes, 'Failed to load total leads count'),
    parseJsonOrThrow<PaginatedLeadsResponse>(highLeadsRes, 'Failed to load high-priority leads count'),
    parseJsonOrThrow<PaginatedLeadsResponse>(mediumLeadsRes, 'Failed to load medium-priority leads count'),
    parseJsonOrThrow<PaginatedLeadsResponse>(lowLeadsRes, 'Failed to load low-priority leads count'),
    parseJsonOrThrow<PaginatedSearchRunsResponse>(allRunsRes, 'Failed to load total search runs count'),
    parseJsonOrThrow<PaginatedSearchRunsResponse>(runningRunsRes, 'Failed to load running searches count'),
  ]);

  return {
    totalLeads: allLeads.total,
    highPriorityLeads: highLeads.total,
    mediumPriorityLeads: mediumLeads.total,
    lowPriorityLeads: lowLeads.total,
    totalSearchRuns: allRuns.total,
    runningSearchRuns: runningRuns.total,
  };
}

export async function createSearchRunApi(
  payload: SearchRunCreateRequest
): Promise<SearchRunSummaryResponse> {
  const response = await fetchWithProxyFallback('/api/v1/search-runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<SearchRunSummaryResponse>(
    response,
    'Unable to create business discovery search run'
  );
}

export async function triggerSearchRunApi(
  searchRunId: number
): Promise<SearchRunSummaryResponse> {
  const response = await fetchWithProxyFallback(`/api/v1/search-runs/${searchRunId}/trigger`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
    },
  });
  return parseJsonOrThrow<SearchRunSummaryResponse>(
    response,
    `Unable to trigger search run #${searchRunId}`
  );
}

export async function fetchSearchRunsApi(params: {
  limit?: number;
  offset?: number;
  status?: SearchRunStatus | 'all';
  city?: string;
  businessType?: string;
} = {}): Promise<PaginatedSearchRunsResponse> {
  const query = new URLSearchParams();
  query.set('limit', String(params.limit ?? 15));
  query.set('offset', String(params.offset ?? 0));
  if (params.status && params.status !== 'all') {
    query.set('status', params.status);
  }
  if (params.city && params.city.trim()) {
    query.set('city', params.city.trim());
  }
  if (params.businessType && params.businessType.trim()) {
    query.set('business_type', params.businessType.trim());
  }

  const response = await fetchWithProxyFallback(`/api/v1/search-runs?${query.toString()}`, {
    headers: { Accept: 'application/json' },
  });
  return parseJsonOrThrow<PaginatedSearchRunsResponse>(
    response,
    'Unable to load search run history'
  );
}

export async function fetchSearchRunDetailApi(
  searchRunId: number
): Promise<SearchRunDetailResponse> {
  const response = await fetchWithProxyFallback(`/api/v1/search-runs/${searchRunId}`, {
    headers: { Accept: 'application/json' },
  });
  return parseJsonOrThrow<SearchRunDetailResponse>(
    response,
    `Unable to load search run #${searchRunId}`
  );
}

export async function fetchSearchRunsPipelineStatusApi(
  limit = 20
): Promise<SearchRunPipelineStatusResponse[]> {
  const response = await fetchWithProxyFallback(
    `/api/v1/search-runs/pipeline-status?limit=${limit}`,
    {
      headers: { Accept: 'application/json' },
    }
  );
  return parseJsonOrThrow<SearchRunPipelineStatusResponse[]>(
    response,
    'Unable to load search run pipeline statuses'
  );
}

export async function fetchLeadsApi(
  filters: LeadQueryFilters
): Promise<PaginatedLeadsResponse> {
  const qs = buildLeadsQueryString(filters);
  const response = await fetchWithProxyFallback(`/api/v1/leads?${qs}`, {
    headers: { Accept: 'application/json' },
  });
  return parseJsonOrThrow<PaginatedLeadsResponse>(
    response,
    'Unable to load discovered business leads'
  );
}

export async function fetchLeadDetailApi(leadId: number): Promise<LeadDetailResponse> {
  const response = await fetchWithProxyFallback(`/api/v1/leads/${leadId}`, {
    headers: { Accept: 'application/json' },
  });
  return parseJsonOrThrow<LeadDetailResponse>(
    response,
    `Unable to load details for lead #${leadId}`
  );
}
