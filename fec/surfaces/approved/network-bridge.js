window.MelangeNetwork = {
  data: DATA,
  state: () => ({issue, network: NETCFG, member: sel >= 0 ? DATA.name[sel] : null, targets: ORD.map(([i]) => DATA.name[i]), budget}),
  select: name => { const i = DATA.name.indexOf(name); if (i >= 0) selectMember(i); },
  reset: () => { fit(); draw(); },
  zoom: scale => { cam.tx = W / 2 - (W / 2 - cam.tx) * scale; cam.ty = H / 2 - (H / 2 - cam.ty) * scale; cam.s *= scale; draw(); },
  links: name => { const i = DATA.name.indexOf(name); return i < 0 ? [] : EDGES.filter(e => e[0] === i || e[1] === i).map(e => ({name: DATA.name[e[0] === i ? e[1] : e[0]], weight: e[2]})); }
};
