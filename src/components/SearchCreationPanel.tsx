import React, { useState } from 'react';
import { Search, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { SearchRunCreateRequest, SearchRunSummaryResponse } from '../types/leadforge';
import { validateSearchRunInput } from '../utils/leadforgeLogic';

interface SearchCreationPanelProps {
  onSubmitSearch: (payload: SearchRunCreateRequest) => Promise<SearchRunSummaryResponse>;
  isSubmitting: boolean;
}

const QUICK_NICHE_PRESETS: { businessType: string; city: string; maxResults: number }[] = [
  { businessType: 'Dental Clinic', city: 'Austin', maxResults: 20 },
  { businessType: 'HVAC Contractor', city: 'Seattle', maxResults: 20 },
  { businessType: 'Law Firm', city: 'Chicago', maxResults: 15 },
  { businessType: 'Med Spa', city: 'Miami', maxResults: 25 },
];

export const SearchCreationPanel: React.FC<SearchCreationPanelProps> = ({
  onSubmitSearch,
  isSubmitting,
}) => {
  const [businessType, setBusinessType] = useState<string>('');
  const [city, setCity] = useState<string>('');
  const [maxResults, setMaxResults] = useState<string>('20');
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
            Create a tracked search run in SQLite and dispatch to the configured n8n discovery workflow.
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
                Search Run #{createdRun.id} created:
              </span>{' '}
              <span>
                {createdRun.business_type} in {createdRun.city} (max {createdRun.max_results})
              </span>{' '}
              <span aria-hidden="true">·</span>{' '}
              <span className="font-medium">
                Status: {createdRun.status.toUpperCase()}
              </span>
              {createdRun.status === 'pending' && (
                <span className="text-slate-600">
                  {' '}
                  (Recorded in SQLite; configure N8N_WEBHOOK_URL to dispatch live scraping)
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
