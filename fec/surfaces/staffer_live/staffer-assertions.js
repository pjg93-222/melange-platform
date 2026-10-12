(() => {
  'use strict';
  const panel = document.getElementById('assertion-records');
  const status = document.getElementById('assertion-status');
  const add = (parent, tag, text) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    parent.append(node);
    return node;
  };
  async function refreshAssertions() {
    try {
      const response = await fetch('/api/staffer-assertions');
      if (!response.ok) throw new Error('Could not load your statements');
      const data = await response.json();
      panel.replaceChildren();
      for (const record of data.assertions) {
        const card = add(panel, 'section');
        add(card, 'p', record.assertion);
        add(card, 'p', `Effective ${record.as_of}${record.effective_until ? ' through ' + record.effective_until + ' (exclusive)' : ' onward'} · ${record.status || 'active'}`);
        if (record.status !== 'active') continue;
        const form = add(card, 'form');
        const dateLabel = add(form, 'label', 'Effective date (Eastern time)');
        const date = add(dateLabel, 'input');
        date.type = 'date'; date.value = data.local_date; date.min = record.as_of; date.max = data.local_date; date.required = true;
        const statementLabel = add(form, 'label', 'Replacement statement about these same people');
        const statement = add(statementLabel, 'textarea');
        statement.value = record.assertion; statement.maxLength = 2000;
        const replace = add(form, 'button', 'Replace statement');
        replace.type = 'submit'; replace.value = 'supersede';
        const retract = add(form, 'button', 'Retract statement');
        retract.type = 'submit'; retract.value = 'retract';
        form.addEventListener('submit', async event => {
          event.preventDefault();
          const action = event.submitter?.value;
          if (!action) return;
          replace.disabled = retract.disabled = true;
          status.textContent = 'Saving statement and updating routes…';
          try {
            const saved = await fetch('/api/staffer-assertions', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({assertion_id: record.id, action, effective_date: date.value, statement: statement.value})
            });
            const result = await saved.json();
            if (!saved.ok) throw new Error(result.error || 'Could not save the statement');
            window.dispatchEvent(new CustomEvent('staffer-assertions-changed'));
            window.StafferLive.clearRoute();
            window.StafferLive.refresh();
            status.textContent = 'Saved. Routes are updating; the previous statement remains in history.';
            await refreshAssertions();
          } catch (error) {
            status.textContent = error.message;
            replace.disabled = retract.disabled = false;
          }
        });
      }
    } catch (error) { status.textContent = error.message; }
  }
  refreshAssertions();
})();
