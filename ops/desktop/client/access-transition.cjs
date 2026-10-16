'use strict';
// Trust selection is deliberately optimistic: a chooser never blocks normal Open.
// Its result is refused if another access operation began while it was open.
class AccessTransition {
 constructor(hasBackend){this.hasBackend=hasBackend;this.sequence=0;this.active=0;this.mutating=false;}
 begin(){if(this.mutating)throw new Error('Administrator trust is being verified');this.sequence++;this.active++;let ended=false;return()=>{if(!ended){ended=true;this.active--;}};}
 choose(){if(this.mutating||this.active||this.hasBackend())throw new Error('Close the app before changing administrator trust');return this.sequence;}
 commit(ticket){if(this.mutating||this.active||this.hasBackend()||ticket!==this.sequence)throw new Error('Access changed while administrator trust was selected');this.mutating=true;this.sequence++;let ended=false;return()=>{if(!ended){ended=true;this.mutating=false;}};}
}
module.exports={AccessTransition};
