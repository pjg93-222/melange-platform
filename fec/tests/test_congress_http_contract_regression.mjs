import assert from 'node:assert/strict';
import test from 'node:test';
import {buildAdvancedSearchPageUrl,parseAdvancedSearchHttpBody,parseCanonicalUrl} from '../lib/congress_advanced_http_surface.mjs';

const entry={id:'fixture',advanced_search_query:'("Fixture Act")',query_terms:[{query:'"Fixture Act"'}]};
const contract={congresses:[119],legislation_types:['hr'],required_window_start:'2025-01-01',required_window_end:'2026-12-31'};
const request=()=>buildAdvancedSearchPageUrl(entry,contract,1);
const row=()=>({result_url:'https://www.congress.gov/bill/119th-congress/house-bill/1',heading:'H.R. 1 - 119th Congress',display_number:'H.R. 1',initial_action_date:'01/02/2025',title:'Synthetic Fixture Act'});
const snapshot=()=>({title:'Advanced Search | Congress.gov',body_text:'Search Results 1-1 of 1',main_texts:['Search Results 1-1 of 1'],records:[row()],detail_sponsor_url:null});
async function parse({requestedUrl=request(),loadedUrl=requestedUrl,value=snapshot(),dualTransportRedirectMatch=false}={}){
  const page={evaluate:async()=>value};
  return parseAdvancedSearchHttpBody(page,{requestedUrl,loadedUrl,html:'synthetic snapshot; no browser',entry,contract,pageNumber:1,expectedTotal:1,dualTransportRedirectMatch});
}
test('valid exact filters and reordered parameters retain canonical result',async()=>{
  const loaded=new URL(request());const entries=[...loaded.searchParams].reverse();loaded.search='';for(const [key,value]of entries)loaded.searchParams.append(key,value);
  loaded.pathname='/advanced-search/legislation/';
  const result=await parse({loadedUrl:loaded.href});assert.equal(result.records[0].canonical_key,'119:hr:1');
  assert.ok(Object.values(result.surface_validation).every(Boolean));
});
for(const [name,url]of Object.entries({foreign:'https://foreign.example/bill/119th-congress/house-bill/1',http:'http://www.congress.gov/bill/119th-congress/house-bill/1',credentials:'https://fixture@www.congress.gov/bill/119th-congress/house-bill/1',wrongPort:'https://www.congress.gov:444/bill/119th-congress/house-bill/1',pathPrefix:'https://www.congress.gov/unrelated/bill/119th-congress/house-bill/1'}))test('refuses '+name+' result URL',async()=>{
  const value=snapshot();value.records[0].result_url=url;await assert.rejects(parse({value}));
});
for(const [name,change]of Object.entries({congress:value=>{value.records[0].result_url=value.records[0].result_url.replace('119th','118th');value.records[0].heading='H.R. 1 - 118th Congress';},type:value=>{value.records[0].result_url=value.records[0].result_url.replace('house-bill','senate-bill');}}))test('refuses result outside '+name+' contract',async()=>{const value=snapshot();change(value);await assert.rejects(parse({value}));});
for(const [name,change]of Object.entries({congress:url=>url.searchParams.set('congresses[0]','118'),type:url=>url.searchParams.set('legislationTypes[0]','s'),duplicate:url=>url.searchParams.append('congresses[0]','118'),duplicateSameValue:url=>url.searchParams.append('congresses[0]','119'),unknownFilter:url=>url.searchParams.append('newSelectionFilter','unexpected'),page:url=>url.searchParams.set('page','2'),sort:url=>url.searchParams.set('pageSort','relevance'),query:url=>url.searchParams.set('enterTerms','different'),missingFilter:url=>url.searchParams.delete('restrictionFields[0]')}))test('refuses loaded '+name+' change',async()=>{const url=new URL(request());change(url);await assert.rejects(parse({loadedUrl:url.href}));});
for(const [name,change]of Object.entries({foreign:url=>url.hostname='foreign.example',http:url=>url.protocol='http:',route:url=>url.pathname='/unrelated/advanced-search/legislation',credentials:url=>url.username='fixture',filter:url=>url.searchParams.set('congresses[0]','118')}))test('refuses invalid requested '+name+' before parsing',async()=>{
  const url=new URL(request());change(url);let evaluated=false;
  await assert.rejects(parseAdvancedSearchHttpBody({evaluate:async()=>{evaluated=true;return snapshot();}},{requestedUrl:url.href,loadedUrl:url.href,html:'',entry,contract,pageNumber:1}));assert.equal(evaluated,false);
});
test('valid singleton search proof and index.php canonicalization retained',async()=>{
  const loaded=new URL('https://www.congress.gov/index.php/bill/119th-congress/house-bill/1');loaded.searchParams.set('hl',entry.advanced_search_query);loaded.searchParams.set('s','9');loaded.searchParams.set('r','1');
  const body=['EDIT THIS SEARCH','PREVIOUS (disabled)','NEXT (disabled)','H.R.1 - Fixture Act','119th Congress (2025-2026)','Sponsor: Rep. Example [R-XY-1] (Introduced 01/02/2025)','Cosponsors (1)'].join('\n');
  const value={title:'H.R.1 - 119th Congress (2025-2026): Fixture Act | Congress.gov | Library of Congress',body_text:body,main_texts:[],records:[],detail_sponsor_url:'https://www.congress.gov/member/example/E000001'};
  const result=await parse({loadedUrl:loaded.href,value});assert.equal(result.result_transport,'single_result_redirect');assert.equal(result.records[0].canonical_key,'119:hr:1');
  const controlFree={...value,body_text:body.split('\n').slice(3).join('\n')};
  await assert.rejects(parse({loadedUrl:loaded.href,value:controlFree}));
  assert.equal((await parse({loadedUrl:loaded.href,value:controlFree,dualTransportRedirectMatch:true})).result_transport,'single_result_redirect');
  loaded.protocol='http:';await assert.rejects(parse({loadedUrl:loaded.href,value}));
  assert.equal(parseCanonicalUrl('https://www.congress.gov/index.php/bill/119th-congress/house-bill/1?s=9').canonical_key,'119:hr:1');
});
