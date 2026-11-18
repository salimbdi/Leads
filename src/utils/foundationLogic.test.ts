import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ROADMAP_PHASES,
  filterRoadmapPhases,
  formatArchitectureStatus,
  calculateAverageLatency,
} from './foundationLogic';
import { HealthCheckLogEntry } from '../types/foundation';

test('filterRoadmapPhases returns all 7 phases when filter is all', () => {
  const result = filterRoadmapPhases(ROADMAP_PHASES, 'all');
  assert.equal(result.length, 7);
});

test('filterRoadmapPhases returns Phases 1, 2, and 3 when filter is active', () => {
  const result = filterRoadmapPhases(ROADMAP_PHASES, 'active');
  assert.equal(result.length, 3);
  assert.deepEqual(
    result.map((p) => p.phaseNumber),
    [1, 2, 3]
  );
});

test('filterRoadmapPhases filters by search query across title and deliverables', () => {
  const sqlitePhases = filterRoadmapPhases(ROADMAP_PHASES, 'all', 'SQLAlchemy');
  assert.equal(sqlitePhases.length, 1);
  assert.equal(sqlitePhases[0].phaseNumber, 2);

  const deterministicPhases = filterRoadmapPhases(ROADMAP_PHASES, 'upcoming', 'deterministic');
  assert.equal(deterministicPhases.length, 1);
  assert.equal(deterministicPhases[0].phaseNumber, 6);
});

test('formatArchitectureStatus maps internal status codes to readable labels', () => {
  assert.equal(formatArchitectureStatus('active'), 'Operational in Phase 1');
  assert.equal(formatArchitectureStatus('planned_phase_2'), 'Scheduled for Phase 2');
  assert.equal(formatArchitectureStatus('planned_phase_4'), 'Scheduled for Phase 4');
});

test('calculateAverageLatency computes rounded mean latency and handles empty list', () => {
  assert.equal(calculateAverageLatency([]), 0);
  const sampleLogs: HealthCheckLogEntry[] = [
    {
      id: '1',
      endpoint: '/api/health',
      statusCode: 200,
      statusText: 'OK',
      latencyMs: 12,
      checkedAt: '2026-11-13T14:00:00Z',
      service: 'leadforge-backend',
    },
    {
      id: '2',
      endpoint: '/health',
      statusCode: 200,
      statusText: 'OK',
      latencyMs: 19,
      checkedAt: '2026-11-13T14:01:00Z',
      service: 'leadforge-backend',
    },
  ];
  assert.equal(calculateAverageLatency(sampleLogs), 16);
});
