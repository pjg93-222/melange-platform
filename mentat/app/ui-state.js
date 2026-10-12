/* Shared display rules; no timers are estimated from task counts. */
(function(root) {
  const normalizeName = value => String(value || '').normalize('NFKC').trim().replace(/\s+/g,' ').toLocaleLowerCase('en-US');
  // Display-only counterpart of R/report.R spending_title_role. The report also
  // knows executive/entity evidence unavailable in a raw incoming-record preview.
  function titlePosition(title) {
    if(!String(title||'').trim())return 'Unreported';
    const expansions={SVP:'SENIOR VICE PRESIDENT',EVP:'EXECUTIVE VICE PRESIDENT',VP:'VICE PRESIDENT',CEO:'CHIEF EXECUTIVE OFFICER',CFO:'CHIEF FINANCIAL OFFICER',COO:'CHIEF OPERATING OFFICER'};
    const value=String(title).toUpperCase().replace(/\b(SVP|EVP|VP|CEO|CFO|COO)\b/g,m=>expansions[m]).replace(/[^A-Z0-9]+/g,' ').trim();
    if(/\b(CHIEF (EXECUTIVE|FINANCIAL|OPERATING|TECHNOLOGY|INFORMATION|LEGAL|MARKETING|PEOPLE|PRODUCT|RISK|COMPLIANCE|REVENUE|STRATEGY|ACCOUNTING) OFFICER|CEO|CFO|COO|CTO|CIO|CLO|CMO|GENERAL COUNSEL|FOUNDER|OWNER|CHAIRMAN|CHAIRWOMAN|CHAIRPERSON)\b/.test(value)||/\bPRESIDENT\b/.test(value)&&!/\bVICE PRESIDENT\b/.test(value))return 'Chief';
    return /\b(VICE PRESIDENT|DIRECTOR|PARTNER|HEAD|MANAGER|MANAGEMENT)\b/.test(value)?'Middle-Management':'Employees';
  }
  function clockText(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return 'Unavailable';
    const n=Math.floor(seconds);
    return `${Math.floor(n/3600)}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
  }
  function timer(run, since=0) {
    const status=run.status||{}, state=run.state;
    const terminalStates=['completed','failed','stopped','launch failed'];
    const terminal=terminalStates.includes(state)||terminalStates.includes(status.stage);
    const elapsed=run.elapsed_seconds ?? status.elapsed_seconds;
    const age=run.age_seconds;
    const remaining=status.estimated_remaining_seconds;
    const result={elapsed:clockText(typeof elapsed==='number'?elapsed+(terminal?0:since):NaN), remaining:'Estimating…'};
    if(terminal)result.remaining=state==='stopped'?'Stopped':[state,status.stage].some(value=>['failed','launch failed'].includes(value))?'Failed':status.stage==='stopped'?'Stopped':'Finished';
    else if(['ready','needs review'].includes(state))result.remaining='Not started';
    else if(age==null||age+since>60)result.remaining='Waiting for checkpoint';
    else if(typeof remaining==='number'&&remaining>0)result.remaining=remaining-age-since>0?'About '+clockText(remaining-age-since):'Estimate exceeded';
    return result;
  }
  // Per-browser display memory (build 03: the Chart Studio Inspector's
  // collapse). Storage can be missing or throw (a private window, blocked site
  // data), so a read answers null and a write answers false instead of failing.
  function recall(key) {
    try { return root.localStorage ? root.localStorage.getItem(String(key)) : null; } catch (error) { return null; }
  }
  function remember(key, value) {
    try { if (!root.localStorage) return false; root.localStorage.setItem(String(key), String(value)); return true; } catch (error) { return false; }
  }
  const api={normalizeName,titlePosition,clockText,timer,recall,remember};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MentatState=api;
})(globalThis);
