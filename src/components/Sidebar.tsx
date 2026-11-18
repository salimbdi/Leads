import React from 'react';
import {
  LayoutDashboard,
  Server,
  Layers,
  Cpu,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { WorkspaceSection } from './TopNav';
import { HealthResponse } from '../types/foundation';

interface SidebarProps {
  activeSection: WorkspaceSection;
  onSelectSection: (section: WorkspaceSection) => void;
  healthData: HealthResponse | null;
  healthError: string | null;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeSection,
  onSelectSection,
  healthData,
  healthError,
}) => {
  const navItems: {
    id: WorkspaceSection;
    label: string;
    sublabel: string;
    icon: React.ComponentType<{ className?: string }>;
  }[] = [
    {
      id: 'dashboard',
      label: 'Lead Discovery & Scoring',
      sublabel: 'Search runs, leads & score dossiers',
      icon: LayoutDashboard,
    },
    {
      id: 'health',
      label: 'System Health & Config',
      sublabel: 'FastAPI /health & runtime telemetry',
      icon: Server,
    },
    {
      id: 'architecture',
      label: 'Architecture & Phases',
      sublabel: 'Target pipeline & 7-phase roadmap',
      icon: Layers,
    },
    {
      id: 'thinking',
      label: 'AI Thinking Sandbox',
      sublabel: 'gemini-4-carbon HIGH reasoning',
      icon: Cpu,
    },
  ];

  return (
    <aside className="w-full lg:w-64 shrink-0 bg-white border-b lg:border-b-0 lg:border-r border-slate-200 flex flex-col justify-between">
      <div className="p-5 space-y-6">
        <div>
          <p className="text-xs font-medium text-slate-500">
            Commercial Qualification Suite
          </p>
          <h2 className="mt-1 text-sm font-semibold text-slate-900">
            Workspace Modules
          </h2>
        </div>

        <nav aria-label="Sidebar Module Navigation" className="space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeSection === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelectSection(item.id)}
                className={`w-full flex items-start gap-3 px-3 py-2.5 rounded-lg text-left transition-colors cursor-pointer ${
                  isActive
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-700 hover:bg-slate-100'
                }`}
              >
                <Icon
                  className={`w-4 h-4 mt-0.5 shrink-0 ${
                    isActive ? 'text-blue-400' : 'text-slate-500'
                  }`}
                />
                <div className="min-w-0">
                  <div className="text-xs font-semibold whitespace-nowrap truncate">
                    {item.label}
                  </div>
                  <div
                    className={`text-[11px] truncate ${
                      isActive ? 'text-slate-300' : 'text-slate-500'
                    }`}
                  >
                    {item.sublabel}
                  </div>
                </div>
              </button>
            );
          })}
        </nav>

        <div className="pt-4 border-t border-slate-200 space-y-2">
          <div className="text-xs font-semibold text-slate-900">
            Operator Workflow
          </div>
          <ol className="space-y-1.5 text-xs text-slate-500">
            <li>01. Launch a niche & city search</li>
            <li>02. Monitor n8n batch execution</li>
            <li>03. Sort leads by deterministic score</li>
            <li>04. Inspect 5-dimension breakdown</li>
            <li>05. Review AI evidence & outreach</li>
          </ol>
        </div>
      </div>

      <div className="p-5 border-t border-slate-200 bg-slate-50/70">
        <div className="flex items-center gap-2 text-xs font-medium text-slate-800">
          {healthError ? (
            <>
              <AlertTriangle className="w-4 h-4 text-red-600 shrink-0" />
              <span>FastAPI Status: Unreachable</span>
            </>
          ) : healthData?.status === 'ok' ? (
            <>
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
              <span>FastAPI Status: Operational</span>
            </>
          ) : (
            <span>FastAPI Status: Connecting...</span>
          )}
        </div>
        {healthData && (
          <div className="mt-1.5 text-[11px] text-slate-500 font-mono tabular-nums">
            {healthData.service} ·{' '}
            {healthData.version.startsWith('v')
              ? healthData.version
              : `v${healthData.version}`}{' '}
            · Python {healthData.runtime.python_version}
          </div>
        )}
      </div>
    </aside>
  );
};
