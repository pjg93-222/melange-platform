'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {verifySignatureResult}=require('../native-signature.cjs');
test('native signature requires valid status, exact publisher subject and exact installer path',()=>{
  const filename='C:\\Test\\Package.exe',publisher='CN=Publisher, O=Example, C=US';
  const valid={status:0,subject:publisher,path:filename};
  verifySignatureResult(valid,filename,publisher);
  for(const result of [{},{...valid,status:1},{...valid,status:'0'},{...valid,subject:'CN=Publisher'},{...valid,subject:publisher+', OU=Unexpected'},{...valid,path:'C:\\Other\\Package.exe'}])assert.throws(()=>verifySignatureResult(result,filename,publisher),/could not be verified/);
});
