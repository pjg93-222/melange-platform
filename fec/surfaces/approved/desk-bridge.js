/* Read the existing generation; keep its local notes and source renderers authoritative. */
setBillState = function(key, field, value) {
  const next={...BILLSTATE,[key]:{...(BILLSTATE[key]||{}),[field]:value}};
  localStorage.setItem(BILLKEY,JSON.stringify(next));BILLSTATE=next;
};
window.MelangeDesk = {
  data: D,
  members: M,
  manual: () => MAN,
  writeManual(name, patch) {
    const next = {...MAN, [name]: {...(MAN[name] || {}), ...patch}};
    // Commit only after storage succeeds. A quota/privacy failure leaves the draft intact.
    localStorage.setItem(LSKEY, JSON.stringify(next));
    MAN = next;
    window.dispatchEvent(new CustomEvent('melange-notes-saved', {detail: {name}}));
  },
  show: setView,
  open: name => { if (byName[name] != null) openMember(byName[name]); },
  billIdentity,
  billState,
  setBillState,
  bills: () => BILLS,
  coalition: () => ({members:[...coal].map(i=>M[i].name),basis:cbBasis,area:cbArea,snapshots:CBSNAPS}),
  addCoalition(names) { for (const name of names) if(byName[name]!=null)coal.add(byName[name]);renderCoal(); },
  saveCoalition(name, bill) {
    const snapshot={id:Date.now(),name,bill,basis:cbBasis,members:[...coal].map(i=>M[i].name),area:cbArea,party:cbParty,committee:cbCommittee,sourceType:'Scenario',saved_at:new Date().toISOString()};
    const next=[snapshot,...CBSNAPS];localStorage.setItem(CBSNAPKEY,JSON.stringify(next));CBSNAPS=next;renderSnapshots();return snapshot;
  },
  loadCoalition(id) {
    const s=CBSNAPS.find(s=>String(s.id)===String(id));if(!s)return;
    coal=new Set((s.members||[]).map(n=>byName[n]).filter(i=>i!=null));cbArea=s.area||'';cbParty=s.party||'';cbCommittee=s.committee||'';cbBasis=s.basis||'cosponsor';
    $('#cbarea').value=cbArea;$('#cbparty').value=cbParty;$('#cbcommittee').value=cbCommittee;
    document.querySelectorAll('#cbbasis button').forEach(b=>b.classList.toggle('on',b.dataset.b===cbBasis));renderCoal();return s;
  },
  staffState: () => STF,
  staffIndex: () => STFIDX
};
