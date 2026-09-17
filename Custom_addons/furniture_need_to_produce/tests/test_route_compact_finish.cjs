const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/../static/src/js/production_route_graph.js', 'utf8');
const ctx = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('export function remainingNeedProduceGraph'), source.indexOf('let graphSequence')).replaceAll('export function ', 'function '), ctx);
const clean = x => JSON.parse(JSON.stringify(x));
const graph = {
 nodes: ['priming','carpentry','bases','finishing','tailoring','painting','upholstery','packaging'].map((code,i)=>({
  id:code,kind:'stage',stage_code:code,lane:['bases','finishing'].includes(code)?'finish':code,
  stage_id:['bases','finishing'].includes(code)?10:i+20,editable:true,estimated_hours:2,
 })),
 edges: [['priming','carpentry'],['carpentry','bases'],['carpentry','finishing'],['bases','upholstery'],['finishing','upholstery'],['tailoring','upholstery'],['upholstery','packaging'],['painting','packaging']].map(([from,to])=>({from,to})),
 hours:10,unknown_wait:false,
};
const before=clean(graph),projected=ctx.remainingNeedProduceGraph(graph);
assert.deepEqual(clean(graph),before);
assert.deepEqual(clean(ctx.remainingNeedProduceGraph(projected)),clean(projected));
assert.equal(projected.hours,10);
assert(!projected.edges.some(e=>e.from==='bases'&&e.to==='upholstery'));
assert(!projected.edges.some(e=>e.to==='bases'));
assert(projected.edges.some(e=>e.from==='bases'&&e.to==='finishing'&&e.completion));
const layout=ctx.layoutNeedProduceGraph(projected), byId=new Map(layout.nodes.map(n=>[n.id,n]));
assert.equal(layout.invalid,false);assert.equal(layout.width,806);assert.equal(layout.height,132);
assert.equal(layout.hasCompletion,true);
for(const code of ['priming','carpentry','finishing','upholstery','packaging']) assert.equal(byId.get(code).row,0);
for(const [side,join] of [['bases','finishing'],['tailoring','upholstery'],['painting','packaging']]) {
 assert.equal(byId.get(side).row,1);assert.equal(byId.get(side).x,byId.get(join).x);
 assert.equal(byId.get(side).y-byId.get(join).y-byId.get(join).height,20);
}
for(const n of layout.nodes) {assert.equal(n.width,142);assert.equal(n.height,48);assert.equal(n.editable,true);}
assert.equal(layout.edges.find(e=>e.from==='bases').kind,'completion');
// Stock and withdrawn history still disappear without dangling connectors.
const hidden=clean(graph);hidden.nodes.find(n=>n.id==='bases').withdrawn=true;
const filtered=ctx.layoutNeedProduceGraph(ctx.remainingNeedProduceGraph(hidden));
assert(!filtered.nodes.some(n=>n.id==='bases'));assert(!filtered.edges.some(e=>e.completion));
// Legacy serial bases/preparation are NOT relabelled as a parallel gate.
const legacy=clean(graph);legacy.nodes.find(n=>n.id==='bases').lane='bases';
assert.deepEqual(clean(ctx.remainingNeedProduceGraph(legacy).edges),legacy.edges);
const otherStage=clean(graph);otherStage.nodes.find(n=>n.id==='bases').stage_id=999;
assert.deepEqual(clean(ctx.remainingNeedProduceGraph(otherStage).edges),otherStage.edges);
console.log('PASS: compact two rows, exact shared-order completion gate, no data mutation, legacy and green filtering preserved');
const template = fs.readFileSync(__dirname + '/../static/src/xml/production_route_graph.xml', 'utf8');
assert(!template.includes('o_ntp_graph_port'), 'No decorative connection dots');
assert(template.includes('t-foreach="diagram.edges"'), 'Keep every arrow');
assert(template.includes('t-att-marker-end'), 'Keep arrow heads');
