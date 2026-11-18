export interface RuntimeInfo {
  python_version: string;
  fastapi_version: string;
  sqlalchemy_version: string;
}

export interface PublicConfigSummary {
  app_name: string;
  environment: string;
  version: string;
  ai_provider: 'mock' | 'gemini';
  ai_model: string;
  ai_thinking_level: string;
  database_engine: string;
  database_target: string;
  n8n_webhook_configured: boolean;
  callback_secret_configured: boolean;
}

export interface HealthResponse {
  status: string;
  service: string;
  app_name: string;
  version: string;
  environment: string;
  phase: string;
  timestamp: string;
  runtime: RuntimeInfo;
  config: PublicConfigSummary;
}

export interface ArchitectureStep {
  step: number;
  component: string;
  role: string;
  status: 'active' | 'planned_phase_2' | 'planned_phase_4';
}

export interface FoundationStatusResponse {
  status: string;
  current_phase: number;
  phase_title: string;
  architecture_flow: ArchitectureStep[];
  config_summary: PublicConfigSummary;
}

export interface HealthCheckLogEntry {
  id: string;
  endpoint: string;
  statusCode: number;
  statusText: string;
  latencyMs: number;
  checkedAt: string;
  service: string;
}

export type PhaseFilter = 'all' | 'active' | 'upcoming';

export interface RoadmapPhaseItem {
  phaseNumber: number;
  title: string;
  summary: string;
  deliverables: string[];
  state: 'active' | 'upcoming';
}
