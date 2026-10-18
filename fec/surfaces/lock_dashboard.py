# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""Lock the Moneyball dashboard behind ONE password.
AES-256-GCM encrypts the entire self-contained HTML (key = PBKDF2-SHA256 of the password, 250k iters) and
wraps it in a self-decrypting page using the browser's built-in Web Crypto (no external libs). Safe to host
anywhere (even a public URL); only the password decrypts it, in-browser.
Usage:  python fec/surfaces/lock_dashboard.py --password-file <path>
"""
import argparse, sys, os, base64, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
B = MB_ROOT
SRC = B + r'\Moneyball.html'
OUT = B + r'\deploy\index.html'
ITERS = 250000

WRAP = r'''<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><meta name=referrer content=no-referrer><title>Melange — locked</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">
<style>
:root{color-scheme:dark}
html,body{margin:0;height:100%;color:#F3F3E6;font:13px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif}
body{background:radial-gradient(680px 480px at 50% 34%,#312716 0%,#241D10 48%,#1D1912 76%) #1D1912;overflow:hidden}
body::after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.16;background-image:repeating-linear-gradient(0deg,rgba(243,243,230,.045) 0 1px,transparent 1px 3px),repeating-linear-gradient(90deg,rgba(243,243,230,.035) 0 1px,transparent 1px 4px)}
.wrap{min-height:100vh;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px;position:relative;z-index:1}
.box{width:100%;max-width:332px;text-align:center}
.glyph{display:block;margin:0 auto 16px;filter:drop-shadow(0 0 22px rgba(238,205,92,.20))}
.glyph path{stroke-dasharray:520;stroke-dashoffset:520;animation:draw 1100ms cubic-bezier(.22,.61,.36,1) 120ms forwards}
@keyframes draw{to{stroke-dashoffset:0}}
h2{font-family:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif;font-size:20px;font-weight:700;text-transform:uppercase;letter-spacing:.24em;margin:0 0 6px;text-indent:.24em}
p{color:#A49D8A;font-size:12.5px;margin:4px 0 22px}
.note{color:#9D9784;font-size:11px}
.rise{opacity:0;transform:translateY(8px);animation:rise 620ms cubic-bezier(.22,.61,.36,1) forwards}
.d1{animation-delay:320ms}.d2{animation-delay:440ms}.d3{animation-delay:560ms}
@keyframes rise{to{opacity:1;transform:none}}
input{width:100%;padding:11px 13px;font-size:16px;border-radius:6px;border:1px solid #564019;background:rgba(29,25,18,.72);color:#F3F3E6;box-sizing:border-box;transition:border-color 140ms ease-out,box-shadow 140ms ease-out}
input:hover{border-color:#6B4E1F}
input:focus{border-color:#D2A63C;box-shadow:0 0 0 3px rgba(210,166,60,.16);outline:none}
button{margin-top:10px;width:100%;padding:11px;font-size:14px;border-radius:6px;border:0;background:#D2A63C;color:#1D1912;font-weight:600;cursor:pointer;transition:background 140ms ease-out,transform 90ms ease-out}
button:hover{background:#EECD5C}button:active{transform:translateY(1px)}button:disabled{opacity:.5;cursor:default}
.err{color:#ff7b72;min-height:18px;font-size:12.5px;margin-top:11px;line-height:1.4}
:where(button,input):focus-visible{outline:2px solid #D2A63C;outline-offset:2px}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition-duration:1ms!important;animation-duration:1ms!important;animation-delay:0ms!important}.rise{opacity:1;transform:none}.glyph path{stroke-dashoffset:0}}
</style></head>
<body><div class=wrap><div class=box>
<svg class=glyph width="54" height="88" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="18" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>
<h2 class="rise d1">Melange</h2><p class="rise d2">Influence dashboard — enter the password to view. <span class=note>(case-sensitive)</span></p>
<div class="rise d3"><input id=pw type=password autocapitalize=none autocorrect=off autocomplete=off spellcheck=false placeholder=Password aria-label=Password autofocus><button id=go>Unlock</button><div class=err id=err role=alert></div></div>
</div></div>
<script>
var D={s:"__SALT__",i:"__IV__",c:"__CT__",n:__ITERS__};
var err=document.getElementById('err'),go=document.getElementById('go');
function u8(x){return Uint8Array.from(atob(x),function(c){return c.charCodeAt(0);});}
var SECURE=!!(window.crypto&&window.crypto.subtle);
if(!SECURE){err.textContent='⚠ This must be opened from the https:// link. On a plain http:// address (or a file sent directly) the browser blocks decryption — use the https link.';go.disabled=true;}
async function unlock(){if(!SECURE)return;err.textContent='Decrypting…';
 try{
  var pw=document.getElementById('pw').value.trim().replace(/[‐-―−]/g,'-');  // fix smart-dashes + stray spaces
  if(!pw){err.textContent='Enter the password.';return;}
  var enc=new TextEncoder();
  var bk=await crypto.subtle.importKey('raw',enc.encode(pw),'PBKDF2',false,['deriveBits']);
  var bits=await crypto.subtle.deriveBits({name:'PBKDF2',salt:u8(D.s),iterations:D.n,hash:'SHA-256'},bk,256);
  var key=await crypto.subtle.importKey('raw',bits,{name:'AES-GCM'},false,['decrypt']);
  var pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:u8(D.i)},key,u8(D.c));
  document.open();document.write(new TextDecoder().decode(pt));document.close();
 }catch(e){err.textContent='Wrong password — check capitalization and spacing.';}}
go.onclick=unlock;
document.getElementById('pw').addEventListener('keydown',function(e){if(e.key==='Enter')unlock();});
</script></body></html>'''

def lock_dashboard(source, output, password):
    """Encrypt SOURCE and atomically write the browser-decrypting wrapper."""
    if not password:
        raise RuntimeError('dashboard password is required; automatic password generation is disabled')
    source = os.path.abspath(source); output = os.path.abspath(output)
    with open(source, 'rb') as handle:
        html = handle.read()
    html.decode('utf-8')  # retain UTF-8 validation without changing source bytes
    salt = os.urandom(16); iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERS, dklen=32)
    ct = AESGCM(key).encrypt(iv, html, None)
    b64 = lambda value: base64.b64encode(value).decode()
    rendered = (WRAP.replace('__SALT__', b64(salt)).replace('__IV__', b64(iv))
                    .replace('__CT__', b64(ct)).replace('__ITERS__', str(ITERS)))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    temporary = '%s.%d.tmp' % (output, os.getpid())
    with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(rendered)
    os.replace(temporary, output)
    return {'source': source, 'output': output,
            'source_sha256': hashlib.sha256(html).hexdigest(),
            'output_sha256': hashlib.sha256(rendered.encode('utf-8')).hexdigest(),
            'iterations': ITERS}


def _password(args):
    configured = args.password_file or os.environ.get('DASHBOARD_PASSWORD_FILE')
    if not configured:
        raise RuntimeError('set --password-file or DASHBOARD_PASSWORD_FILE')
    value = open(configured, encoding='utf-8').read().strip()
    if not value:
        raise RuntimeError('dashboard password file is empty')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=SRC)
    parser.add_argument('--output', default=OUT)
    parser.add_argument('--password-file')
    args = parser.parse_args(argv)
    receipt = lock_dashboard(args.source, args.output, _password(args))
    print('LOCKED -> %s  (%.1f MB)' % (
        receipt['output'], os.path.getsize(receipt['output']) / 1048576))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
