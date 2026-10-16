'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {scopedTokens}=require('../theme-tokens.cjs');
test('shared chrome uses existing warm family and strong controls, with app-specific heading fonts',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../desktop-chrome.css'),'utf8');
 for(const token of ['--desktop-bg:#FCF7F4','--desktop-paper:#F7EDE4','--desktop-text:#342649','--desktop-accent:#57447B','--desktop-amber:#ECA91C','--desktop-healthy:#327A7A','--desktop-control-border:#8E819B'])assert.ok(css.includes(token));
 assert.match(css,/\[hidden\]\{display:none!important\}/);assert.match(css,/body\[data-app=terrier\]/);assert.match(css,/min-height:44px/);assert.match(css,/prefers-reduced-motion/);assert.doesNotMatch(css,/#11161B|#A9DBC8/);
});
test('Terrier auth gets only deterministic scoped tokens, without changing domain/global styles',()=>{
 const generated=scopedTokens();assert.ok(generated.startsWith('/* Generated'));assert.ok(generated.includes('.terrier-auth{'));assert.doesNotMatch(generated,/:root|body\{|Geist|Georgia/);
 if(process.env.TERRIER_AUTH_THEME_PATH)assert.equal(fs.readFileSync(process.env.TERRIER_AUTH_THEME_PATH,'utf8'),generated);
});
