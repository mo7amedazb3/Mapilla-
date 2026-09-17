const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('furniture_need_to_produce/static/src/js/piece_partners.js','utf8')
    .replace(/^import .*;\n/gm,'').replaceAll('export class ','class ');
const services = {};
const context = {Component:class {}, Dialog:class {}, Many2XAutocomplete:class {},
    standardFieldProps:{}, registry:{category:()=>({add(){}})},
    useService:name=>services[name],useState:value=>value,onWillStart(){},user:{}};
vm.createContext(context);
vm.runInContext(source+'\nglobalThis.DialogClass=PiecePartnersDialog;globalThis.FieldClass=PiecePartners;',context);
(async()=>{
 let calls=[],loads=0,closes=0;
 services.orm={call:async(...args)=>calls.push(args)};
 const record={resId:42,context:{allowed_company_ids:[1]},data:{is_custom:true,state:'draft',
    custom_partners:{company_id:1,buyer_partner_id:[7,'Buyer'],beneficiary_partner_id:[8,'Consumer']}},
    load:async()=>{loads++;}};
 const dialog=new context.DialogClass();dialog.props={record,close:()=>closes++};dialog.setup();
 assert.equal(dialog.partnerDomain(true).at(-1)[0],'is_company');
 assert.equal(dialog.partnerDomain().length,2);
 dialog.selectPartner('beneficiary',[{id:9,display_name:'Changed consumer'}]);
 await dialog.save();
 assert.equal(calls.length,1);assert.equal(calls[0][1],'action_save_custom_partners');
 assert.equal(JSON.stringify(calls[0][2]),JSON.stringify([[42],7,9]));
 assert.equal(loads,1);assert.equal(closes,1);
 dialog.selectPartner('buyer',false);assert.equal(dialog.state.buyer,false);
 dialog.state.saving=true;await dialog.save();assert.equal(calls.length,1);
 dialog.state.saving=false;services.orm.call=async()=>{throw {data:{message:'Denied'}};};
 await dialog.save();assert.equal(dialog.state.error,'Denied');assert.equal(closes,1);assert.equal(dialog.state.saving,false);
 const field=new context.FieldClass();field.props={record};field.state={canEdit:true};field.bulk={};
 assert.equal(field.editable,true);
 field.bulk.confirming=true;assert.equal(field.editable,false);field.bulk.confirming=false;
 record.data.state='approved';assert.equal(field.editable,false);
 record.data.state='draft';record.data.is_custom=false;assert.equal(field.editable,false);
 const xml=fs.readFileSync('furniture_need_to_produce/static/src/xml/piece_partners.xml','utf8');
 assert.ok(xml.includes('t-if="props.record.data.is_custom"'));
 assert.ok(!xml.includes('Approve'));
 console.log('PASS: Custom-only chooser, company/buyer domains, exact piece save, reload, error/double-click guards, no extra approval');
})().catch(e=>{console.error(e);process.exitCode=1;});
