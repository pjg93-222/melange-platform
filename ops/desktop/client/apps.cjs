'use strict';
const APPS = Object.freeze({
  melange: Object.freeze({id:'melange', name:'Melange', applicationId:'org.melange.desktop.melange', path:'/', health:'/api/staffer-live?limit=1'}),
  terrier: Object.freeze({id:'terrier', name:'Terrier', applicationId:'org.melange.desktop.terrier', path:'/', health:'/api/health'}),
  mentat: Object.freeze({id:'mentat', name:'Mentat', applicationId:'org.melange.desktop.mentat', path:'/', health:'/api/health'})
});
function appDefinition(id) {
  if (!Object.hasOwn(APPS,id)) throw new Error('Unknown application');
  return APPS[id];
}
module.exports={APPS,appDefinition};
