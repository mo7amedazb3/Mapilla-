const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/src/js/production_route_graph.js'), 'utf8');
const code = source.slice(source.indexOf('export function remainingNeedProduceGraph'), source.indexOf('let graphSequence')).replaceAll('export function ', 'function ');
const ctx = vm.createContext({});
vm.runInContext(code, ctx);
const clean = value => JSON.parse(JSON.stringify(value));
const remaining = graph => ctx.remainingNeedProduceGraph(graph);
const layout = graph => ctx.layoutNeedProduceGraph(remaining(graph));
const graph = {
 nodes:[{id:'frame',kind:'history',withdrawn:true},{id:'finish',kind:'history',withdrawn:true},
        {id:'sewing',kind:'history',withdrawn:true},{id:'paint',kind:'stock'},
        {id:'upholstery',kind:'incoming',production_id:123},
        {id:'pack',kind:'stage',stage_id:456,editable:true}],
 edges:[{from:'frame',to:'finish'},{from:'finish',to:'upholstery'},
        {from:'sewing',to:'upholstery'},{from:'paint',to:'pack'},
        {from:'upholstery',to:'pack'}],
 hours:2,days:0.2,unknown_wait:true,history_incomplete:false,
};
const before = clean(graph), projected = remaining(graph), result = layout(graph);
assert.deepEqual(clean(projected.nodes).map(n=>n.id),['upholstery','pack']);
assert.deepEqual(clean(projected.edges),[{from:'upholstery',to:'pack'}]);
assert.equal(result.width,320);assert.equal(result.height,64);
assert.ok(result.nodes.every(node=>node.width===142));
assert.deepEqual(clean(result.nodes.map(node=>node.height)),[48,48]);
assert.equal(result.invalid,false);assert.equal(result.hasParallel,false);
assert.equal(result.edges[0].kind,'incoming');
assert.equal(result.nodes[0].hasInputs,false);assert.equal(result.nodes[1].hasOutputs,false);
assert.equal(projected.hours,2);assert.equal(projected.days,0.2);assert.equal(projected.unknown_wait,true);
assert.equal(projected.nodes[1].stage_id,456);assert.equal(projected.nodes[1].editable,true);
assert.deepEqual(graph,before);

// Yellow reserved/unconsumed history stays, including partial transfers.
const reserved = clean(graph);reserved.nodes[1].withdrawn=false;
assert.deepEqual(clean(remaining(reserved).nodes).map(n=>n.id),['finish','upholstery','pack']);
assert.equal(layout(reserved).invalid,false);
assert.ok(layout(reserved).edges.every(e=>e.kind==='incoming'));

// Any reused planned/incoming node already marked withdrawn is green too.
for(const kind of ['stage','incoming','history']) {
 assert.equal(remaining({nodes:[{id:'a',kind,withdrawn:true}],edges:[]}).nodes.length,0);
}
const greenOnly={nodes:[{id:'a',kind:'stock'},{id:'b',kind:'history',withdrawn:true}],edges:[{from:'a',to:'b'}]};
assert.equal(layout(greenOnly).nodes.length,0);assert.equal(layout(greenOnly).edges.length,0);
assert.equal(layout(greenOnly).invalid,false);
assert.equal(layout(null).nodes.length,0);

// Real broken links / cycles still get reported rather than silently hidden.
assert.equal(layout({...graph,edges:[...graph.edges,{from:'missing',to:'pack'}]}).invalid,true);
assert.equal(layout({nodes:[{id:'a',kind:'stage'},{id:'b',kind:'incoming'}],edges:[{from:'a',to:'b'},{from:'b',to:'a'}]}).invalid,true);
const parallel={nodes:[{id:'a',kind:'stage'},{id:'b',kind:'incoming',lane:'tailoring'},{id:'c',kind:'stage'}],edges:[{from:'a',to:'c'},{from:'b',to:'c'}]};
assert.equal(layout(parallel).hasParallel,true);assert.equal(layout(parallel).edges.length,2);
// Compact card geometry must keep cards disjoint and connectors centred.
// Include a shared input that skips a column (routed below the cards).
const shared={nodes:[{id:'a',kind:'stage'},{id:'b',kind:'stage'},
 {id:'c',kind:'stage'},{id:'d',kind:'incoming'}],
 edges:[{from:'a',to:'b'},{from:'b',to:'c'},{from:'c',to:'d'},{from:'a',to:'d'}]};
for(const example of [graph,reserved,parallel,shared]) {
 const compact=layout(example),byId=new Map(compact.nodes.map(n=>[n.id,n]));
 for(const a of compact.nodes) {
  assert.equal(a.width,142);assert.equal(a.height,48);
  assert.ok(a.x>=0&&a.y>=0&&a.x+a.width<=compact.width&&a.y+a.height<=compact.height);
  for(const b of compact.nodes) if(a!==b) {
   assert.ok(!(a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y));
  }
 }
 for(const edge of compact.edges) {
  const from=byId.get(edge.from),to=byId.get(edge.to);
  if(from.rank===to.rank&&from.row>to.row) {
   assert.equal(edge.path,`M ${from.x+from.width/2} ${from.y-1} V ${to.y+to.height+2}`);
   continue;
  }
  assert.ok(edge.path.startsWith(`M ${from.x-1} ${from.y+from.height/2} `));
  assert.ok(edge.path.endsWith(to.rank-from.rank>1
   ? `V ${to.y+to.height/2} H ${to.x+to.width+5}`
   : `${to.x+to.width+5} ${to.y+to.height/2}`));
 }
}
assert.deepEqual(clean(remaining(remaining(graph))),clean(projected));
const template = fs.readFileSync(path.join(__dirname, '../static/src/xml/production_route_graph.xml'), 'utf8');
assert.ok(!template.includes('node.material_count'));
assert.ok(!template.includes('<t t-else="">تصنيع مطلوب</t>'));
assert.ok(template.includes('t-if="node.kind !== \'stage\' or node.withdrawn"'));
assert.ok(template.includes('format(node.estimated_hours)'));
assert.ok(template.includes('this.openStage(node, event)'));
assert.ok(template.includes('o_ntp_graph_customized_stage'));
assert.ok(template.includes('t-att-title="(canEdit(node)'));
assert.ok(!template.includes('o_ntp_graph_bom_arrow'));
assert.ok(!template.includes('<span lang="en" dir="ltr">BoM</span>'));
assert.ok(!template.includes('fa-pencil'));
assert.ok(!template.includes('fa-eye'));
console.log('PASS: hide green only, keep yellow/purple, compact layout, no dangling arrows, read-only projection and edge cases');
