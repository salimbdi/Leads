import React from 'react';
import {
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  RotateCcw,
  Search,
} from 'lucide-react';
import {
  LeadPriority,
  LeadQueryFilters,
  LeadSortField,
  LeadSummaryResponse,
} from '../types/leadforge';
import {
  formatWebsiteDisplayHost,
  getPrioritySemanticClass,
  normalizeExternalWebsiteUrl,
} from '../utils/leadforgeLogic';

interface LeadsTablePanelProps {
  leads: LeadSummaryResponse[];
  totalLeads: number;
  filters: LeadQueryFilters;
  isLoading: boolean;
  error: string | null;
  onUpdateFilters: (patch: Partial<LeadQueryFilters>) => void;
  onResetFilters: () => void;
  onRefresh: () => void;
  onSelectLead: (leadId: number) => void;
  selectedLeadId: number | null;
}

export const LeadsTablePanel: React.FC<LeadsTablePanelProps> = ({
  leads,
  totalLeads,
  filters,
  isLoading,
  error,
  onUpdateFilters,
  onResetFilters,
  onRefresh,
  onSelectLead,
  selectedLeadId,
}) => {
  const priorityTabs: { value: 'all' | LeadPriority; label: string }[] = [
    { value: 'all', label: 'All Leads' },
    { value: 'High', label: 'High Priority' },
    { value: 'Medium', label: 'Medium Priority' },
    { value: 'Low', label: 'Low Priority' },
  ];

  const handleToggleSort = (field: LeadSortField) => {
    if (filters.sortBy === field) {
      onUpdateFilters({
        sortOrder: filters.sortOrder === 'desc' ? 'asc' : 'desc',
        offset: 0,
      });
    } else {
      onUpdateFilters({
        sortBy: field,
        sortOrder: field === 'company_name' ? 'asc' : 'desc',
        offset: 0,
      });
    }
  };

  const currentPage = Math.floor(filters.offset / filters.limit) + 1;
  const totalPages = Math.max(1, Math.ceil(totalLeads / filters.limit));
  const hasPrevPage = filters.offset > 0;
  const hasNextPage = filters.offset + filters.limit < totalLeads;

  const hasActiveFilters =
    filters.priority !== 'all' ||
    filters.minScore !== '' ||
    filters.q.trim() !== '' ||
    filters.city.trim() !== '' ||
    filters.businessType.trim() !== '' ||
    filters.searchRunId !== null ||
    filters.hasWebsite !== 'all';

  return (
    <section
      aria-labelledby="leads-table-heading"
      className="bg-white border border-slate-200 rounded-xl overflow-hidden"
    >
      {/* Header & Priority Tabs */}
      <div className="p-5 border-b border-slate-200 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="leads-table-heading" className="text-base font-bold text-slate-900">
              03. Qualified Business Leads
            </h2>
            {filters.searchRunId !== null && (
              <span className="text-xs font-medium text-blue-700 font-mono tabular-nums">
                · Filtered by Search Run #{filters.searchRunId}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-600 font-mono tabular-nums">
            Showing {leads.length} of {totalLeads} leads · Sorted by{' '}
            {filters.sortBy === 'score'
              ? 'Deterministic Score'
              : filters.sortBy === 'created_at'
                ? 'Discovery Date'
                : filters.sortBy}{' '}
            ({filters.sortOrder.toUpperCase()})
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Filter leads by deterministic priority"
            className="flex items-center gap-1 p-1 bg-slate-100 rounded-lg"
          >
            {priorityTabs.map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => onUpdateFilters({ priority: tab.value, offset: 0 })}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                  filters.priority === tab.value
                    ? 'bg-white text-slate-900 shadow-xs'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                {tab.label}
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
            <span>Refresh Leads</span>
          </button>
        </div>
      </div>

      {/* Filter & Sort Controls Bar */}
      <div className="p-4 bg-slate-50/70 border-b border-slate-200 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 gap-3 items-end">
        <div className="lg:col-span-4">
          <label
            htmlFor="leads-search-input"
            className="block text-[11px] font-semibold text-slate-700 mb-1"
          >
            Search Business Name
          </label>
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              id="leads-search-input"
              type="text"
              value={filters.q}
              onChange={(e) => onUpdateFilters({ q: e.target.value, offset: 0 })}
              placeholder="Search company name..."
              className="w-full pl-8 pr-3 py-1.5 text-xs text-slate-900 bg-white border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600"
            />
          </div>
        </div>

        <div className="lg:col-span-2">
          <label
            htmlFor="leads-min-score-input"
            className="block text-[11px] font-semibold text-slate-700 mb-1"
          >
            Minimum Score (0–100)
          </label>
          <input
            id="leads-min-score-input"
            type="number"
            min={0}
            max={100}
            value={filters.minScore}
            onChange={(e) => {
              const raw = e.target.value;
              onUpdateFilters({
                minScore: raw === '' ? '' : Math.max(0, Math.min(100, Number(raw))),
                offset: 0,
              });
            }}
            placeholder="0"
            className="w-full px-3 py-1.5 text-xs text-slate-900 bg-white border border-slate-300 rounded-lg font-mono tabular-nums focus:outline-none focus:ring-2 focus:ring-blue-600"
          />
        </div>

        <div className="lg:col-span-2">
          <label
            htmlFor="leads-website-filter"
            className="block text-[11px] font-semibold text-slate-700 mb-1"
          >
            Website Presence
          </label>
          <select
            id="leads-website-filter"
            value={filters.hasWebsite}
            onChange={(e) =>
              onUpdateFilters({
                hasWebsite: e.target.value as LeadQueryFilters['hasWebsite'],
                offset: 0,
              })
            }
            className="w-full px-2.5 py-1.5 text-xs text-slate-900 bg-white border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600"
          >
            <option value="all">All Websites</option>
            <option value="no">Missing Website Only</option>
            <option value="yes">Has Website Only</option>
          </select>
        </div>

        <div className="lg:col-span-2">
          <label
            htmlFor="leads-sort-select"
            className="block text-[11px] font-semibold text-slate-700 mb-1"
          >
            Sort Order
          </label>
          <select
            id="leads-sort-select"
            value={`${filters.sortBy}:${filters.sortOrder}`}
            onChange={(e) => {
              const [sortBy, sortOrder] = e.target.value.split(':') as [
                LeadSortField,
                'asc' | 'desc',
              ];
              onUpdateFilters({ sortBy, sortOrder, offset: 0 });
            }}
            className="w-full px-2.5 py-1.5 text-xs text-slate-900 bg-white border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600"
          >
            <option value="score:desc">Score: High to Low</option>
            <option value="score:asc">Score: Low to High</option>
            <option value="created_at:desc">Newest Discovered</option>
            <option value="rating:desc">Rating: High to Low</option>
            <option value="reviews_count:desc">Reviews: Most to Least</option>
            <option value="company_name:asc">Business Name: A to Z</option>
          </select>
        </div>

        <div className="lg:col-span-2 flex items-center justify-end">
          {hasActiveFilters && (
            <button
              type="button"
              onClick={onResetFilters}
              className="w-full sm:w-auto inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-100 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Clear Filters</span>
            </button>
          )}
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="p-4 bg-red-50 border-b border-red-200 text-xs text-red-800"
        >
          <span className="font-semibold">Error loading leads: </span>
          <span>{error}</span>
        </div>
      )}

      {/* Leads Data Grid */}
      {leads.length === 0 ? (
        <div className="p-10 text-center">
          <p className="text-sm font-medium text-slate-800">
            {isLoading
              ? 'Loading discovered business leads...'
              : 'No business leads match the active filters.'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {hasActiveFilters
              ? 'Try clearing your active priority, minimum score, or search query filters.'
              : 'Launch a business discovery search above to populate leads.'}
          </p>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={onResetFilters}
              className="mt-4 inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset All Lead Filters</span>
            </button>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/70 text-[11px] font-semibold text-slate-600">
                <th className="py-2.5 px-4">
                  <button
                    type="button"
                    onClick={() => handleToggleSort('company_name')}
                    className="inline-flex items-center gap-1 hover:text-slate-900 cursor-pointer whitespace-nowrap"
                  >
                    <span>Business Name</span>
                    <ArrowUpDown className="w-3 h-3" />
                  </button>
                </th>
                <th className="py-2.5 px-4">Category & City</th>
                <th className="py-2.5 px-4">Website</th>
                <th className="py-2.5 px-4">Phone</th>
                <th className="py-2.5 px-4 text-right">
                  <button
                    type="button"
                    onClick={() => handleToggleSort('rating')}
                    className="inline-flex items-center gap-1 hover:text-slate-900 cursor-pointer whitespace-nowrap"
                  >
                    <span>Rating & Reviews</span>
                    <ArrowUpDown className="w-3 h-3" />
                  </button>
                </th>
                <th className="py-2.5 px-4 text-right">
                  <button
                    type="button"
                    onClick={() => handleToggleSort('score')}
                    className="inline-flex items-center gap-1 hover:text-slate-900 cursor-pointer whitespace-nowrap"
                  >
                    <span>Score (0–100)</span>
                    <ArrowUpDown className="w-3 h-3" />
                  </button>
                </th>
                <th className="py-2.5 px-4">Priority</th>
                <th className="py-2.5 px-4 text-right">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {leads.map((lead) => {
                const externalUrl = normalizeExternalWebsiteUrl(lead.website);
                const websiteLabel = formatWebsiteDisplayHost(lead.website);
                const isSelected = selectedLeadId === lead.id;
                const isHighPriority = lead.priority === 'High';

                return (
                  <tr
                    key={lead.id}
                    onClick={() => onSelectLead(lead.id)}
                    className={`transition-colors cursor-pointer ${
                      isSelected
                        ? 'bg-blue-50/70'
                        : isHighPriority
                          ? 'bg-emerald-50/20 hover:bg-slate-50'
                          : 'hover:bg-slate-50'
                    }`}
                  >
                    <td className="py-3 px-4">
                      <div className="font-semibold text-slate-900">{lead.company_name}</div>
                      {lead.analysis?.potential_opportunity && (
                        <div className="mt-0.5 text-[11px] text-slate-500 truncate max-w-xs">
                          {lead.analysis.potential_opportunity}
                        </div>
                      )}
                    </td>

                    <td className="py-3 px-4 whitespace-nowrap text-slate-700">
                      <span>{lead.business_type}</span>
                      <span aria-hidden="true" className="mx-1.5 text-slate-400">
                        ·
                      </span>
                      <span className="text-slate-500">{lead.city}</span>
                    </td>

                    <td
                      className="py-3 px-4 whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {externalUrl ? (
                        <a
                          href={externalUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-blue-700 hover:underline font-medium"
                        >
                          <span className="truncate max-w-[160px]">{websiteLabel}</span>
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <span className="text-emerald-800 font-medium">
                          No website (Build opportunity)
                        </span>
                      )}
                    </td>

                    <td className="py-3 px-4 font-mono tabular-nums text-slate-700 whitespace-nowrap">
                      {lead.phone || <span className="text-slate-400">Not listed</span>}
                    </td>

                    <td className="py-3 px-4 text-right font-mono tabular-nums whitespace-nowrap text-slate-700">
                      {lead.rating !== null ? (
                        <>
                          <span className="font-semibold text-slate-900">
                            {lead.rating.toFixed(1)}
                          </span>
                          <span className="text-slate-400"> / 5.0</span>
                          <span aria-hidden="true" className="mx-1 text-slate-300">
                            ·
                          </span>
                          <span className="text-slate-500">
                            {lead.reviews_count ?? 0} rev
                          </span>
                        </>
                      ) : (
                        <span className="text-slate-400">Unrated</span>
                      )}
                    </td>

                    <td className="py-3 px-4 text-right font-mono tabular-nums whitespace-nowrap">
                      <span
                        data-testid={`lead-score-${lead.id}`}
                        className="text-sm font-bold text-slate-900"
                      >
                        {lead.score}
                      </span>
                      <span className="text-slate-400"> / 100</span>
                    </td>

                    <td className="py-3 px-4 whitespace-nowrap">
                      <span
                        data-testid={`lead-priority-${lead.id}`}
                        className={getPrioritySemanticClass(lead.priority)}
                      >
                        {lead.priority}
                      </span>
                    </td>

                    <td
                      className="py-3 px-4 text-right whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        onClick={() => onSelectLead(lead.id)}
                        aria-label={`Inspect ${lead.company_name}`}
                        className="px-3 py-1 text-xs font-semibold text-slate-800 bg-white border border-slate-300 rounded-md hover:bg-slate-100 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
                      >
                        Inspect Lead
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination Footer */}
      <div className="px-5 py-3.5 bg-slate-50/70 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 text-xs text-slate-600">
        <div className="font-mono tabular-nums">
          Page {currentPage} of {totalPages} · Total matching leads: {totalLeads}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={!hasPrevPage || isLoading}
            onClick={() =>
              onUpdateFilters({
                offset: Math.max(0, filters.offset - filters.limit),
              })
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 disabled:opacity-40 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            <span>Previous</span>
          </button>

          <button
            type="button"
            disabled={!hasNextPage || isLoading}
            onClick={() =>
              onUpdateFilters({
                offset: filters.offset + filters.limit,
              })
            }
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 disabled:opacity-40 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <span>Next</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </section>
  );
};
