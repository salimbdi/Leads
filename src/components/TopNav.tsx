import React from 'react';
import { RefreshCw } from 'lucide-react';

export type WorkspaceSection = 'dashboard' | 'health' | 'architecture' | 'thinking';

interface TopNavProps {
  activeSection: WorkspaceSection;
  onSelectSection: (section: WorkspaceSection) => void;
  onPrimaryAction: () => void;
  isBusy: boolean;
}

export const TopNav: React.FC<TopNavProps> = ({
  activeSection,
  onSelectSection,
  onPrimaryAction,
  isBusy,
}) => {
  return (
    <header className="flex items-center justify-between gap-8 px-6 py-4 bg-white border-b border-slate-200">
      {/* Zone 1: Single text element wordmark */}
      <a
        href="#top"
        onClick={(e) => {
          e.preventDefault();
          onSelectSection('dashboard');
        }}
        className="text-lg font-bold tracking-tight text-slate-900 whitespace-nowrap shrink-0"
      >
        LeadForge
      </a>

      {/* Zone 2: 4 concise single-line text navigation links */}
      <nav
        aria-label="Primary Workspace Navigation"
        className="hidden md:flex items-center gap-6 text-sm font-medium text-slate-600"
      >
        <a
          href="#dashboard"
          onClick={(e) => {
            e.preventDefault();
            onSelectSection('dashboard');
          }}
          className={`transition-colors whitespace-nowrap shrink-0 hover:text-slate-900 ${
            activeSection === 'dashboard'
              ? 'text-slate-900 underline underline-offset-8 decoration-2 decoration-blue-600'
              : ''
          }`}
        >
          Lead Workspace
        </a>
        <a
          href="#health"
          onClick={(e) => {
            e.preventDefault();
            onSelectSection('health');
          }}
          className={`transition-colors whitespace-nowrap shrink-0 hover:text-slate-900 ${
            activeSection === 'health'
              ? 'text-slate-900 underline underline-offset-8 decoration-2 decoration-blue-600'
              : ''
          }`}
        >
          Backend Health
        </a>
        <a
          href="#architecture"
          onClick={(e) => {
            e.preventDefault();
            onSelectSection('architecture');
          }}
          className={`transition-colors whitespace-nowrap shrink-0 hover:text-slate-900 ${
            activeSection === 'architecture'
              ? 'text-slate-900 underline underline-offset-8 decoration-2 decoration-blue-600'
              : ''
          }`}
        >
          Architecture & Roadmap
        </a>
        <a
          href="#thinking"
          onClick={(e) => {
            e.preventDefault();
            onSelectSection('thinking');
          }}
          className={`transition-colors whitespace-nowrap shrink-0 hover:text-slate-900 ${
            activeSection === 'thinking'
              ? 'text-slate-900 underline underline-offset-8 decoration-2 decoration-blue-600'
              : ''
          }`}
        >
          AI Qualification Engine
        </a>
      </nav>

      {/* Zone 3: 1 primary action */}
      <div className="flex items-center gap-3 shrink-0">
        <button
          type="button"
          onClick={onPrimaryAction}
          disabled={isBusy}
          className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800 disabled:opacity-60 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isBusy ? 'animate-spin' : ''}`} />
          <span>{isBusy ? 'Refreshing...' : 'Refresh Workspace'}</span>
        </button>
      </div>
    </header>
  );
};
