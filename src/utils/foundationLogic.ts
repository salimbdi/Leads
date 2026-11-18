import {
  ArchitectureStep,
  HealthCheckLogEntry,
  PhaseFilter,
  RoadmapPhaseItem,
} from '../types/foundation';

export const ROADMAP_PHASES: RoadmapPhaseItem[] = [
  {
    phaseNumber: 1,
    title: 'Phase 1 — Project Foundation',
    summary: 'Establish workspace layout, React + TypeScript + Vite frontend, FastAPI backend, environment configuration, and live health verification.',
    deliverables: [
      'Modular frontend & FastAPI backend directory structure',
      'Typed Pydantic environment settings without secret leakage',
      'FastAPI /health and /api/health endpoints with CORS support',
      'Configurable AI reasoning setup (mock default + gemini-4-carbon HIGH thinking)',
    ],
    state: 'active',
  },
  {
    phaseNumber: 2,
    title: 'Phase 2 — Database & ORM Models',
    summary: 'Configure SQLite as the single source of truth using SQLAlchemy 2.0 models, constraints, and relationships.',
    deliverables: [
      'SearchRun, Lead, LeadAnalysis, and SearchRunLead models',
      'Foreign keys, uniqueness constraints, and session lifecycle management',
      'Automated database initialization and CRUD verification tests',
    ],
    state: 'active',
  },
  {
    phaseNumber: 3,
    title: 'Phase 3 — Backend REST API',
    summary: 'Expose validated FastAPI endpoints for creating and retrieving search runs, listing leads, filtering, and inspecting lead details.',
    deliverables: [
      'SearchRun creation and status retrieval routes',
      'Lead listing with city, business_type, rating, and score filters',
      'Pydantic request/response schemas and structured error handling',
    ],
    state: 'active',
  },
  {
    phaseNumber: 4,
    title: 'Phase 4 — n8n & Apify Orchestration',
    summary: 'Connect FastAPI to the LeadForge — Business Discovery & Qualification v2 n8n workflow and implement secured idempotent callbacks.',
    deliverables: [
      'Outbound webhook trigger to n8n workflow',
      'Secured FastAPI callback endpoint preserving raw Apify data + AI analysis',
      'Duplicate prevention and idempotent lead ingestion',
    ],
    state: 'upcoming',
  },
  {
    phaseNumber: 5,
    title: 'Phase 5 — Discovery & Leads Dashboard',
    summary: 'Build the production business search form, execution history monitor, filterable leads data table, and lead detail drawer.',
    deliverables: [
      'Business search launch form (city, business_type, max_results)',
      'Search run history table and live execution status tracking',
      'Filterable leads table and detailed lead qualification view',
    ],
    state: 'upcoming',
  },
  {
    phaseNumber: 6,
    title: 'Phase 6 — Deterministic Lead Qualification',
    summary: 'Compute transparent, rule-based lead scores in the FastAPI backend combining Apify metrics and AI-extracted evidence.',
    deliverables: [
      'Deterministic backend scoring rules with transparent point breakdowns',
      'Graceful handling of missing websites, ratings, or uncertain AI fields',
      'Strict separation between AI evidence extraction and final score calculation',
    ],
    state: 'upcoming',
  },
  {
    phaseNumber: 7,
    title: 'Phase 7 — Quality Assurance & Deployment',
    summary: 'Complete end-to-end test coverage, security review, structured logging, and production deployment documentation.',
    deliverables: [
      'Comprehensive unit and integration test suites',
      'Input validation hardening and secret audit',
      'Production deployment and operations guide',
    ],
    state: 'upcoming',
  },
];

export function filterRoadmapPhases(
  phases: RoadmapPhaseItem[],
  filter: PhaseFilter,
  searchQuery = ''
): RoadmapPhaseItem[] {
  const normalizedQuery = searchQuery.trim().toLowerCase();
  return phases.filter((item) => {
    const matchesFilter =
      filter === 'all' ? true : item.state === filter;
    if (!matchesFilter) return false;
    if (!normalizedQuery) return true;
    const inTitle = item.title.toLowerCase().includes(normalizedQuery);
    const inSummary = item.summary.toLowerCase().includes(normalizedQuery);
    const inDeliverables = item.deliverables.some((d) =>
      d.toLowerCase().includes(normalizedQuery)
    );
    return inTitle || inSummary || inDeliverables;
  });
}

export function formatArchitectureStatus(
  status: ArchitectureStep['status']
): string {
  switch (status) {
    case 'active':
      return 'Operational in Phase 1';
    case 'planned_phase_2':
      return 'Scheduled for Phase 2';
    case 'planned_phase_4':
      return 'Scheduled for Phase 4';
    default:
      return 'Planned';
  }
}

export function calculateAverageLatency(entries: HealthCheckLogEntry[]): number {
  if (entries.length === 0) return 0;
  const total = entries.reduce((sum, entry) => sum + entry.latencyMs, 0);
  return Math.round(total / entries.length);
}
