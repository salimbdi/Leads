import React, { useState } from 'react';
import { Cpu, Send } from 'lucide-react';

interface ThinkingResponse {
  provider: string;
  model: string;
  thinking_level: string;
  query: string;
  analysis: string;
  deterministic_note: string;
}

export const ThinkingEnginePanel: React.FC = () => {
  const [query, setQuery] = useState(
    'Explain how LeadForge separates AI qualitative evidence extraction from deterministic backend lead scoring for a local dental clinic with a 4.8 rating and 140 reviews.'
  );
  const [providerMode, setProviderMode] = useState<'mock' | 'auto'>('mock');
  const [result, setResult] = useState<ThinkingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setError('Please enter a qualification scenario or architectural query.');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/ai/think', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: trimmed, mode: providerMode }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || `Request failed with HTTP ${response.status}`);
      }
      setResult(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Analysis request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <div className="pb-5 border-b border-slate-200">
        <p className="text-xs font-medium text-slate-500">
          Configurable AI Provider · High-Thinking Mode Foundation
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
          AI Evidence Extraction & High-Thinking Engine
        </h1>
        <p className="mt-1 text-sm text-slate-600 max-w-3xl">
          Configured with <code className="font-mono">gemini-4-carbon</code> and <code className="font-mono">ThinkingLevel.HIGH</code> on the server, alongside a deterministic Mock AI provider for initial local development.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
        <form
          onSubmit={handleAnalyze}
          className="p-5 bg-white border border-slate-200 rounded-lg flex flex-col justify-between space-y-4"
        >
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <label
                htmlFor="thinking-query"
                className="text-sm font-semibold text-slate-900"
              >
                Qualification Reasoning Query
              </label>

              <div
                role="group"
                aria-label="Select AI Provider Mode"
                className="flex items-center gap-1 p-1 bg-slate-100 rounded-lg"
              >
                <button
                  type="button"
                  onClick={() => setProviderMode('mock')}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    providerMode === 'mock'
                      ? 'bg-white text-slate-900 shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  Mock Provider
                </button>
                <button
                  type="button"
                  onClick={() => setProviderMode('auto')}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    providerMode === 'auto'
                      ? 'bg-white text-slate-900 shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  Gemini Carbon (HIGH)
                </button>
              </div>
            </div>

            <textarea
              id="thinking-query"
              rows={5}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full p-3 text-xs bg-slate-50 border border-slate-300 rounded-lg text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-600 leading-relaxed"
              placeholder="Describe a business lead or qualification rule to evaluate..."
            />

            {error && (
              <p role="alert" className="text-xs font-medium text-red-600">
                {error}
              </p>
            )}
          </div>

          <div className="pt-3 border-t border-slate-100 flex items-center justify-between gap-4">
            <span className="text-xs text-slate-500 font-mono">
              Model: gemini-4-carbon · ThinkingLevel.HIGH
            </span>
            <button
              type="submit"
              disabled={isLoading}
              className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800 disabled:opacity-50 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <Send className="w-3.5 h-3.5" />
              <span>{isLoading ? 'Running Analysis...' : 'Test AI Provider'}</span>
            </button>
          </div>
        </form>

        <section className="p-5 bg-white border border-slate-200 rounded-lg flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-blue-600" />
              <h2 className="text-base font-semibold text-slate-900">
                Provider Output & Architectural Contract
              </h2>
            </div>

            {result ? (
              <div className="mt-4 space-y-4 text-xs">
                <div className="text-slate-500 font-mono">
                  Provider: {result.provider} · Model: {result.model} · Thinking: {result.thinking_level}
                </div>
                <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg text-slate-800 leading-relaxed whitespace-pre-wrap">
                  {result.analysis}
                </div>
                <div className="p-3 bg-blue-50/70 border border-blue-200 rounded-lg text-blue-950">
                  <span className="font-semibold">Deterministic Scoring Boundary: </span>
                  {result.deterministic_note}
                </div>
              </div>
            ) : (
              <div className="mt-6 py-10 text-center text-xs text-slate-500 space-y-2">
                <p className="font-medium text-slate-700">
                  No provider test executed in this session yet.
                </p>
                <p>
                  Click &ldquo;Test AI Provider&rdquo; to verify the configurable Mock / Gemini Carbon (ThinkingLevel.HIGH) server endpoint.
                </p>
              </div>
            )}
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-500">
            Target LeadAnalysis fields (Phase 2): business_summary · personalization_observation · potential_opportunity · icebreaker · confidence
          </div>
        </section>
      </div>
    </div>
  );
};
