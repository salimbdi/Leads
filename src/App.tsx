/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useState } from 'react';
import { TopNav, WorkspaceSection } from './components/TopNav';
import { Sidebar } from './components/Sidebar';
import { DashboardOverviewBar } from './components/DashboardOverviewBar';
import { SearchCreationPanel } from './components/SearchCreationPanel';
import { SearchHistoryPanel } from './components/SearchHistoryPanel';
import { LeadsTablePanel } from './components/LeadsTablePanel';
import { LeadDetailModal } from './components/LeadDetailModal';
import { HealthMonitorPanel } from './components/HealthMonitorPanel';
import { ArchitectureRoadmapPanel } from './components/ArchitectureRoadmapPanel';
import { ThinkingEnginePanel } from './components/ThinkingEnginePanel';
import {
  ArchitectureStep,
  FoundationStatusResponse,
  HealthCheckLogEntry,
  HealthResponse,
} from './types/foundation';
import {
  DashboardMetricsSummary,
  LeadDetailResponse,
  LeadPriority,
  LeadQueryFilters,
  LeadSummaryResponse,
  SearchRunCreateRequest,
  SearchRunPipelineStatusResponse,
  SearchRunStatus,
  SearchRunSummaryResponse,
} from './types/leadforge';
import {
  createSearchRunApi,
  fetchDashboardOverviewMetrics,
  fetchLeadDetailApi,
  fetchLeadsApi,
  fetchSearchRunsApi,
  fetchSearchRunsPipelineStatusApi,
  fetchWithProxyFallback,
  triggerSearchRunApi,
} from './services/leadforgeApi';

const DEFAULT_ARCHITECTURE_FLOW: ArchitectureStep[] = [
  { step: 1, component: 'React Dashboard', role: 'Search & lead qualification UI', status: 'active' },
  { step: 2, component: 'FastAPI Backend', role: 'REST API, validation & deterministic scoring', status: 'active' },
  { step: 3, component: 'Python Durable Worker', role: 'SQLite-backed asynchronous job claim & execution', status: 'active' },
  { step: 4, component: 'Apify + Gemini AI', role: 'Business discovery & 2-stage commercial qualification', status: 'active' },
  { step: 5, component: 'Incremental Persistence', role: 'Idempotent ingestion & deterministic lead scoring', status: 'active' },
  { step: 6, component: 'SQLite + SQLAlchemy', role: 'Single source of truth persistence', status: 'active' },
];

const INITIAL_LEAD_FILTERS: LeadQueryFilters = {
  limit: 20,
  offset: 0,
  priority: 'all',
  minScore: '',
  q: '',
  city: '',
  businessType: '',
  searchRunId: null,
  hasWebsite: 'all',
  sortBy: 'score',
  sortOrder: 'desc',
};

export default function App() {
  const [activeSection, setActiveSection] = useState<WorkspaceSection>('dashboard');

  // Health & Foundation state
  const [healthData, setHealthData] = useState<HealthResponse | null>(null);
  const [architectureFlow, setArchitectureFlow] = useState<ArchitectureStep[]>(
    DEFAULT_ARCHITECTURE_FLOW
  );
  const [healthError, setHealthError] = useState<string | null>(null);
  const [isCheckingHealth, setIsCheckingHealth] = useState<boolean>(false);
  const [checkLogs, setCheckLogs] = useState<HealthCheckLogEntry[]>([]);

  // Dashboard Overview Metrics state
  const [overviewMetrics, setOverviewMetrics] = useState<DashboardMetricsSummary | null>(null);
  const [isLoadingOverview, setIsLoadingOverview] = useState<boolean>(false);

  // Search Runs state
  const [searchRuns, setSearchRuns] = useState<SearchRunSummaryResponse[]>([]);
  const [pipelineStatusMap, setPipelineStatusMap] = useState<
    Record<number, SearchRunPipelineStatusResponse>
  >({});
  const [totalSearchRuns, setTotalSearchRuns] = useState<number>(0);
  const [runStatusFilter, setRunStatusFilter] = useState<'all' | SearchRunStatus>('all');
  const [isLoadingRuns, setIsLoadingRuns] = useState<boolean>(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [isSubmittingSearch, setIsSubmittingSearch] = useState<boolean>(false);
  const [triggeringRunId, setTriggeringRunId] = useState<number | null>(null);

  // Leads Table state
  const [leads, setLeads] = useState<LeadSummaryResponse[]>([]);
  const [totalLeads, setTotalLeads] = useState<number>(0);
  const [leadFilters, setLeadFilters] = useState<LeadQueryFilters>(INITIAL_LEAD_FILTERS);
  const [isLoadingLeads, setIsLoadingLeads] = useState<boolean>(false);
  const [leadsError, setLeadsError] = useState<string | null>(null);

  // Selected Lead Detail Modal state
  const [selectedLeadId, setSelectedLeadId] = useState<number | null>(null);
  const [selectedLeadDetail, setSelectedLeadDetail] = useState<LeadDetailResponse | null>(null);
  const [isLoadingLeadDetail, setIsLoadingLeadDetail] = useState<boolean>(false);
  const [leadDetailError, setLeadDetailError] = useState<string | null>(null);

  const runHealthCheck = useCallback(
    async (endpoint: '/api/health' | '/health' = '/api/health') => {
      setIsCheckingHealth(true);
      setHealthError(null);
      const startTime = performance.now();

      try {
        const response = await fetchWithProxyFallback(endpoint, {
          headers: { Accept: 'application/json' },
        });
        const latencyMs = Math.max(1, Math.round(performance.now() - startTime));

        if (!response.ok) {
          throw new Error(`HTTP ${response.status} ${response.statusText}`);
        }

        const data: HealthResponse = await response.json();
        setHealthData(data);

        const newLog: HealthCheckLogEntry = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          endpoint,
          statusCode: response.status,
          statusText: response.statusText || 'OK',
          latencyMs,
          checkedAt: new Date().toISOString(),
          service: data.service,
        };

        setCheckLogs((prev) => [newLog, ...prev.slice(0, 14)]);
      } catch (err: unknown) {
        const message =
          err instanceof Error
            ? err.message
            : 'Unable to reach FastAPI health endpoint';
        setHealthError(message);
      } finally {
        setIsCheckingHealth(false);
      }
    },
    []
  );

  const loadOverviewMetrics = useCallback(async () => {
    setIsLoadingOverview(true);
    try {
      const metrics = await fetchDashboardOverviewMetrics();
      setOverviewMetrics(metrics);
    } catch {
      // Errors are surfaced in individual Search Runs / Leads panels
    } finally {
      setIsLoadingOverview(false);
    }
  }, []);

  const loadSearchRuns = useCallback(async () => {
    setIsLoadingRuns(true);
    setRunsError(null);
    try {
      const [data, pipelineStatuses] = await Promise.all([
        fetchSearchRunsApi({
          limit: 15,
          offset: 0,
          status: runStatusFilter,
        }),
        fetchSearchRunsPipelineStatusApi(25).catch(() => []),
      ]);
      setSearchRuns(data.items);
      setTotalSearchRuns(data.total);
      const statusLookup: Record<number, SearchRunPipelineStatusResponse> = {};
      for (const item of pipelineStatuses) {
        statusLookup[item.id] = item;
      }
      setPipelineStatusMap(statusLookup);
    } catch (err: unknown) {
      setRunsError(
        err instanceof Error ? err.message : 'Unable to load search runs.'
      );
    } finally {
      setIsLoadingRuns(false);
    }
  }, [runStatusFilter]);

  const loadLeads = useCallback(async () => {
    setIsLoadingLeads(true);
    setLeadsError(null);
    try {
      const data = await fetchLeadsApi(leadFilters);
      setLeads(data.items);
      setTotalLeads(data.total);
    } catch (err: unknown) {
      setLeadsError(
        err instanceof Error ? err.message : 'Unable to load business leads.'
      );
    } finally {
      setIsLoadingLeads(false);
    }
  }, [leadFilters]);

  const refreshAllDashboardData = useCallback(async () => {
    await Promise.all([loadOverviewMetrics(), loadSearchRuns(), loadLeads()]);
  }, [loadOverviewMetrics, loadSearchRuns, loadLeads]);

  // Initial load of health and foundation metadata
  useEffect(() => {
    runHealthCheck('/api/health');

    fetchWithProxyFallback('/api/v1/foundation')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: FoundationStatusResponse | null) => {
        if (data?.architecture_flow) {
          setArchitectureFlow(data.architecture_flow);
        }
      })
      .catch(() => {
        // Fallback to default architecture list
      });
  }, [runHealthCheck]);

  // Load overview metrics on mount
  useEffect(() => {
    loadOverviewMetrics();
  }, [loadOverviewMetrics]);

  // Reload search runs when runStatusFilter changes
  useEffect(() => {
    loadSearchRuns();
  }, [loadSearchRuns]);

  // Reload leads when leadFilters change
  useEffect(() => {
    loadLeads();
  }, [loadLeads]);

  // Poll search runs, pipeline stages, and leads every 2.5s whenever any search run is pending or running
  useEffect(() => {
    const hasActivePipeline =
      (overviewMetrics?.runningSearchRuns ?? 0) > 0 ||
      searchRuns.some((r) => r.status === 'running' || r.status === 'pending');
    if (!hasActivePipeline) return;

    const timer = window.setInterval(() => {
      loadOverviewMetrics();
      loadSearchRuns();
      loadLeads();
    }, 2500);

    return () => window.clearInterval(timer);
  }, [overviewMetrics?.runningSearchRuns, searchRuns, loadOverviewMetrics, loadSearchRuns, loadLeads]);

  const handleCreateSearchRun = async (
    payload: SearchRunCreateRequest
  ): Promise<SearchRunSummaryResponse> => {
    setIsSubmittingSearch(true);
    try {
      const created = await createSearchRunApi(payload);
      await Promise.all([loadOverviewMetrics(), loadSearchRuns(), loadLeads()]);
      return created;
    } finally {
      setIsSubmittingSearch(false);
    }
  };

  const handleTriggerRun = async (runId: number) => {
    setTriggeringRunId(runId);
    setRunsError(null);
    try {
      await triggerSearchRunApi(runId);
      await Promise.all([loadOverviewMetrics(), loadSearchRuns(), loadLeads()]);
    } catch (err: unknown) {
      setRunsError(
        err instanceof Error ? err.message : `Failed to dispatch search run #${runId}`
      );
    } finally {
      setTriggeringRunId(null);
    }
  };

  const handleUpdateLeadFilters = useCallback((patch: Partial<LeadQueryFilters>) => {
    setLeadFilters((prev) => ({ ...prev, ...patch }));
  }, []);

  const handleResetLeadFilters = useCallback(() => {
    setLeadFilters(INITIAL_LEAD_FILTERS);
  }, []);

  const handleSelectPriorityFromOverview = useCallback(
    (priority: 'all' | LeadPriority) => {
      setLeadFilters((prev) => ({ ...prev, priority, offset: 0 }));
    },
    []
  );

  const handleSelectLead = useCallback(async (leadId: number) => {
    setSelectedLeadId(leadId);
    setSelectedLeadDetail(null);
    setLeadDetailError(null);
    setIsLoadingLeadDetail(true);
    try {
      const detail = await fetchLeadDetailApi(leadId);
      setSelectedLeadDetail(detail);
    } catch (err: unknown) {
      setLeadDetailError(
        err instanceof Error ? err.message : `Failed to load lead #${leadId}`
      );
    } finally {
      setIsLoadingLeadDetail(false);
    }
  }, []);

  const handleCloseLeadDetail = useCallback(() => {
    setSelectedLeadId(null);
    setSelectedLeadDetail(null);
    setLeadDetailError(null);
  }, []);

  const isWorkspaceBusy =
    isCheckingHealth || isLoadingOverview || isLoadingRuns || isLoadingLeads;

  const trackedRunForPipeline =
    (leadFilters.searchRunId
      ? searchRuns.find((r) => r.id === leadFilters.searchRunId)
      : null) ||
    searchRuns.find((r) => r.status === 'running' || r.status === 'pending') ||
    searchRuns[0] ||
    null;

  const trackedPipelineStatus = trackedRunForPipeline
    ? pipelineStatusMap[trackedRunForPipeline.id] ?? null
    : null;

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <TopNav
        activeSection={activeSection}
        onSelectSection={setActiveSection}
        onPrimaryAction={() => {
          if (activeSection === 'dashboard') {
            refreshAllDashboardData();
          } else {
            runHealthCheck('/api/health');
          }
        }}
        isBusy={isWorkspaceBusy}
      />

      <div className="flex-1 flex flex-col lg:flex-row">
        <Sidebar
          activeSection={activeSection}
          onSelectSection={setActiveSection}
          healthData={healthData}
          healthError={healthError}
        />

        <main className="flex-1 min-w-0 w-full max-w-7xl p-6 lg:p-8">
          {activeSection === 'dashboard' && (
            <div className="space-y-6">
              <div>
                <h1 className="text-2xl font-bold tracking-tight text-slate-900">
                  Business Discovery & Qualification Workspace
                </h1>
                <p className="mt-1 text-sm text-slate-600">
                  Launch local business searches through the Python durable worker (Apify + Gemini), monitor real-time pipeline stages, and qualify high-value prospects for custom software, AI, and automation services.
                </p>
              </div>

              <DashboardOverviewBar
                metrics={overviewMetrics}
                isLoading={isLoadingOverview}
                activePriorityFilter={leadFilters.priority}
                onSelectPriorityFilter={handleSelectPriorityFromOverview}
              />

              <SearchCreationPanel
                onSubmitSearch={handleCreateSearchRun}
                isSubmitting={isSubmittingSearch}
                trackedRun={trackedRunForPipeline}
                trackedPipelineStatus={trackedPipelineStatus}
              />

              <SearchHistoryPanel
                runs={searchRuns}
                totalRuns={totalSearchRuns}
                isLoading={isLoadingRuns}
                error={runsError}
                statusFilter={runStatusFilter}
                onChangeStatusFilter={setRunStatusFilter}
                onRefresh={() => {
                  loadOverviewMetrics();
                  loadSearchRuns();
                }}
                onTriggerRun={handleTriggerRun}
                triggeringRunId={triggeringRunId}
                selectedSearchRunId={leadFilters.searchRunId}
                onSelectSearchRunFilter={(runId) =>
                  handleUpdateLeadFilters({ searchRunId: runId, offset: 0 })
                }
                pipelineStatusMap={pipelineStatusMap}
              />

              <LeadsTablePanel
                leads={leads}
                totalLeads={totalLeads}
                filters={leadFilters}
                isLoading={isLoadingLeads}
                error={leadsError}
                onUpdateFilters={handleUpdateLeadFilters}
                onResetFilters={handleResetLeadFilters}
                onRefresh={() => {
                  loadOverviewMetrics();
                  loadLeads();
                }}
                onSelectLead={handleSelectLead}
                selectedLeadId={selectedLeadId}
              />

              {selectedLeadId !== null && (
                <LeadDetailModal
                  lead={selectedLeadDetail}
                  isLoading={isLoadingLeadDetail}
                  error={leadDetailError}
                  onClose={handleCloseLeadDetail}
                />
              )}
            </div>
          )}

          {activeSection === 'health' && (
            <HealthMonitorPanel
              healthData={healthData}
              healthError={healthError}
              isChecking={isCheckingHealth}
              checkLogs={checkLogs}
              onRunHealthCheck={runHealthCheck}
            />
          )}

          {activeSection === 'architecture' && (
            <ArchitectureRoadmapPanel architectureFlow={architectureFlow} />
          )}

          {activeSection === 'thinking' && <ThinkingEnginePanel />}
        </main>
      </div>
    </div>
  );
}
