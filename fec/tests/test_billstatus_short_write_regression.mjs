import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {buildBillstatusRelationshipReuse} from '../lib/billstatus_relationship_reuse.mjs';

const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const receipt = async file => {const raw=await fs.readFile(file);return {path:file,bytes:raw.length,sha256:sha(raw)};};

async function fixture() {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'fec-reuse-write-'));
  const sidecar=path.join(root,'sidecar');await fs.mkdir(sidecar);
  const artifacts={};
  for (const [name,rows] of Object.entries({edges:[{canonical_key:'119:hr:1',member:'Synthetic \u00e9\ud83d\ude80'}],verification_ledger:[],measure_metadata:[{canonical_key:'119:hr:1'}],source_gap_ledger:[]})) {
    const file=path.join(sidecar,name+'.ndjson');await fs.writeFile(file,rows.map(row=>JSON.stringify(row)+'\n').join(''));artifacts[name]=await receipt(file);
  }
  const manifest={schema:'congress-sponsorship-enrichment-manifest-v1',status:'complete',official_source_only:true,top_level_relationship_evidence_complete:true,selected_record_count:1,top_level_metadata_count:1,unresolved_measure_count:0,edge_count:1,edge_counts:{sponsors:1},outputs:artifacts};
  for(const key of ['candidate_creation_performed','semantic_exclusion_performed','ratified_decision_performed','automatic_scoring_performed','sync_or_promotion_performed','production_write_performed','membership_disposition_authority','ratification_authority','scoring_authority','acceptance_passed'])manifest[key]=false;
  for(const [name,row]of Object.entries(artifacts)){manifest[name+'_file']=path.basename(row.path);manifest[name+'_sha256']=row.sha256;}
  const manifestPath=path.join(sidecar,'manifest.json');await fs.writeFile(manifestPath,JSON.stringify(manifest));
  const pointerPath=path.join(root,'pointer.json');await fs.writeFile(pointerPath,JSON.stringify({schema:'govinfo-billstatus-review-sidecar-pointer-v1',manifest:await receipt(manifestPath),sync_or_promotion_performed:false}));
  const records=[{canonical_key:'119:hr:1',record_kind:'bill'}];
  const outputDir=path.join(root,'output');await fs.mkdir(outputDir);
  return {root,outputDir,args:{pointerPath,discoveryRecords:records,discoveryManifestPath:path.join(root,'discovery.json'),discoveryRecordsSha256:sha(JSON.stringify(records)),outputDir}};
}

async function withWrites(mode,callback) {
  const originalOpen=fs.open;let calls=0;
  try {
    fs.open=async function(file,...args) {
      const handle=await originalOpen.call(this,file,...args);
      if(args[0]==='wx' && String(file).includes('.tmp-')) {
        const write=handle.write.bind(handle);
        handle.write=async function(buffer,offset,length,position) {
          calls++;
          if(mode==='zero')return {bytesWritten:0};
          if(mode==='invalid')return {bytesWritten:Infinity};
          if(mode==='throw')throw Object.assign(new Error('synthetic disk error'),{code:'EIO'});
          const bytes=Buffer.isBuffer(buffer)?buffer:Buffer.from(buffer,'utf8');
          const start=Buffer.isBuffer(buffer)?offset:0;
          const remaining=Buffer.isBuffer(buffer)?length:bytes.length;
          return write(bytes,start,Math.min(3,remaining),null);
        };
      }
      return handle;
    };
    return await callback(()=>calls);
  }finally{fs.open=originalOpen;}
}

test('partial multibyte writes complete with exact persisted receipt',async()=>{
  const value=await fixture();
  const manifest=await withWrites('partial',async calls=>{const result=await buildBillstatusRelationshipReuse(value.args);assert.ok(calls()>2);return result;});
  const actual=await receipt(path.join(value.outputDir,manifest.edges_file));
  assert.equal(actual.sha256,manifest.edges_file_sha256);
  assert.equal(actual.bytes,manifest.billstatus_relationship_reuse.hybrid_resolution.outputs.edges.bytes);
  const rows=(await fs.readFile(actual.path,'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(rows.length,manifest.edge_count);assert.equal(rows[0].member,'Synthetic \u00e9\ud83d\ude80');
});

for(const mode of ['zero','invalid','throw'])test(mode+' write refuses before replacing prior artifact',async()=>{
  const value=await fixture();const output=path.join(value.outputDir,'member_measure_edges.ndjson');
  const original='prior accepted fixture\n';await fs.writeFile(output,original);
  await withWrites(mode,async()=>assert.rejects(buildBillstatusRelationshipReuse(value.args)));
  assert.equal(await fs.readFile(output,'utf8'),original);
  assert.deepEqual((await fs.readdir(value.outputDir)).sort(),['member_measure_edges.ndjson']);
});

test('ordinary writes and empty ledgers retain exact hashes',async()=>{
  const value=await fixture();const manifest=await buildBillstatusRelationshipReuse(value.args);
  for(const row of Object.values(manifest.billstatus_relationship_reuse.hybrid_resolution.outputs)){
    const actual=await receipt(row.path);assert.equal(actual.bytes,row.bytes);assert.equal(actual.sha256,row.sha256);
  }
  assert.equal(manifest.edge_count,1);assert.equal(manifest.unresolved_measure_count,0);
});
