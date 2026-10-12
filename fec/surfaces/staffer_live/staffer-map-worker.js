/* Multiscale overview layout away from the UI thread. No relationships inferred. */
'use strict';
const hash=value=>{let h=2166136261;for(const c of String(value)){h^=c.charCodeAt(0);h=Math.imul(h,16777619);}return h>>>0;};
const normalize=value=>String(value).normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
let names=[],activeGeneration=0;
self.onmessage=({data})=>{
  if(data.type==='search'){
    const tokens=normalize(data.query).trim().split(/\s+/).filter(Boolean),matches=[];
    for(let i=0;i<names.length;i++)if(tokens.every(t=>names[i].includes(t)))matches.push(i);
    self.postMessage({type:'search',generation:activeGeneration,request:data.request,matches});return;
  }
  const {generation,people}=data,positions=new Float32Array(people.length*2);
  activeGeneration=generation;names=people.map(p=>normalize(p.slice(2).join(' ')));
  const levels=[8,32,128].map(size=>({size,bins:new Map()}));
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  people.forEach(([id,group],i)=>{
    const h=hash(group||id),a=(h%100000)/100000*Math.PI*2,r=120+Math.sqrt((h>>>8)%10000)/100*760;
    const nh=hash(id),na=(nh%10000)/10000*Math.PI*2,offset=10+Math.sqrt((nh>>>8)%1000)*2.3;
    const x=Math.cos(a)*r+Math.cos(na)*offset,y=Math.sin(a)*r+Math.sin(na)*offset;
    positions[i*2]=x;positions[i*2+1]=y;
    minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);
    for(const level of levels){const cx=Math.floor(x/level.size),cy=Math.floor(y/level.size),key=cx+','+cy;
      let bin=level.bins.get(key);if(!bin){bin={cx,cy,x:0,y:0,count:0,first:i};level.bins.set(key,bin);}bin.x+=x;bin.y+=y;bin.count++;}
  });
  const tiles=levels.map(({size,bins})=>({size,tiles:[...bins.values()].map(b=>({...b,x:b.x/b.count,y:b.y/b.count}))}));
  self.postMessage({type:'layout',generation,positions,levels:tiles,bounds:{minX,minY,maxX,maxY}},[positions.buffer]);
};
