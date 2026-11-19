import React from 'react';
import { RefreshCw, Play, Filter } from 'lucide-react';
import {
  SearchRunPipelineStatusResponse,
  SearchRunStatus,
  SearchRunSummaryResponse,
} from '../types/leadforge';
import {
  formatPipelineStageBadge,
  formatSearchRunStatusLabel,
  formatTimestampCompact,
} from '../utils/leadforgeLogic';

interface SearchHistoryPanelProps {
  runs: SearchRunSummaryResponse[];
  totalRuns: number;
  isLoading: boolean;
  error: string | null;
  statusFilter: 'all' | SearchRunStatus;
  onChangeStatusFilter: (status: 'all' | SearchRunStatus) => void;
  onRefresh: () => void;
  onTriggerRun: (runId: number) => Promise<void>;
  triggeringRunId: number | null;
  selectedSearchRunId: number | null;
  onSelectSearchRunFilter: (runId: number | null) => void;
  pipelineStatusMap?: Record<number, SearchRunPipelineStatusResponse>;
}

function getStatusSemanticStyle(status: SearchRunStatus): string {
  switch (status) {
    case 'completed':
      return 'text-emerald-700 font-semibold';
    case 'running':
      return 'text-blue-700 font-semibold';
    case 'failed':
      return 'text-red-700 font-semibold';
    case 'pending':
    default:
      return 'text-amber-700 font-semibold';
  }
}

export const SearchHistoryPanel: React.FC<SearchHistoryPanelProps> = ({
  runs,
  totalRuns,
  isLoading,
  error,
  statusFilter,
  onChangeStatusFilter,
  onRefresh,
  onTriggerRun,
  triggeringRunId,
  selectedSearchRunId,
  onSelectSearchRunFilter,
  pipelineStatusMap = {},
}) => {
  const statusOptions: { value: 'all' | SearchRunStatus; label: string }[] = [
    { value: 'all', label: 'All Runs' },
    { value: 'running', label: 'Running' },
    { value: 'completed', label: 'Completed' },
    { value: 'pending', label: 'Pending' },
    { value: 'failed', label: 'Failed' },
  ];

  return (
    <section
      aria-labelledby="search-history-heading"
      className="bg-white border border-slate-200 rounded-xl overflow-hidden"
    >
      <div className="p-5 border-b border-slate-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 id="search-history-heading" className="text-base font-bold text-slate-900">
            02. Search Execution History
          </h2>
          <p className="mt-0.5 text-xs text-slate-600 font-mono tabular-nums">
            Showing {runs.length} of {totalRuns} recorded search runs
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Filter search runs by status"
            className="flex items-center gap-1 p-1 bg-slate-100 rounded-lg"
          >
            {statusOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onChangeStatusFilter(opt.value)}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                  statusFilter === opt.value
                    ? 'bg-white text-slate-900 shadow-xs'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={onRefresh}
            disabled={isLoading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-60 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh Runs</span>
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="p-4 bg-red-50 border-b border-red-200 text-xs text-red-800"
        >
          <span className="font-semibold">Error loading search history: </span>
          <span>{error}</span>
        </div>
      )}

      {runs.length === 0 ? (
        <div className="p-8 text-center">
          <p className="text-sm font-medium text-slate-800">
            {isLoading ? 'Loading search runs...' : 'No search runs match the current view.'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Submit a business category and target city above to launch a discovery run.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/70 text-[11px] font-semibold text-slate-600">
                <th className="py-2.5 px-4">Run ID</th>
                <th className="py-2.5 px-4">Business Category</th>
                <th className="py-2.5 px-4">Target Location</th>
                <th className="py-2.5 px-4">Created</th>
                <th className="py-2.5 px-4">Status & Pipeline Stage</th>
                <th className="py-2.5 px-4 text-right">Discovered Leads</th>
                <th className="py-2.5 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {runs.map((run) => {
                const isSelected = selectedSearchRunId === run.id;
                const isTriggering = triggeringRunId === run.id;
                const pipe = pipelineStatusMap[run.id];
                const stageBadge = formatPipelineStageBadge(
                  run.status,
                  pipe?.pipeline_stage
                );
                return (
                  <tr
                    key={run.id}
                    className={`transition-colors hover:bg-slate-50 ${
                      isSelected ? 'bg-blue-50/50' : ''
                    }`}
                  >
                    <td className="py-3 px-4 font-mono tabular-nums font-semibold text-slate-900">
                      #{run.id}
                    </td>
                    <td className="py-3 px-4 font-medium text-slate-900">
                      {run.business_type}
                    </td>
                    <td className="py-3 px-4 text-slate-700">{run.city}</td>
                    <td className="py-3 px-4 text-slate-500 font-mono tabular-nums whitespace-nowrap">
                      {formatTimestampCompact(run.created_at)}
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={getStatusSemanticStyle(run.status)}>
                          {formatSearchRunStatusLabel(run.status)}
                        </span>
                        <span aria-hidden="true" className="text-slate-300">
                          ·
                        </span>
                        <span className="text-slate-600">{stageBadge}</span>
                      </div>
                      {pipe?.last_error && (
                        <div className="mt-0.5 text-[11px] text-red-600 max-w-md break-words">
                          Error: {pipe.last_error}
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right font-mono tabular-nums text-slate-800">
                      {run.leads_count} <span className="text-slate-400">/ {run.max_results}</span>
                    </td>
                    <td className="py-3 px-4 text-right whitespace-nowrap">
                      <div className="inline-flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            onSelectSearchRunFilter(isSelected ? null : run.id)
                          }
                          className={`inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md border transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                            isSelected
                              ? 'bg-slate-900 text-white border-slate-900'
                              : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                          }`}
                        >
                          <Filter className="w-3 h-3" />
                          <span>{isSelected ? 'Showing Leads' : `Filter Leads (${run.leads_count})`}</span>
                        </button>

                        {(run.status === 'pending' || run.status === 'failed') && (
                          <button
                            type="button"
                            onClick={() => onTriggerRun(run.id)}
                            disabled={isTriggering}
                            aria-label={`Trigger Search Run #${run.id}`}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded-md disabled:opacity-60 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
                          >
                            <Play className="w-3 h-3" />
                            <span>{isTriggering ? 'Queueing...' : 'Retry Discovery'}</span>
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};
