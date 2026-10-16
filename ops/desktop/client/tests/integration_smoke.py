"""Isolated interoperability smoke; never accesses production publisher state."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "ops/desktop/server"))
from desktop_admin import Store, BoundedServer, handler_for

TRUST = str(REPO / "ops/desktop/client/trust.cjs")
NODE = "node"

def node(script, data):
    result = subprocess.run([NODE, "-e", script], input=json.dumps(data), text=True,
                            capture_output=True, timeout=60)
    if result.returncode:
        # Errors are our assertion labels; never print request data or credentials.
        raise RuntimeError("Node smoke failed: " + result.stderr[-1200:])
    return json.loads(result.stdout)

def main():
    with tempfile.TemporaryDirectory(prefix="desktop-integration-", dir=Path(__file__).parent) as temporary:
        root = Path(temporary)
        store = Store(root / "publisher")
        store.init()
        server = BoundedServer(("127.0.0.1", 0), handler_for(store), store.tls_context())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            bundle = store.pairing("melange", f"https://127.0.0.1:{server.server_port}")
            first = node(r'''
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const x=JSON.parse(fs.readFileSync(0,'utf8')),t=require(x.trust);
(async()=>{
 const p=t.pairing(x.bundle,'melange');
 const body={enrollmentCode:p.code,appId:'melange',deviceId:crypto.randomUUID(),platform:'win32',arch:'x64',currentVersion:'1.0.0'};
 await assert.rejects(()=>t.jsonRequest({...p,tlsSpkiSha256:'0'.repeat(64)},'/v1/enroll',{method:'POST',body}),/TLS identity changed/);
 const a=await t.jsonRequest(p,'/v1/enroll',{method:'POST',body});
 const e={...p,...a},opts={token:a.deviceToken};
 await assert.rejects(()=>t.jsonRequest(p,'/v1/enroll',{method:'POST',body}),/403/);
 assert.equal(await t.jsonRequest(e,'/v1/apps/melange/updates?platform=win32&arch=x64',opts),null);
 assert.deepEqual(await t.jsonRequest(e,'/v1/apps/melange/trust',opts),{transitions:[]});
 await assert.rejects(()=>t.jsonRequest(e,'/v1/apps/terrier/updates?platform=win32&arch=x64',opts),/403/);
 assert.deepEqual(await t.jsonRequest(e,'/v1/status',{method:'POST',token:a.deviceToken,body:{appId:'melange',version:'1.0.0',status:'downloaded'}}),{ok:true});
 process.stdout.write(JSON.stringify({enrollment:e,checks:['bad SPKI refused before enrollment','real HTTPS enrollment','one-time code replay refused','empty update 204','trust endpoint','cross-app token refused','downloaded status accepted']}));
})().catch(e=>{process.stderr.write(e.stack);process.exitCode=1;});
''', {"trust": TRUST, "bundle": bundle})
            # All gates are bypassed ONLY on this in-memory test object; the fixture is
            # inert text and is never installed. Production CLI has no bypass switch.
            package = root / "fixture.exe"
            package.write_bytes(b"inert cross-language integrity fixture\n" * 100)
            store.verify_release_gates = lambda *_: None
            store.rotate_key("melange")
            store.publish("melange", package, {}, "1.0.1", 1, "win32", "x64", [first["enrollment"]["deviceId"]])
            second = node(r'''
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const x=JSON.parse(fs.readFileSync(0,'utf8')),t=require(x.trust);
(async()=>{
 let e=x.enrollment;
 const transitions=await t.jsonRequest(e,'/v1/apps/melange/trust',{token:e.deviceToken});
 e=t.applyKeyTransitions(e,transitions.transitions);assert.equal(e.keySequence,1);
 const envelope=await t.jsonRequest(e,'/v1/apps/melange/updates?platform=win32&arch=x64',{token:e.deviceToken});
 const id={appId:'melange',platform:'win32',arch:'x64'};
 const v=t.verifyEnvelope(envelope,e,id);
 assert.equal(v.manifest.version,'1.0.1');
 assert.throws(()=>t.verifyEnvelope({...envelope,payload:Buffer.from('{}').toString('base64')},e,id),/signature/);
 assert.throws(()=>t.verifyEnvelope(envelope,e,{...id,appId:'terrier'}),/different app/);
 const file=await t.downloadVerified(e,e.deviceToken,v.manifest,x.download);
 assert.equal(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),v.manifest.package.sha256);
 process.stdout.write(JSON.stringify({checks:['Python rotation verified by Node','Python Ed25519 envelope verified by Node','tampered signature refused','wrong app envelope refused','HTTPS fixture download hashes verified']}));
})().catch(e=>{process.stderr.write(e.stack);process.exitCode=1;});
''', {"trust": TRUST, "enrollment": first["enrollment"], "download": str(root / "download")})
            print(json.dumps({"success": True, "checks": first["checks"] + second["checks"], "productionChanges": False}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

if __name__ == "__main__":
    main()

