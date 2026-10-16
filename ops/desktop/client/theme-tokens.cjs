'use strict';
// Build-time only: a deterministic scoped subset for the existing Terrier form.
const fs=require('node:fs'),path=require('node:path');
function scopedTokens(){
 const css=fs.readFileSync(path.join(__dirname,'desktop-chrome.css'),'utf8');
 const tokens=[...css.matchAll(/(--desktop-[a-z-]+):([^;}]+)/g)].filter(([_,name])=>name!=='--desktop-heading-font').map(([,name,value])=>[name,value]);
 const unique=new Map(tokens);if(unique.size!==tokens.length||unique.size<10)throw new Error('Shared chrome tokens are invalid');
 return '/* Generated from desktop-chrome.css; run theme-tokens.cjs to refresh. */\n.terrier-auth{'+tokens.map(([name,value])=>name+':'+value).join(';')+'}\n';
}
if(require.main===module){const target=process.argv[2];if(!target||!target.endsWith('auth-tokens.generated.css'))throw new Error('Explicit generated auth CSS target required');fs.writeFileSync(path.resolve(target),scopedTokens());}
module.exports={scopedTokens};
