import React from 'react';
import { CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react';
import { HealthCheckLogEntry, HealthResponse } from '../types/foundation';
import { calculateAverageLatency } from '../utils/foundationLogic';

interface HealthMonitorPanelProps {
  healthData: HealthResponse | null;
  healthError: string | null;
  isChecking: boolean;
  checkLogs: HealthCheckLogEntry[];
  onRunHealthCheck: (endpoint?: '/api/health' | '/health') => void;
}

export const HealthMonitorPanel: React.FC<HealthMonitorPanelProps> = ({
  healthData,
  healthError,
  isChecking,
  checkLogs,
  onRunHealthCheck,
}) => {
  const avgLatency = calculateAverageLatency(checkLogs);

  return (
    <div className="space-y-8">
      {/* Section Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-5 border-b border-slate-200">
        <div>
          <p className="text-xs font-medium text-slate-500">
            Phase 1 — Project Foundation · Live Backend Verification
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
            Frontend & FastAPI Connectivity Monitor
          </h1>
          <p className="mt-1 text-sm text-slate-600 max-w-2xl">
            Verifies live communication between the React + TypeScript + Vite dashboard and the Python FastAPI backend before database models or workflow triggers are added in Phase 2.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => onRunHealthCheck('/api/health')}
            disabled={isChecking}
            className="px-4 py-2 text-xs font-semibold text-slate-800 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            Ping /api/health
          </button>
          <button
            type="button"
            onClick={() => onRunHealthCheck('/health')}
            disabled={isChecking}
            className="px-4 py-2 text-xs font-semibold text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            Ping /health
          </button>
        </div>
      </div>

      {/* Error State */}
      {healthError && (
        <div
          role="alert"
          className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start justify-between gap-4"
        >
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 shrink-0 mt-0.5" />
            <div>
              <h2 className="text-sm font-semibold text-red-900">
                Backend Health Check Failed
              </h2>
              <p className="mt-1 text-xs text-red-700">{healthError}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onRunHealthCheck('/api/health')}
            className="px-3 py-1.5 text-xs font-medium text-red-900 bg-white border border-red-300 rounded-md hover:bg-red-100 whitespace-nowrap shrink-0 cursor-pointer"
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Top Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-4 bg-white border border-slate-200 rounded-lg">
          <div className="text-xs font-medium text-slate-500">
            Backend Service State
          </div>
          <div className="mt-2 flex items-center gap-2">
            {healthData?.status === 'ok' ? (
              <>
                <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                <span className="text-lg font-bold text-slate-900">
                  Healthy (200 OK)
                </span>
              </>
            ) : (
              <span className="text-lg font-bold text-slate-500">
                {isChecking ? 'Checking...' : 'Unavailable'}
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            {healthData ? `${healthData.service} · ${healthData.environment}` : 'Awaiting response'}
          </div>
        </div>

        <div className="p-4 bg-white border border-slate-200 rounded-lg">
          <div className="text-xs font-medium text-slate-500">
            Backend Runtime Stack
          </div>
          <div className="mt-2 text-lg font-bold text-slate-900 font-mono tabular-nums">
            {healthData
              ? `FastAPI ${healthData.runtime.fastapi_version}`
              : 'Loading...'}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            {healthData
              ? `Python ${healthData.runtime.python_version} · SQLAlchemy ${healthData.runtime.sqlalchemy_version}`
              : 'Python + SQLAlchemy'}
          </div>
        </div>

        <div className="p-4 bg-white border border-slate-200 rounded-lg">
          <div className="text-xs font-medium text-slate-500">
            AI Provider & Model
          </div>
          <div className="mt-2 text-lg font-bold text-slate-900 font-mono">
            {healthData ? healthData.config.ai_provider : 'mock'}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono">
            {healthData
              ? `${healthData.config.ai_model} · Thinking: ${healthData.config.ai_thinking_level}`
              : 'gemini-4-carbon · HIGH'}
          </div>
        </div>

        <div className="p-4 bg-white border border-slate-200 rounded-lg">
          <div className="text-xs font-medium text-slate-500">
            Mean Health Latency
          </div>
          <div className="mt-2 text-lg font-bold text-slate-900 font-mono tabular-nums">
            {checkLogs.length > 0 ? `${avgLatency} ms` : '—'}
          </div>
          <div className="mt-1 text-xs text-slate-500 font-mono tabular-nums">
            {checkLogs.length} verified check{checkLogs.length === 1 ? '' : 's'} this session
          </div>
        </div>
      </div>

      {/* Two-Column Split: Configuration Summary & Live JSON Response */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
        <section className="p-5 bg-white border border-slate-200 rounded-lg flex flex-col justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">
              Non-Secret Environment Configuration
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Loaded by Pydantic Settings in <code className="font-mono">backend/app/config.py</code>. Secrets and tokens are never serialized to the client.
            </p>

            {healthData ? (
              <dl className="mt-4 divide-y divide-slate-100 text-xs">
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">Application Name</dt>
                  <dd className="font-mono text-slate-900">{healthData.config.app_name}</dd>
                </div>
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">Environment Mode</dt>
                  <dd className="font-mono text-slate-900">{healthData.config.environment}</dd>
                </div>
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">Source of Truth Database</dt>
                  <dd className="font-mono text-slate-900">{healthData.config.database_engine}</dd>
                </div>
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">SQLite Target File</dt>
                  <dd className="font-mono text-slate-900">{healthData.config.database_target}</dd>
                </div>
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">AI Analysis Provider</dt>
                  <dd className="font-mono text-slate-900">
                    {healthData.config.ai_provider} ({healthData.config.ai_model})
                  </dd>
                </div>
                <div className="py-2.5 flex items-center justify-between gap-4">
                  <dt className="text-slate-600 font-medium">n8n Webhook URL Configured</dt>
                  <dd className="font-mono text-slate-900">
                    {healthData.config.n8n_webhook_configured ? 'Yes' : 'Deferred to Phase 4'}
                  </dd>
                </div>
              </dl>
            ) : (
              <div className="mt-6 py-8 text-center text-xs text-slate-500">
                Loading configuration summary from FastAPI...
              </div>
            )}
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-500">
            Security boundary verified · API keys and callback secrets remain strictly server-side
          </div>
        </section>

        <section className="p-5 bg-white border border-slate-200 rounded-lg flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-slate-900">
                  Live FastAPI Health Payload
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  Direct JSON response returned by <code className="font-mono">GET /api/health</code>
                </p>
              </div>
              <span className="text-xs font-mono text-slate-500 tabular-nums whitespace-nowrap shrink-0">
                {healthData ? healthData.timestamp.replace(/\.\d+/, '') : ''}
              </span>
            </div>

            <pre
              data-testid="health-json-payload"
              className="mt-4 p-4 bg-slate-900 text-slate-100 rounded-lg text-xs font-mono overflow-x-auto leading-relaxed max-h-64"
            >
              {healthData
                ? JSON.stringify(healthData, null, 2)
                : isChecking
                ? 'Fetching /api/health payload...'
                : 'No payload available'}
            </pre>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-500 flex items-center justify-between">
            <span>FastAPI Server: http://127.0.0.1:8001</span>
            <span>Vite Proxy: /api/health &amp; /health</span>
          </div>
        </section>
      </div>

      {/* Verification Log Table */}
      <section className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">
              Endpoint Verification Log
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Recorded HTTP health checks against the FastAPI backend during this session
            </p>
          </div>
          <button
            type="button"
            onClick={() => onRunHealthCheck('/api/health')}
            disabled={isChecking}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 rounded-md hover:bg-slate-200 disabled:opacity-50 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isChecking ? 'animate-spin' : ''}`} />
            <span>Trigger Check</span>
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-[11px] font-semibold text-slate-600">
                <th className="py-2.5 px-4">Timestamp (UTC)</th>
                <th className="py-2.5 px-4">Endpoint</th>
                <th className="py-2.5 px-4">Service</th>
                <th className="py-2.5 px-4">HTTP Status</th>
                <th className="py-2.5 px-4 text-right">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {checkLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-8 px-4 text-center text-slate-500">
                    No health checks recorded yet. Click &ldquo;Run Health Check&rdquo; to verify the backend.
                  </td>
                </tr>
              ) : (
                checkLogs.map((entry) => (
                  <tr key={entry.id} className="hover:bg-slate-50 transition-colors">
                    <td className="py-2.5 px-4 font-mono text-slate-600 tabular-nums">
                      {entry.checkedAt}
                    </td>
                    <td className="py-2.5 px-4 font-mono font-medium text-slate-900">
                      GET {entry.endpoint}
                    </td>
                    <td className="py-2.5 px-4 font-mono text-slate-600">
                      {entry.service}
                    </td>
                    <td className="py-2.5 px-4 font-mono tabular-nums text-slate-800">
                      {entry.statusCode} {entry.statusText}
                    </td>
                    <td className="py-2.5 px-4 font-mono tabular-nums text-right text-slate-900">
                      {entry.latencyMs} ms
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};
