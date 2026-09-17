const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const base=fs.readFileSync(path.join(root,'static/src/css/stage_replenishment_dashboard.css'),'utf8');
const extension=fs.readFileSync(path.join(root,'../furniture_need_to_produce/static/src/css/minmax_workspace.css'),'utf8');
const wrap=base.match(/\.o_fsr_table_wrap\s*\{([^}]+)\}/)[1];
assert(wrap.includes('max-height: none;'));
assert(wrap.includes('overflow-x: auto;'),'Keep mobile horizontal access to all columns');
assert(wrap.includes('overflow-y: hidden;'),'No nested vertical wheel target');
for(const css of [base,extension]){
    const blocks=[...css.matchAll(/[^{}]*\.o_fsr_table_wrap[^{}]*\{([^}]+)\}/g)];
    for(const block of blocks)assert(!/(?:max-)?height:\s*(?:\d|min\(|calc\()/i.test(block[1]),'No desktop/mobile height cap on table');
}
console.log('PASS: Min/Max table grows with page at all breakpoints, horizontal scrolling retained');
