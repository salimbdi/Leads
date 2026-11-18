import React, { useState } from 'react';
import { Search } from 'lucide-react';
import {
  ArchitectureStep,
  PhaseFilter,
} from '../types/foundation';
import {
  ROADMAP_PHASES,
  filterRoadmapPhases,
  formatArchitectureStatus,
} from '../utils/foundationLogic';

interface ArchitectureRoadmapPanelProps {
  architectureFlow: ArchitectureStep[];
}

export const ArchitectureRoadmapPanel: React.FC<ArchitectureRoadmapPanelProps> = ({
  architectureFlow,
}) => {
  const [phaseFilter, setPhaseFilter] = useState<PhaseFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredPhases = filterRoadmapPhases(
    ROADMAP_PHASES,
    phaseFilter,
    searchQuery
  );

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="pb-5 border-b border-slate-200">
        <p className="text-xs font-medium text-slate-500">
          Target Architecture & Phased Delivery Plan
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
          LeadForge System Architecture & Implementation Roadmap
        </h1>
        <p className="mt-1 text-sm text-slate-600 max-w-3xl">
          SQLite serves as the single source of truth. n8n orchestrates Apify discovery and AI evidence extraction, while the FastAPI backend enforces deterministic lead qualification rules.
        </p>
      </div>

      {/* End-to-End Target Data Flow Table */}
      <section className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-900">
            Target Pipeline Flow
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            React Dashboard → FastAPI → n8n Webhook → Apify + AI Analysis → FastAPI Callback → SQLite → React Dashboard
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-[11px] font-semibold text-slate-600">
                <th className="py-2.5 px-4">Step</th>
                <th className="py-2.5 px-4">Architecture Node</th>
                <th className="py-2.5 px-4">Responsibility</th>
                <th className="py-2.5 px-4">Implementation Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {architectureFlow.map((node) => (
                <tr key={node.step} className="hover:bg-slate-50">
                  <td className="py-2.5 px-4 font-mono tabular-nums text-slate-500">
                    0{node.step}
                  </td>
                  <td className="py-2.5 px-4 font-semibold text-slate-900">
                    {node.component}
                  </td>
                  <td className="py-2.5 px-4 text-slate-600">{node.role}</td>
                  <td className="py-2.5 px-4 font-mono text-slate-700">
                    {formatArchitectureStatus(node.status)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Filterable Roadmap Section */}
      <section className="space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          {/* Interactive Segmented Filter Controls */}
          <div
            role="group"
            aria-label="Filter implementation phases"
            className="flex items-center gap-1 p-1 bg-slate-200/70 rounded-lg self-start"
          >
            <button
              type="button"
              onClick={() => setPhaseFilter('all')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                phaseFilter === 'all'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              All Phases (7)
            </button>
            <button
              type="button"
              onClick={() => setPhaseFilter('active')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                phaseFilter === 'active'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Active Phase (1)
            </button>
            <button
              type="button"
              onClick={() => setPhaseFilter('upcoming')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                phaseFilter === 'upcoming'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Upcoming Phases (6)
            </button>
          </div>

          {/* Search input */}
          <div className="relative w-full sm:w-72">
            <label htmlFor="roadmap-search" className="sr-only">
              Search roadmap phases
            </label>
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              id="roadmap-search"
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter deliverables (e.g. SQLite, n8n)..."
              className="w-full pl-9 pr-3 py-1.5 text-xs bg-white border border-slate-300 rounded-lg text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-600"
            />
          </div>
        </div>

        {/* Phase List */}
        {filteredPhases.length === 0 ? (
          <div className="p-8 bg-white border border-slate-200 rounded-lg text-center">
            <p className="text-sm font-medium text-slate-800">
              No phases match &ldquo;{searchQuery}&rdquo;
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Reset the filter or search query to inspect all 7 roadmap phases.
            </p>
            <button
              type="button"
              onClick={() => {
                setPhaseFilter('all');
                setSearchQuery('');
              }}
              className="mt-3 px-3 py-1.5 text-xs font-medium text-white bg-slate-900 rounded-md hover:bg-slate-800 cursor-pointer"
            >
              Reset Filter
            </button>
          </div>
        ) : (
          <div className="divide-y divide-slate-200 bg-white border border-slate-200 rounded-lg">
            {filteredPhases.map((phase) => (
              <article key={phase.phaseNumber} className="p-5">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <h3 className="text-sm font-bold text-slate-900">
                    0{phase.phaseNumber}. {phase.title}
                  </h3>
                  <div className="text-xs text-slate-500 font-mono">
                    {phase.state === 'active'
                      ? 'Status: Active & Verified · Phase 1'
                      : `Status: Planned · Awaiting Phase ${phase.phaseNumber} Approval`}
                  </div>
                </div>
                <p className="mt-1.5 text-xs text-slate-600 leading-relaxed">
                  {phase.summary}
                </p>
                <ul className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-1.5 text-xs text-slate-700">
                  {phase.deliverables.map((item) => (
                    <li key={item} className="flex items-baseline gap-2">
                      <span aria-hidden="true" className="text-slate-400">
                        ·
                      </span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
