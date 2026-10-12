/* Affiliations beside the same people network; one shared search field. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const fmt=n=>Number.isFinite(Number(n))?Number(n).toLocaleString():'—';
  const add=(parent,tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value??'';if(cls)e.className=cls;parent.append(e);return e;};
  let data=null,loading=null,revision=null,expanded=new Set(),limits={};

  function match(group,words){
    if(!words.length)return true;
    const text=group.name.toLowerCase();
    return words.every(word=>text.includes(word));
  }
  function renderPeople(out,group){
    const list=add(out,'div','','group-people');
    for(const person of group.people){
      const row=add(list,'div','','group-person');
      const button=add(row,'button',person.name,'result');
      button.onclick=()=>{
        window.StafferTabs?.show('explore');
        // The person may not be in the bounded map yet; StafferLive.select
        // refuses an id it does not already hold, so seed the node first.
        window.StafferLive?.addNodes([{id:person.id,label:person.name,kind:'person'}]);
        window.StafferLive?.select(person.id,true);
      };
      add(button,'small',[person.current?'Current':'',person.membership==='represents'?'Represents this organisation':person.membership==='employee'?'Employee':'',person.title,person.as_of?'As of '+person.as_of:''].filter(Boolean).join(' · '));
    }
    if(group.more)add(out,'p',fmt(group.more)+' more in this group. Search a name above to reach their record.','note');
    if(!group.people.length)add(out,'p','No people in this group.','note');
  }
  function renderList(target,groups,words,emptyText){
    const out=$(target);out.replaceChildren();
    const matching=groups.filter(group=>match(group,words)),shown=matching.slice(0,limits[target]||50);
    for(const group of shown){
      const item=add(out,'details','','group');
      item.open=expanded.has(group.id);
      item.addEventListener('toggle',()=>{item.open?expanded.add(group.id):expanded.delete(group.id);if(item.open&&!item.dataset.filled){item.dataset.filled='1';renderPeople(item,group);}});
      const summary=add(item,'summary',group.name);
      add(summary,'small',fmt(group.current_count)+' current · '+fmt(group.people_count)+' total');
      if(item.open&&!item.dataset.filled){item.dataset.filled='1';renderPeople(item,group);}
    }
    if(shown.length<matching.length){const more=add(out,'button','Show more ('+fmt(matching.length-shown.length)+' remaining)');more.onclick=()=>{limits[target]=(limits[target]||50)+50;render();};}
    if(!shown.length)add(out,'p',emptyText,'empty');
    return {shown:shown.length,total:matching.length};
  }
  function render(){
    if(!data)return;
    const words=$('search').value.toLowerCase().split(/\s+/).filter(Boolean);
    const offices=renderList('groups-offices',data.offices,words,'No offices match that search.');
    const organizations=renderList('groups-organizations',data.organizations,words,'No organisations match that search.');
    $('groups-offices-count').textContent=fmt(offices.shown)+' of '+fmt(offices.total);
    $('groups-organizations-count').textContent=fmt(organizations.shown)+' of '+fmt(organizations.total);
    $('groups-status').textContent=`${fmt(data.counts.offices)} offices · ${fmt(data.counts.organizations)} organisations · ${fmt(data.counts.people_in_groups)} people listed`;
    $('groups-note').textContent=[data.employment_note,data.note].filter(Boolean).join(' ');
  }
  async function load(force){
    if(loading)return loading;
    $('groups-status').textContent='Loading offices and organisations…';
    loading=(async()=>{
      try{
        const response=await fetch('/api/staffer-groups-index',{cache:force?'reload':'no-cache'});
        if(!response.ok)throw Error('Groups HTTP '+response.status);
        const payload=await response.json();
        if(payload.ready===false){$('groups-status').textContent='Loading: '+(payload.stage||'records');setTimeout(()=>{loading=null;load();},3000);return;}
        data=payload;revision=payload.revision;render();
      }catch(error){
        $('groups-status').textContent=error.message+' — use Refresh to retry.';
      }finally{loading=null;}
    })();
    return loading;
  }
  $('groups-panel').addEventListener('toggle',()=>{if($('groups-panel').open&&!data)load();});
  window.addEventListener('staffer-revision',event=>{
    const next=event.detail?.source_revision||event.detail?.revision;
    if(data&&next&&next!==revision&&$('groups-panel').open){data=null;load(true);}
  });
  window.StafferGroups={search(){expanded.clear();limits={};render();},refresh(){data=null;expanded.clear();limits={};if($('groups-panel').open)load(true);},get ready(){return Boolean(data);}};
})();
