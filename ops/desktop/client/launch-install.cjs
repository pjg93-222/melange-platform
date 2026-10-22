'use strict';
// State of the launch-time update install (main.cjs). While it runs there is no window or backend yet, so a launch
// request (second instance, access IPC) must only be recorded, never started.
class LaunchInstall{
  constructor({createWindow}={}){this.installing=false;this.requested=false;this.createWindow=createWindow;this.window=null;}
  showWindow(){if(this.window&&!this.window.isDestroyed())return;this.window=this.createWindow();}
  // Synchronous on purpose: a closable:false window left open would cancel app.quit().
  closeWindow(){const window=this.window;this.window=null;if(window&&!window.isDestroyed())window.destroy();}
  begin(){this.installing=true;this.requested=false;}
  // True while installing: the request is recorded and the caller must not launch anything.
  defer(){if(!this.installing)return false;this.requested=true;return true;}
  // Read after preparation: an interactive start, or a launch asked for while the update installed.
  relaunch(interactive){return Boolean(interactive||this.requested);}
  // Ends the install phase and reports whether a launch was requested during it.
  end(){const requested=this.requested;this.installing=false;this.requested=false;return requested;}
}
module.exports={LaunchInstall};
