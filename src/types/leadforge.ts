export type LeadPriority = 'High' | 'Medium' | 'Low';

export type SearchRunStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface ScoreDimensionBreakdown {
  score: number;
  max_score: number;
  reasons: string[];
}

export interface LeadScoreBreakdown {
  total_score: number;
  max_score: number;
  priority: LeadPriority;
  formula_version: string;
  dimensions: {
    service_opportunity?: ScoreDimensionBreakdown;
    digital_presence_opportunity?: ScoreDimensionBreakdown;
    business_relevance?: ScoreDimensionBreakdown;
    contactability?: ScoreDimensionBreakdown;
    business_evidence?: ScoreDimensionBreakdown;
    [key: string]: ScoreDimensionBreakdown | undefined;
  };
}

export interface LeadAnalysisResponse {
  id: number;
  lead_id: number;
  business_summary: string | null;
  personalization_observation: string | null;
  potential_opportunity: string | null;
  icebreaker: string | null;
  confidence: number | null;
  created_at: string;
  updated_at: string;
}

export interface SearchRunBriefResponse {
  id: number;
  city: string;
  business_type: string;
  max_results: number;
  status: SearchRunStatus;
  created_at: string;
  completed_at: string | null;
}

export interface LeadSummaryResponse {
  id: number;
  company_name: string;
  website: string | null;
  address: string | null;
  phone: string | null;
  city: string;
  business_type: string;
  rating: number | null;
  reviews_count: number | null;
  score: number;
  priority: LeadPriority;
  score_breakdown: LeadScoreBreakdown;
  created_at: string;
  updated_at: string;
  analysis: LeadAnalysisResponse | null;
}

export interface LeadDetailResponse extends LeadSummaryResponse {
  raw_payload: string | null;
  search_runs: SearchRunBriefResponse[];
}

export interface PaginatedLeadsResponse {
  total: number;
  limit: number;
  offset: number;
  items: LeadSummaryResponse[];
}

export interface SearchRunCreateRequest {
  city: string;
  business_type: string;
  max_results: number;
}

export interface SearchRunSummaryResponse {
  id: number;
  city: string;
  business_type: string;
  max_results: number;
  status: SearchRunStatus;
  created_at: string;
  completed_at: string | null;
  leads_count: number;
}

export interface SearchRunDetailResponse extends SearchRunSummaryResponse {
  leads: LeadSummaryResponse[];
}

export interface PaginatedSearchRunsResponse {
  total: number;
  limit: number;
  offset: number;
  items: SearchRunSummaryResponse[];
}

export interface DashboardMetricsSummary {
  totalLeads: number;
  highPriorityLeads: number;
  mediumPriorityLeads: number;
  lowPriorityLeads: number;
  totalSearchRuns: number;
  runningSearchRuns: number;
}

export type LeadSortField = 'score' | 'created_at' | 'rating' | 'reviews_count' | 'company_name';
export type SortDirection = 'asc' | 'desc';

export interface LeadQueryFilters {
  limit: number;
  offset: number;
  priority: 'all' | LeadPriority;
  minScore: number | '';
  q: string;
  city: string;
  businessType: string;
  searchRunId: number | null;
  hasWebsite: 'all' | 'yes' | 'no';
  sortBy: LeadSortField;
  sortOrder: SortDirection;
}
