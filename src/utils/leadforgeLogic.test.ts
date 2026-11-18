import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildLeadsQueryString,
  formatDimensionName,
  formatSearchRunStatusLabel,
  formatWebsiteDisplayHost,
  normalizeExternalWebsiteUrl,
  validateSearchRunInput,
} from './leadforgeLogic';
import { LeadQueryFilters } from '../types/leadforge';

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
