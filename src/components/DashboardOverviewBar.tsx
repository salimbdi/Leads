import React from 'react';
import { DashboardMetricsSummary, LeadPriority } from '../types/leadforge';

interface DashboardOverviewBarProps {
  metrics: DashboardMetricsSummary | null;
  isLoading: boolean;
  activePriorityFilter: 'all' | LeadPriority;
  onSelectPriorityFilter: (priority: 'all' | LeadPriority) => void;
}

export const DashboardOverviewBar: React.FC<DashboardOverviewBarProps> = ({
  metrics,
  isLoading,
  activePriorityFilter,
  onSelectPriorityFilter,
}) => {
  const totalLeads = metrics?.totalLeads ?? 0;
  const highLeads = metrics?.highPriorityLeads ?? 0;
  const mediumLeads = metrics?.mediumPriorityLeads ?? 0;
  const lowLeads = metrics?.lowPriorityLeads ?? 0;
  const totalRuns = metrics?.totalSearchRuns ?? 0;
  const runningRuns = metrics?.runningSearchRuns ?? 0;

  return (
    <section
      aria-label="Dashboard Overview Metrics"
      className="bg-white border border-slate-200 rounded-xl overflow-hidden"
    >
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 divide-y md:divide-y-0 md:divide-x divide-slate-200">
        <button
          type="button"
          onClick={() => onSelectPriorityFilter('all')}
          className={`p-5 text-left transition-colors cursor-pointer hover:bg-slate-50 ${
            activePriorityFilter === 'all' ? 'bg-slate-50/80' : ''
          }`}
        >
          <div className="text-xs font-medium text-slate-500">Total Discovered Leads</div>
          <div
            data-testid="metric-total-leads"
            className="mt-2 text-2xl font-bold text-slate-900 font-mono tabular-nums"
          >
            {isLoading && !metrics ? '—' : totalLeads}
          </div>
          <div className="mt-1 text-xs text-slate-500">
            All persisted prospects
          </div>
        </button>

        <button
          type="button"
          onClick={() =>
            onSelectPriorityFilter(activePriorityFilter === 'High' ? 'all' : 'High')
          }
          className={`p-5 text-left transition-colors cursor-pointer hover:bg-slate-50 ${
            activePriorityFilter === 'High' ? 'bg-emerald-50/50' : ''
          }`}
        >
          <div className="text-xs font-medium text-slate-500">High-Priority Leads</div>
          <div
            data-testid="metric-high-leads"
            className="mt-2 text-2xl font-bold text-emerald-700 font-mono tabular-nums"
          >
            {isLoading && !metrics ? '—' : highLeads}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            Score 70–100 · Ready to review
          </div>
        </button>

        <button
          type="button"
          onClick={() =>
            onSelectPriorityFilter(activePriorityFilter === 'Medium' ? 'all' : 'Medium')
          }
          className={`p-5 text-left transition-colors cursor-pointer hover:bg-slate-50 ${
            activePriorityFilter === 'Medium' ? 'bg-amber-50/50' : ''
          }`}
        >
          <div className="text-xs font-medium text-slate-500">Medium-Priority Leads</div>
          <div
            data-testid="metric-medium-leads"
            className="mt-2 text-2xl font-bold text-amber-700 font-mono tabular-nums"
          >
            {isLoading && !metrics ? '—' : mediumLeads}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            Score 45–69 · Secondary fit
          </div>
        </button>

        <button
          type="button"
          onClick={() =>
            onSelectPriorityFilter(activePriorityFilter === 'Low' ? 'all' : 'Low')
          }
          className={`p-5 text-left transition-colors cursor-pointer hover:bg-slate-50 ${
            activePriorityFilter === 'Low' ? 'bg-slate-100/70' : ''
          }`}
        >
          <div className="text-xs font-medium text-slate-500">Low-Priority Leads</div>
          <div
            data-testid="metric-low-leads"
            className="mt-2 text-2xl font-bold text-slate-700 font-mono tabular-nums"
          >
            {isLoading && !metrics ? '—' : lowLeads}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            Score 0–44 · Low service fit
          </div>
        </button>

        <div className="p-5">
          <div className="text-xs font-medium text-slate-500">Total Search Runs</div>
          <div
            data-testid="metric-total-runs"
            className="mt-2 text-2xl font-bold text-slate-900 font-mono tabular-nums"
          >
            {isLoading && !metrics ? '—' : totalRuns}
          </div>
          <div className="mt-1 text-xs text-slate-500">
            Discovery batches recorded
          </div>
        </div>

        <div className="p-5">
          <div className="text-xs font-medium text-slate-500">Currently Running Searches</div>
          <div
            data-testid="metric-running-runs"
            className={`mt-2 text-2xl font-bold font-mono tabular-nums ${
              runningRuns > 0 ? 'text-blue-700' : 'text-slate-900'
            }`}
          >
            {isLoading && !metrics ? '—' : runningRuns}
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {runningRuns > 0 ? 'Active n8n execution in progress' : 'No active workflow runs'}
          </div>
        </div>
      </div>
    </section>
  );
};
