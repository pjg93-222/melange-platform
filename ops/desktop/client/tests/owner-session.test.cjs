'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
// The Terrier owner's one sign-in for every district is bound to one per-process key (Geo server/research/desktop_owner.py).
test('only the existing Terrier owner gets the desktop owner directory and this app session key',()=>{
 const source=fs.readFileSync(path.join(__dirname,'..','runtime.cjs'),'utf8');
 assert.match(source,/^const OWNER_SESSION_KEY=crypto\.randomBytes\(32\)\.toString\('base64url'\);$/m);
 assert.equal(source.match(/\bOWNER_SESSION_KEY\b/g).length,2,'generated once at load and only passed to a backend');
 assert.ok(source.includes("const ownerRoot=roots.legacyOwner?path.join(this.userData,'state','terrier-owner'):null;"));
 assert.ok(source.includes("if(ownerRoot){refuseInstalledOverlap(ownerRoot,[this.resources,path.dirname(binary),...this.protectedPaths]);environment.DESKTOP_OWNER_SESSION_KEY=OWNER_SESSION_KEY;}"));
 assert.ok(source.includes("if(roots.legacyOwner) args.push('--legacy-owner',roots.legacyOwner,'--owner-root',ownerRoot);"));
});
