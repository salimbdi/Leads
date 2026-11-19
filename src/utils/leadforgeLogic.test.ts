import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCommercialQualification,
  buildLeadsQueryString,
  buildPipelineStageSteps,
  formatDimensionName,
  formatPipelineStageBadge,
  formatSearchRunStatusLabel,
  formatWebsiteDisplayHost,
  normalizeExternalWebsiteUrl,
  resolveApiUrl,
  validateSearchRunInput,
} from './leadforgeLogic';
import {
  LeadDetailResponse,
  LeadQueryFilters,
  SearchRunPipelineStatusResponse,
  SearchRunSummaryResponse,
} from '../types/leadforge';

test('validateSearchRunInput validates required fields and trims valid inputs', () => {
  const invalidEmpty = validateSearchRunInput('   ', '', '20');
  assert.equal(invalidEmpty.valid, false);
  assert.ok(invalidEmpty.errors.businessType);
  assert.ok(invalidEmpty.errors.city);

  const invalidMax = validateSearchRunInput('Dental Clinic', 'Austin', '0');
  assert.equal(invalidMax.valid, false);
  assert.ok(invalidMax.errors.maxResults);

  const valid = validateSearchRunInput('  Dental Clinic  ', '  Austin ', '25');
  assert.equal(valid.valid, true);
  assert.deepEqual(valid.sanitized, {
    business_type: 'Dental Clinic',
    city: 'Austin',
    max_results: 25,
  });
});

test('buildLeadsQueryString serializes score sorting, priority, min_score, and search query', () => {
  const filters: LeadQueryFilters = {
    limit: 20,
    offset: 0,
    priority: 'High',
    minScore: 70,
    q: '  Bright Dental ',
    city: 'Austin',
    businessType: 'Dental Clinic',
    searchRunId: 4,
    hasWebsite: 'no',
    sortBy: 'score',
    sortOrder: 'desc',
  };

  const qs = buildLeadsQueryString(filters);
  const params = new URLSearchParams(qs);
  assert.equal(params.get('limit'), '20');
  assert.equal(params.get('offset'), '0');
  assert.equal(params.get('priority'), 'High');
  assert.equal(params.get('min_score'), '70');
  assert.equal(params.get('q'), 'Bright Dental');
  assert.equal(params.get('city'), 'Austin');
  assert.equal(params.get('business_type'), 'Dental Clinic');
  assert.equal(params.get('search_run_id'), '4');
  assert.equal(params.get('has_website'), 'false');
  assert.equal(params.get('sort_by'), 'score');
  assert.equal(params.get('sort_order'), 'desc');
});

test('normalizeExternalWebsiteUrl and formatWebsiteDisplayHost handle missing and bare domains safely', () => {
  assert.equal(normalizeExternalWebsiteUrl(null), null);
  assert.equal(normalizeExternalWebsiteUrl('   '), null);
  assert.equal(normalizeExternalWebsiteUrl('austindental.com'), 'https://austindental.com');
  assert.equal(normalizeExternalWebsiteUrl('http://austindental.com/about'), 'http://austindental.com/about');
  assert.equal(formatWebsiteDisplayHost('https://www.austindental.com/contact'), 'austindental.com');
  assert.equal(formatWebsiteDisplayHost(null), 'No website');
});

test('formatDimensionName and formatSearchRunStatusLabel map keys to readable labels', () => {
  assert.equal(formatDimensionName('service_opportunity'), 'Service Opportunity');
  assert.equal(formatDimensionName('digital_presence_opportunity'), 'Digital Presence Opportunity');
  assert.equal(formatSearchRunStatusLabel('running'), 'Running');
  assert.equal(formatSearchRunStatusLabel('completed'), 'Completed');
});

test('resolveApiUrl correctly builds relative and absolute backend paths', () => {
  assert.equal(resolveApiUrl('/api/v1/search-runs'), '/api/v1/search-runs');
  assert.equal(resolveApiUrl('api/v1/search-runs'), '/api/v1/search-runs');
  assert.equal(resolveApiUrl('/api/v1/search-runs', ''), '/api/v1/search-runs');
  assert.equal(
    resolveApiUrl('/api/v1/search-runs', 'https://leads-einq.onrender.com'),
    'https://leads-einq.onrender.com/api/v1/search-runs'
  );
  assert.equal(
    resolveApiUrl('/api/v1/search-runs', 'https://leads-einq.onrender.com/'),
    'https://leads-einq.onrender.com/api/v1/search-runs'
  );
  assert.equal(
    resolveApiUrl('api/health', 'https://leads-einq.onrender.com'),
    'https://leads-einq.onrender.com/api/health'
  );
});

test('buildPipelineStageSteps renders exact 6 pipeline stages and never fakes completion on failure', () => {
  const baseRun: SearchRunSummaryResponse = {
    id: 1,
    city: 'Austin',
    business_type: 'Dental Clinic',
    max_results: 2,
    status: 'running',
    created_at: '2026-04-19T14:21:14Z',
    completed_at: null,
    leads_count: 1,
  };

  const analyzingStatus: SearchRunPipelineStatusResponse = {
    id: 1,
    status: 'running',
    pipeline_stage: 'analyzing_businesses',
    attempt_count: 1,
    leads_count: 1,
    max_results: 2,
    created_at: '2026-04-19T14:21:14Z',
    started_at: '2026-04-19T14:21:15Z',
    completed_at: null,
    last_error: null,
    worker_id: 'worker-1',
  };

  const steps = buildPipelineStageSteps(baseRun, analyzingStatus);
  assert.deepEqual(
    steps.map((s) => s.label),
    [
      'Search submitted',
      'Waiting in queue',
      'Discovering businesses (Apify)',
      'Analyzing businesses (Gemini)',
      'Saving results',
      'Completed or failed',
    ]
  );
  assert.equal(steps[0].state, 'done');
  assert.equal(steps[1].state, 'done');
  assert.equal(steps[2].state, 'done');
  assert.equal(steps[3].state, 'active');
  assert.equal(steps[4].state, 'pending');
  assert.equal(steps[5].state, 'pending');

  // Verify failed state during Apify discovery does NOT mark Gemini or Saving as done
  const failedApifyStatus: SearchRunPipelineStatusResponse = {
    ...analyzingStatus,
    status: 'failed',
    pipeline_stage: 'discovering_businesses',
    leads_count: 0,
    last_error: 'Apify actor timeout',
  };
  const failedSteps = buildPipelineStageSteps(
    { ...baseRun, status: 'failed', leads_count: 0 },
    failedApifyStatus
  );
  assert.equal(failedSteps[0].state, 'done');
  assert.equal(failedSteps[1].state, 'done');
  assert.equal(failedSteps[2].state, 'failed');
  assert.equal(failedSteps[3].state, 'pending');
  assert.equal(failedSteps[4].state, 'pending');
  assert.equal(failedSteps[5].state, 'failed');
  assert.ok(failedSteps[5].detail.includes('Apify actor timeout'));
  assert.equal(
    formatPipelineStageBadge('failed', 'discovering_businesses'),
    'Failed · Discovering businesses (Apify)'
  );
});

test('buildCommercialQualification maps factual evidence to the 5 offered services without inventing budgets', () => {
  const realLead: LeadDetailResponse = {
    id: 1,
    company_name: 'Avery Ranch Dental',
    website: 'https://www.averyranchdental.com/',
    address: '14900 Avery Ranch Blvd Suite B100, Austin, TX 78717',
    phone: '(512) 341-7041',
    city: 'Austin',
    business_type: 'Dental Clinic',
    rating: 4.8,
    reviews_count: 1256,
    score: 89,
    priority: 'High',
    score_breakdown: {
      total_score: 89,
      max_score: 100,
      priority: 'High',
      formula_version: 'v1.0-deterministic',
      dimensions: {},
    },
    created_at: '2026-04-19T14:21:47Z',
    updated_at: '2026-04-19T14:21:47Z',
    analysis: {
      id: 1,
      lead_id: 1,
      business_summary:
        'Avery Ranch Dental is a comprehensive family and cosmetic dental practice in Austin, TX.',
      personalization_observation:
        'Avery Ranch Dental maintains a 4.8-star rating across 1,256 reviews and manages an in-house Dental Wellness Plan.',
      potential_opportunity:
        'Implement an AI-driven patient triage and FAQ assistant to automate inquiry handling across their diverse service lines.',
      icebreaker: 'Saw your 1,256 reviews in Austin!',
      confidence: 0.9,
      created_at: '2026-04-19T14:21:47Z',
      updated_at: '2026-04-19T14:21:47Z',
    },
    raw_payload: JSON.stringify({
      stage1_ai: {
        services: [
          'Cosmetic Dentistry',
          'Dental Implants',
          'Clear Aligners',
          'Emergency Dental Care',
          'Sleep Apnea Treatment',
          'General & Family Dentistry',
        ],
        website_observations: [
          'Features an online appointment scheduling link and downloadable patient forms',
          'Promotes an in-house Dental Wellness Plan',
        ],
        potential_digital_needs: [
          'AI-powered patient intake and FAQ chatbot',
          'Automated referral and review management workflows',
        ],
      },
      stage2_ai: {
        opportunity_reasons: [
          'High patient volume indicated by 1,256+ reviews creates significant administrative overhead',
        ],
      },
    }),
    search_runs: [],
  };

  const qualification = buildCommercialQualification(realLead);
  assert.ok(qualification.whyGoodProspect.includes('Avery Ranch Dental'));
  assert.ok(qualification.whyGoodProspect.includes('1,256'));
  assert.equal(qualification.extractedServicesOffered.length, 6);
  const serviceNames = qualification.recommendedServices.map((s) => s.service);
  assert.ok(serviceNames.includes('AI integrations and automation'));
  assert.ok(serviceNames.includes('n8n automation systems'));
  assert.ok(serviceNames.includes('Custom web applications'));
  assert.ok(serviceNames.includes('Business management systems'));
  assert.ok(qualification.supportingEvidence.length > 0);
});
