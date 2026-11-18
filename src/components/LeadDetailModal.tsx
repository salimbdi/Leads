import React, { useState } from 'react';
import { Copy, Check, ExternalLink, X } from 'lucide-react';
import { LeadDetailResponse } from '../types/leadforge';
import {
  DIMENSION_DISPLAY_ORDER,
  formatDimensionName,
  formatTimestampCompact,
  getPrioritySemanticClass,
  normalizeExternalWebsiteUrl,
} from '../utils/leadforgeLogic';

interface LeadDetailModalProps {
  lead: LeadDetailResponse | null;
  isLoading: boolean;
  error: string | null;
  onClose: () => void;
}

export const LeadDetailModal: React.FC<LeadDetailModalProps> = ({
  lead,
  isLoading,
  error,
  onClose,
}) => {
  const [copiedIcebreaker, setCopiedIcebreaker] = useState(false);

  if (!lead && !isLoading && !error) {
    return null;
  }

  const externalUrl = normalizeExternalWebsiteUrl(lead?.website);

  const handleCopyIcebreaker = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIcebreaker(true);
      setTimeout(() => setCopiedIcebreaker(false), 2000);
    } catch {
      // Clipboard API unavailable in non-secure context
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="lead-detail-title"
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/40 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl bg-white h-full overflow-y-auto border-l border-slate-200 flex flex-col justify-between"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          {/* Top Header */}
          <div className="p-6 border-b border-slate-200 flex items-start justify-between gap-4 sticky top-0 bg-white z-10">
            <div>
              <div className="text-xs text-slate-500">
                Lead Qualification Dossier {lead ? `· ID #${lead.id}` : ''}
              </div>
              <h2 id="lead-detail-title" className="mt-1 text-lg font-bold text-slate-900">
                {isLoading ? 'Loading lead details...' : lead?.company_name || 'Lead Details'}
              </h2>
              {lead && (
                <div className="mt-1 text-xs text-slate-600">
                  <span>{lead.business_type}</span>
                  <span aria-hidden="true" className="mx-1.5 text-slate-400">
                    ·
                  </span>
                  <span>{lead.city}</span>
                  <span aria-hidden="true" className="mx-1.5 text-slate-400">
                    ·
                  </span>
                  <span className={getPrioritySemanticClass(lead.priority)}>
                    {lead.priority} Priority ({lead.score}/100)
                  </span>
                </div>
              )}
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close lead details"
              className="p-2 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {error && (
            <div role="alert" className="m-6 p-4 bg-red-50 border border-red-200 rounded-lg text-xs text-red-800">
              <span className="font-semibold">Failed to load lead details: </span>
              <span>{error}</span>
            </div>
          )}

          {lead && (
            <div className="p-6 space-y-8">
              {/* Section 1: Public Business & Contact Information */}
              <section aria-labelledby="detail-contact-heading">
                <h3 id="detail-contact-heading" className="text-sm font-bold text-slate-900 pb-2 border-b border-slate-200">
                  01. Business & Public Contact Information
                </h3>
                <dl className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-xs">
                  <div>
                    <dt className="text-slate-500">Business Name</dt>
                    <dd className="mt-0.5 font-semibold text-slate-900">{lead.company_name}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Category & City</dt>
                    <dd className="mt-0.5 font-medium text-slate-900">
                      {lead.business_type} · {lead.city}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Public Phone</dt>
                    <dd className="mt-0.5 font-mono tabular-nums text-slate-900">
                      {lead.phone ? (
                        <a href={`tel:${lead.phone}`} className="text-blue-700 hover:underline">
                          {lead.phone}
                        </a>
                      ) : (
                        <span className="text-slate-400">No phone number listed</span>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Website URL</dt>
                    <dd className="mt-0.5">
                      {externalUrl ? (
                        <a
                          href={externalUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-blue-700 hover:underline font-medium break-all"
                        >
                          <span>{lead.website}</span>
                          <ExternalLink className="w-3.5 h-3.5 shrink-0" />
                        </a>
                      ) : (
                        <span className="font-semibold text-emerald-800">
                          No website listed (Website build opportunity)
                        </span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-slate-500">Street Address</dt>
                    <dd className="mt-0.5 text-slate-900">
                      {lead.address || <span className="text-slate-400">No street address listed</span>}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Public Rating & Reviews</dt>
                    <dd className="mt-0.5 font-mono tabular-nums text-slate-900">
                      {lead.rating !== null ? (
                        <>
                          <span className="font-semibold">{lead.rating.toFixed(1)} / 5.0</span>
                          <span> ({lead.reviews_count ?? 0} customer reviews)</span>
                        </>
                      ) : (
                        <span className="text-slate-400">No rating or reviews recorded</span>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Discovered In Search Runs</dt>
                    <dd className="mt-0.5 font-mono tabular-nums text-slate-700">
                      {lead.search_runs && lead.search_runs.length > 0
                        ? lead.search_runs
                            .map((r) => `Run #${r.id} (${r.business_type} · ${r.city})`)
                            .join(', ')
                        : `Updated ${formatTimestampCompact(lead.updated_at)}`}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* Section 2: Deterministic Lead Scoring Breakdown */}
              <section aria-labelledby="detail-scoring-heading">
                <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2 pb-2 border-b border-slate-200">
                  <div>
                    <h3 id="detail-scoring-heading" className="text-sm font-bold text-slate-900">
                      02. Deterministic Lead Score Breakdown
                    </h3>
                    <p className="text-[11px] text-slate-500">
                      Calculated by FastAPI rule engine ({lead.score_breakdown.formula_version}) — independent of AI confidence.
                    </p>
                  </div>
                  <div className="font-mono tabular-nums text-sm">
                    <span className="font-bold text-slate-900">{lead.score}</span>
                    <span className="text-slate-400"> / {lead.score_breakdown.max_score} pts</span>
                    <span aria-hidden="true" className="mx-1.5 text-slate-300">
                      ·
                    </span>
                    <span className={getPrioritySemanticClass(lead.priority)}>
                      {lead.priority} Priority
                    </span>
                  </div>
                </div>

                <div className="mt-4 space-y-4">
                  {DIMENSION_DISPLAY_ORDER.map((dimKey) => {
                    const dim = lead.score_breakdown.dimensions[dimKey];
                    if (!dim) return null;
                    const pct = Math.min(100, Math.round((dim.score / dim.max_score) * 100));
                    return (
                      <div key={dimKey} className="pb-3 border-b border-slate-100 last:border-b-0">
                        <div className="flex items-center justify-between text-xs font-semibold text-slate-800">
                          <span>{formatDimensionName(dimKey)}</span>
                          <span className="font-mono tabular-nums">
                            {dim.score} / {dim.max_score} pts
                          </span>
                        </div>
                        <div className="mt-1.5 w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-slate-900 rounded-full"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        {dim.reasons.length > 0 && (
                          <ul className="mt-2 space-y-1 text-xs text-slate-600">
                            {dim.reasons.map((reason, idx) => (
                              <li key={idx}>• {reason}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              {/* Section 3: AI Qualitative Evidence & Outreach Recommendations */}
              <section aria-labelledby="detail-ai-heading">
                <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2 pb-2 border-b border-slate-200">
                  <div>
                    <h3 id="detail-ai-heading" className="text-sm font-bold text-slate-900">
                      03. AI Qualitative Analysis & Outreach Evidence
                    </h3>
                    <p className="text-[11px] text-slate-500">
                      Structured qualitative evidence extracted during n8n workflow qualification.
                    </p>
                  </div>
                  {lead.analysis?.confidence !== null && lead.analysis?.confidence !== undefined && (
                    <div className="text-xs text-slate-600 font-mono tabular-nums">
                      AI Evidence Confidence: {Math.round(lead.analysis.confidence * 100)}%
                    </div>
                  )}
                </div>

                {!lead.analysis ? (
                  <div className="mt-3 p-4 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-600">
                    No AI qualitative analysis record is stored for this lead. The deterministic score above was computed from public business, category, website, and contact attributes.
                  </div>
                ) : (
                  <dl className="mt-4 space-y-4 text-xs">
                    <div>
                      <dt className="font-semibold text-slate-800">Business Summary</dt>
                      <dd className="mt-1 text-slate-700 leading-relaxed">
                        {lead.analysis.business_summary || 'No summary extracted.'}
                      </dd>
                    </div>

                    <div>
                      <dt className="font-semibold text-slate-800">Personalization Observation</dt>
                      <dd className="mt-1 text-slate-700 leading-relaxed">
                        {lead.analysis.personalization_observation || 'No specific observation recorded.'}
                      </dd>
                    </div>

                    <div>
                      <dt className="font-semibold text-slate-800">Recommended Service Opportunity</dt>
                      <dd className="mt-1 text-slate-900 font-medium leading-relaxed">
                        {lead.analysis.potential_opportunity || 'No specific service recommendation recorded.'}
                      </dd>
                    </div>

                    {lead.analysis.icebreaker && (
                      <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg">
                        <div className="flex items-center justify-between gap-2">
                          <dt className="font-semibold text-slate-900">
                            Suggested Manual Outreach Icebreaker
                          </dt>
                          <button
                            type="button"
                            onClick={() => handleCopyIcebreaker(lead.analysis!.icebreaker!)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-100 transition-colors cursor-pointer"
                          >
                            {copiedIcebreaker ? (
                              <>
                                <Check className="w-3 h-3 text-emerald-600" />
                                <span>Copied</span>
                              </>
                            ) : (
                              <>
                                <Copy className="w-3 h-3" />
                                <span>Copy Icebreaker</span>
                              </>
                            )}
                          </button>
                        </div>
                        <dd className="mt-1.5 text-slate-800 leading-relaxed">
                          “{lead.analysis.icebreaker}”
                        </dd>
                      </div>
                    )}
                  </dl>
                )}
              </section>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-200 bg-slate-50 flex items-center justify-between text-xs text-slate-500">
          <span>Manual qualification review · No automatic outreach is sent</span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-xs font-semibold text-slate-800 bg-white border border-slate-300 rounded-lg hover:bg-slate-100 transition-colors cursor-pointer"
          >
            Close Dossier
          </button>
        </div>
      </div>
    </div>
  );
};
