const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const src = fs.readFileSync('furniture_mrp/static/src/js/mrp_stage_dashboard.js', 'utf8');
const fn = src.slice(src.indexOf('export function groupStageRows'), src.indexOf('function normalizeProductBatch'));
const context = {};
vm.runInNewContext(fn.replace('export ', '') + '\nthis.group = groupStageRows;', context);
const input = [
 [1,'بيج مون','شازلونج'], [1,'بيج مون','فوتيه'], [2,'مارلي','فوتيه'],
 [2,'مارلي','كنبة صغيرة'], [1,'بيج مون','كنبة كبيرة'], [2,'مارلي','كنبة كبيرة'],
].map(([model_id,model_name,product_name],i)=>({batch_token:`token-${i}`,display_qty:6,product:{model_id,model_name,product_name}}));
const before = JSON.stringify(input);
const output = context.group(input);
assert.equal(output.map(r=>r.product.product_name).join('|'), 'كنبة كبيرة|شازلونج|فوتيه|كنبة كبيرة|كنبة صغيرة|فوتيه');
assert.equal(output.filter(r=>r.modelStart).length,1);
assert.equal(output[3].modelStart,true);
assert.equal(JSON.stringify(input),before);
assert.deepEqual([...output.map(r=>r.batch_token)].sort(), input.map(r=>r.batch_token).sort());
assert.equal(context.group([{model_id:2,model_name:'نفس الاسم'},{model_id:1,model_name:'نفس الاسم'}])[1].modelStart,true);
assert.equal(context.group([]).length,0);
console.log('PASS model grouping, large sofa first, fauteuil last, boundaries, stable tokens, no mutation');
