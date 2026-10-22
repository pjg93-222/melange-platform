'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {proposal}=require('../dataset-protocol.cjs');
const grant={appId:'terrier',districtIds:['co-05']};
const body=(kind,objects)=>({schemaVersion:1,datasetId:crypto.randomUUID(),revisionId:crypto.randomUUID(),parentRevisionIds:[],districtId:'co-05',kind,adapterVersion:1,objects,counts:{files:objects.length}});
const check=(kind,item)=>proposal(body(kind,[{role:'street.sqlite3',sha256:'a'.repeat(64),size:10,...item}]),grant,{allowUnsupported:true});
test('district-data objects require a non-negative integer rawSize; other kinds keep {role,sha256,size}',()=>{
 assert.equal(check('terrier.district-data.v1',{rawSize:0}).supported,false);
 assert.equal(check('terrier.district-data.v1',{rawSize:4096}).supported,false);
 for(const rawSize of [undefined,-1,1.5,'4096',null,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>check('terrier.district-data.v1',rawSize===undefined?{}:{rawSize}),/Unsupported dataset metadata|Invalid dataset object/);
 assert.equal(check('terrier.record-changes.v1',{}).supported,false);
 assert.throws(()=>check('terrier.record-changes.v1',{rawSize:4096}),/Unsupported dataset metadata/);
});
