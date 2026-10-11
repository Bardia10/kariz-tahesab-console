import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('API URLs stay within the console deployment path', async () => {
  const source = readFileSync(new URL('../frontend/src/deployment.js', import.meta.url), 'utf8');
  for (const base of ['/', '/tahesab/']) {
    const module = await import(`data:text/javascript,${encodeURIComponent(source.replace('import.meta.env.BASE_URL', JSON.stringify(base)))}`);
    assert.equal(module.consoleUrl('/api/session'), `${base}api/session`);
    assert.equal(module.consoleUrl('/api/customers?from_code=1'), `${base}api/customers?from_code=1`);
  }
});
