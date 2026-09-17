// Pure icon-adapter contract, plus browser coverage in deployment verify.cjs.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/brand_navigation.js'), 'utf8');
const adapter = source.slice(source.indexOf('const APP_BRANDS ='), source.indexOf('export class MapillaAppIcon'))
    .replace('export function appBrand', 'function appBrand');
const { appBrand, brands } = vm.runInNewContext(adapter + '; ({ appBrand, brands: APP_BRANDS })');
assert.equal(brands.length, 23);
for (const [prefix, shape, caption] of brands) {
    const app = Object.freeze({xmlid: prefix + (prefix.endsWith('.') ? 'sample_root' : ''), label: 'Original name'});
    const result = appBrand(app);
    assert.equal(result.path, shape);
    assert.equal(result.caption, caption);
    assert.equal(app.label, 'Original name');
}
assert.notEqual(appBrand({xmlid:'hr.menu_hr_root'}).path, appBrand({xmlid:'hr_attendance.menu_hr_attendance_root'}).path);
assert.notEqual(appBrand({xmlid:'mrp.menu_mrp_root'}).path, appBrand({xmlid:'furniture_mrp.menu_furniture_mrp_root'}).path);
assert.ok(appBrand({xmlid:'furniture_stage_replenishment.menu_furniture_stage_replenishment_root'}).path);
assert.equal(appBrand({xmlid:'unknown.root',label:'Custom app'}).path, null);
assert.equal(appBrand({label:'Custom app'}).caption, 'Custom app');
console.log('PASS: 23 known applications, distinct namespaces, immutable labels, unknown-app fallback');
