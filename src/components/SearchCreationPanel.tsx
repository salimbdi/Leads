import React, { useState } from 'react';
import {
  Search,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Clock,
  Activity,
} from 'lucide-react';
import {
  SearchRunCreateRequest,
  SearchRunPipelineStatusResponse,
  SearchRunSummaryResponse,
} from '../types/leadforge';
import {
  buildPipelineStageSteps,
  validateSearchRunInput,
} from '../utils/leadforgeLogic';

interface SearchCreationPanelProps {
  onSubmitSearch: (payload: SearchRunCreateRequest) => Promise<SearchRunSummaryResponse>;
  isSubmitting: boolean;
  trackedRun: SearchRunSummaryResponse | null;
  trackedPipelineStatus: SearchRunPipelineStatusResponse | null;
}

const QUICK_NICHE_PRESETS: { businessType: string; city: string; maxResults: number }[] = [
  { businessType: 'Dental Clinic', city: 'Austin', maxResults: 5 },
  { businessType: 'HVAC Contractor', city: 'Seattle', maxResults: 5 },
  { businessType: 'Law Firm', city: 'Chicago', maxResults: 5 },
  { businessType: 'Med Spa', city: 'Miami', maxResults: 5 },
];

export const SearchCreationPanel: React.FC<SearchCreationPanelProps> = ({
  onSubmitSearch,
  isSubmitting,
  trackedRun,
  trackedPipelineStatus,
}) => {
  const [businessType, setBusinessType] = useState<string>('');
  const [city, setCity] = useState<string>('');
  const [maxResults, setMaxResults] = useState<string>('5');
  const [fieldErrors, setFieldErrors] = useState<{
    businessType?: string;
    city?: string;
    maxResults?: string;
  }>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [createdRun, setCreatedRun] = useState<SearchRunSummaryResponse | null>(null);

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setCreatedRun(null);

    const validation = validateSearchRunInput(businessType, city, maxResults);
    if (!validation.valid || !validation.sanitized) {
      setFieldErrors(validation.errors);
      return;
    }

    setFieldErrors({});
    try {
      const result = await onSubmitSearch(validation.sanitized);
      setCreatedRun(result);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Unable to submit business discovery search.';
      setSubmitError(message);
    }
  };

  const applyPreset = (preset: { businessType: string; city: string; maxResults: number }) => {
    setBusinessType(preset.businessType);
    setCity(preset.city);
    setMaxResults(String(preset.maxResults));
    setFieldErrors({});
    setSubmitError(null);
  };

  const activeRunForTracker = createdRun
    ? trackedRun && trackedRun.id === createdRun.id
      ? trackedRun
      : createdRun
    : trackedRun;

  const activePipelineStatus =
    activeRunForTracker &&
    trackedPipelineStatus &&
    trackedPipelineStatus.id === activeRunForTracker.id
      ? trackedPipelineStatus
      : null;

  const pipelineSteps = activeRunForTracker
    ? buildPipelineStageSteps(activeRunForTracker, activePipelineStatus)
    : [];

  const effectiveRunStatus = activePipelineStatus?.status ?? activeRunForTracker?.status;
  const effectiveLastError = activePipelineStatus?.last_error ?? null;

  return (
    <section
      aria-labelledby="launch-search-heading"
      className="bg-white border border-slate-200 rounded-xl p-6"
    >
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 pb-5 border-b border-slate-200">
        <div>
          <h2 id="launch-search-heading" className="text-base font-bold text-slate-900">
            01. Launch Business Discovery Search
          </h2>
          <p className="mt-1 text-xs text-slate-600">
            Queue a durable Python worker search run in SQLite (Apify Google Maps discovery → Gemini 2-stage commercial qualification → deterministic scoring).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-slate-500 mr-1">Quick fill:</span>
          {QUICK_NICHE_PRESETS.map((preset) => (
            <button
              key={`${preset.businessType}-${preset.city}`}
              type="button"
              onClick={() => applyPreset(preset)}
              className="px-2.5 py-1 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              {preset.businessType} · {preset.city}
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={handleFormSubmit} noValidate className="mt-5">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
          <div className="md:col-span-4">
            <label
              htmlFor="search-business-type"
              className="block text-xs font-semibold text-slate-800 mb-1.5"
            >
              Business Category / Niche
            </label>
            <input
              id="search-business-type"
              name="business_type"
              type="text"
              value={businessType}
              onChange={(e) => {
                setBusinessType(e.target.value);
                if (fieldErrors.businessType) {
                  setFieldErrors((prev) => ({ ...prev, businessType: undefined }));
                }
              }}
              placeholder="e.g., Dental Clinic, HVAC Contractor, Law Firm"
              disabled={isSubmitting}
              className="w-full px-3.5 py-2 text-sm text-slate-900 bg-white border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent disabled:bg-slate-100"
            />
            {fieldErrors.businessType && (
              <p role="alert" className="mt-1.5 text-xs text-red-600">
                {fieldErrors.businessType}
              </p>
            )}
          </div>

          <div className="md:col-span-4">
            <label
              htmlFor="search-target-city"
              className="block text-xs font-semibold text-slate-800 mb-1.5"
            >
              Target City or Location
            </label>
            <input
              id="search-target-city"
              name="city"
              type="text"
              value={city}
              onChange={(e) => {
                setCity(e.target.value);
                if (fieldErrors.city) {
                  setFieldErrors((prev) => ({ ...prev, city: undefined }));
                }
              }}
              placeholder="e.g., Austin, Seattle, Chicago"
              disabled={isSubmitting}
              className="w-full px-3.5 py-2 text-sm text-slate-900 bg-white border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent disabled:bg-slate-100"
            />
            {fieldErrors.city && (
              <p role="alert" className="mt-1.5 text-xs text-red-600">
                {fieldErrors.city}
              </p>
            )}
          </div>

          <div className="md:col-span-4">
            <label
              htmlFor="search-max-results"
              className="block text-xs font-semibold text-slate-800 mb-1.5"
            >
              Max Businesses (1–500)
            </label>
            <div className="flex items-center gap-2">
              <input
                id="search-max-results"
                name="max_results"
                type="number"
                min={1}
                max={500}
                value={maxResults}
                onChange={(e) => {
                  setMaxResults(e.target.value);
                  if (fieldErrors.maxResults) {
                    setFieldErrors((prev) => ({ ...prev, maxResults: undefined }));
                  }
                }}
                disabled={isSubmitting}
                className="w-20 px-3 py-2 text-sm text-slate-900 bg-white border border-slate-300 rounded-lg font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-transparent disabled:bg-slate-100"
              />
              <button
                type="submit"
                disabled={isSubmitting}
                className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-slate-900 hover:bg-slate-800 disabled:opacity-60 rounded-lg transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Submitting...</span>
                  </>
                ) : (
                  <>
                    <Search className="w-4 h-4" />
                    <span>Start Search</span>
                  </>
                )}
              </button>
            </div>
            {fieldErrors.maxResults && (
              <p role="alert" className="mt-1.5 text-xs text-red-600">
                {fieldErrors.maxResults}
              </p>
            )}
          </div>
        </div>
      </form>

      {submitError && (
        <div
          role="alert"
          className="mt-4 p-3.5 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2.5 text-xs text-red-800"
        >
          <AlertCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Search submission failed: </span>
            <span>{submitError}</span>
          </div>
        </div>
      )}

      {createdRun && (
        <div
          role="status"
          data-testid="search-created-confirmation"
          className="mt-4 p-3.5 bg-emerald-50/70 border border-emerald-200 rounded-lg flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 text-xs text-slate-800"
        >
          <div className="flex items-start sm:items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-700 shrink-0 mt-0.5 sm:mt-0" />
            <div>
              <span className="font-semibold text-emerald-900">
                Search Run #{createdRun.id} submitted:
              </span>{' '}
              <span>
                {createdRun.business_type} in {createdRun.city} (max {createdRun.max_results})
              </span>{' '}
              <span aria-hidden="true">·</span>{' '}
              <span className="font-medium">
                Queued for Python discovery worker
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Real-time 6-stage Pipeline Status Tracker */}
      {activeRunForTracker && (
        <div
          data-testid="pipeline-status-tracker"
          className="mt-5 pt-5 border-t border-slate-200"
        >
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-slate-700" />
              <span className="text-xs font-bold text-slate-900">
                Pipeline Execution Status · Search Run #{activeRunForTracker.id} (
                {activeRunForTracker.business_type} in {activeRunForTracker.city})
              </span>
            </div>
            <div className="text-xs font-mono tabular-nums text-slate-600">
              Saved leads:{' '}
              <span className="font-semibold text-slate-900">
                {activePipelineStatus?.leads_count ?? activeRunForTracker.leads_count}
              </span>{' '}
              / {activeRunForTracker.max_results}
              {activePipelineStatus?.worker_id && (
                <span className="ml-2 text-slate-400">
                  · Worker: {activePipelineStatus.worker_id}
                </span>
              )}
            </div>
          </div>

          <ol
            aria-label="Discovery pipeline stages"
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-2.5"
          >
            {pipelineSteps.map((step, index) => {
              const isDone = step.state === 'done';
              const isActive = step.state === 'active';
              const isFailed = step.state === 'failed';

              const cardStyle = isFailed
                ? 'bg-red-50/70 border-red-300 text-red-950'
                : isActive
                  ? 'bg-blue-50/80 border-blue-400 text-blue-950'
                  : isDone
                    ? 'bg-emerald-50/50 border-emerald-200 text-slate-900'
                    : 'bg-slate-50 border-slate-200 text-slate-500';

              return (
                <li
                  key={step.key}
                  data-testid={`pipeline-stage-${step.key}`}
                  data-stage-state={step.state}
                  className={`p-3 rounded-lg border flex flex-col justify-between gap-1.5 ${cardStyle}`}
                >
                  <div className="flex items-start justify-between gap-1.5">
                    <span className="text-[11px] font-mono tabular-nums text-slate-400">
                      0{index + 1}
                    </span>
                    {isDone && (
                      <CheckCircle2
                        aria-label="Stage completed"
                        className="w-3.5 h-3.5 text-emerald-700 shrink-0"
                      />
                    )}
                    {isActive && (
                      <Loader2
                        aria-label="Stage in progress"
                        className="w-3.5 h-3.5 text-blue-700 animate-spin shrink-0"
                      />
                    )}
                    {isFailed && (
                      <AlertCircle
                        aria-label="Stage failed"
                        className="w-3.5 h-3.5 text-red-600 shrink-0"
                      />
                    )}
                    {step.state === 'pending' && (
                      <Clock
                        aria-label="Stage pending"
                        className="w-3.5 h-3.5 text-slate-400 shrink-0"
                      />
                    )}
                  </div>
                  <div>
                    <div className="text-xs font-semibold leading-snug">{step.label}</div>
                    <div className="mt-1 text-[11px] leading-tight opacity-85 break-words">
                      {step.detail}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>

          {effectiveRunStatus === 'failed' && effectiveLastError && (
            <div
              role="alert"
              className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2 text-xs text-red-800"
            >
              <AlertCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold">
                  Pipeline failure on Run #{activeRunForTracker.id}:{' '}
                </span>
                <span>{effectiveLastError}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
};
