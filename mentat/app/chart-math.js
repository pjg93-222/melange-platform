/* Pure local chart calculations, also exercised by node:test. */
(function (root) {
  'use strict';
  function numeric(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string') return null;
    const raw = value.trim(), accounting = raw.startsWith('(') && raw.endsWith(')');
    const text = accounting ? raw.slice(1,-1) : raw;
    if (!/^-?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$/.test(text) || accounting && text.startsWith('-')) return null;
    let n = Number(text.replace(/[$%]/g, '').replaceAll(',', ''));
    if (accounting) n = -n;
    if (text.endsWith('%')) n /= 100;
    return Number.isFinite(n) ? n : null;
  }
  function quantile(sorted, p) {
    if (!sorted.length) return null;
    const i = (sorted.length - 1) * p, low = Math.floor(i);
    return sorted[low] + (sorted[Math.ceil(i)] - sorted[low]) * (i - low);
  }
  function box(values) {
    const a = values.slice().sort((x, y) => x - y);
    const q1 = quantile(a, .25), median = quantile(a, .5), q3 = quantile(a, .75), iqr = q3 - q1;
    const inside = a.filter(n => n >= q1 - 1.5 * iqr && n <= q3 + 1.5 * iqr);
    return {n:a.length, q1, median, q3, low:inside[0], high:inside.at(-1),
      outliers:a.filter(n => n < q1 - 1.5 * iqr || n > q3 + 1.5 * iqr)};
  }
  // r3 (Chart Studio, Paul: "nothing is logically constrained"). config.lenient
  // turns every composition refusal into a warning: the build draws what it can
  // and says what it could not, in result.warnings. A context carries lenient
  // mode, the warnings and a withheld-total sentence through one build. Without
  // config.lenient every refusal below throws exactly as before.
  const context = config => ({lenient: !!(config && config.lenient), warnings: [], totalRefusal: ''});
  const warn = (ctx, message) => { if (ctx) ctx.warnings.push(message); };
  // An honesty guard that used to refuse the chart: lenient mode draws it and
  // withholds the grand total with the same sentence.
  function refuse(ctx, message) {
    if (!ctx || !ctx.lenient) throw new Error(message);
    if (!ctx.totalRefusal) ctx.totalRefusal = message;
    ctx.warnings.push(message);
  }
  const FILTER_OPS = ['equals','contains','gte','lte','in','notin','between'];
  function lenientFilter(data, f, ctx) {
    if (!f || !data.columns[f.column]) { warn(ctx, 'A filter on a field this worksheet does not have was skipped.'); return []; }
    if (!FILTER_OPS.includes(f.op)) { warn(ctx, 'A filter with an unknown operation was skipped.'); return []; }
    if (['gte','lte'].includes(f.op) && numeric(f.value) === null) {
      warn(ctx, 'The ' + (f.op === 'gte' ? 'at-least' : 'at-most') + ' filter on ' + (data.columns[f.column].name || 'a field') +
        ' is not a number, so it compares text.');
      return [Object.assign({}, f, {lexical:true})];
    }
    if (['in','notin'].includes(f.op)) {
      const members = Array.isArray(f.value) ? f.value : f.value == null || f.value === '' ? [] : [f.value];
      return members.length ? [Object.assign({}, f, {value:members})] : [];
    }
    if (f.op === 'between' && (!Array.isArray(f.value) || f.value.every(bound => bound === '' || bound == null))) return [];
    return [f];
  }
  function filterRows(data, config, ctx) {
    if (!ctx && config && config.lenient) ctx = context(config);
    const lenient = !!(ctx && ctx.lenient);
    let limit = config.limit, filters = config.filters || [], start = config.start, end = config.end;
    if (config.limit != null && config.limit !== 0 && (!Number.isInteger(config.limit) || config.limit < 1)) {
      if (!lenient) throw new Error('Row count must be a positive whole number.');
      const whole = Math.trunc(Number(config.limit));
      limit = Number.isFinite(whole) && whole > 0 ? whole : 0;
      warn(ctx, limit ? 'The row count was rounded to ' + limit + '.' : 'The row count was not a positive whole number, so every matching row is used.');
    }
    if (lenient) filters = filters.flatMap(f => lenientFilter(data, f, ctx));
    else for (const f of filters) {
      if (!data.columns[f.column]) throw new Error('Choose a valid filter column.');
      if (!['equals','contains','gte','lte','in','notin','between'].includes(f.op)) throw new Error('Choose a filter operation.');
      if (['gte','lte'].includes(f.op) && numeric(f.value) === null) throw new Error('A numeric filter needs a number.');
      // A member list and a range are the filter shapes that are not scalars.
      if (['in','notin'].includes(f.op) && (!Array.isArray(f.value) || !f.value.length)) throw new Error('A member filter needs at least one chosen value.');
      if (f.op === 'between' && (!Array.isArray(f.value) || f.value.every(bound => bound === '' || bound == null))) throw new Error('A range filter needs a lower or an upper bound.');
    }
    if (config.start && config.end && config.start > config.end) {
      if (!lenient) throw new Error('First worksheet row must precede the last.');
      start = config.end; end = config.start;
      warn(ctx, 'The first and last worksheet rows were given in reverse, so they were swapped.');
    }
    const matching = data.rows.filter((row, i) => {
      const number = data.row_numbers[i];
      if (start && number < start || end && number > end) return false;
      return filters.every(f => {
        const v = row[f.column], text = String(v ?? '').toLocaleLowerCase(), wanted = String(f.value).toLocaleLowerCase();
        if (f.op === 'between') {
          const [low, high] = f.value;
          const inclusive = (bound, test) => bound === '' || bound == null || test(bound);
          const n = numeric(v);
          if (n !== null) return inclusive(low, b => n >= numeric(b)) && inclusive(high, b => n <= numeric(b));
          const raw = String(v ?? '');
          return raw !== '' && inclusive(low, b => raw >= String(b)) && inclusive(high, b => raw <= String(b));
        }
        if (f.op === 'in' || f.op === 'notin') {
          const chosen = f.value.some(member => String(member).toLocaleLowerCase() === text);
          return f.op === 'in' ? chosen : !chosen;
        }
        if (f.op === 'equals') return text === wanted;
        if (f.op === 'contains') return text.includes(wanted);
        if (f.lexical) {
          const raw = String(v ?? '').trim();
          return raw !== '' && (f.op === 'gte' ? raw >= String(f.value) : raw <= String(f.value));
        }
        const n = numeric(v), threshold = numeric(f.value);
        return n !== null && (f.op === 'gte' ? n >= threshold : n <= threshold);
      });
    });
    return limit ? matching.slice(0, limit) : matching;
  }
  const present = value => value != null && !(typeof value === 'string' && !value.trim());
  // countd (W12) observes the trimmed text of a present value, so distinct
  // members are counted however the cell is typed.
  const observation = (row, config) => config.aggregation === 'count'
    ? (config.y == null || config.y < 0 || present(row[config.y]) ? 1 : null)
    : config.aggregation === 'countd' ? (present(row[config.y]) ? String(row[config.y]).trim() : null)
    : numeric(row[config.y]);
  function dimensions(config) {
    return [...new Set(Array.isArray(config.dimensions) ? config.dimensions : config.x >= 0 ? [config.x] : [])];
  }
  function groupName(row, config) {
    const fields = dimensions(config);
    if (!fields.length) return 'All rows';
    const values = fields.map(column => row[column] == null ? null : String(row[column]));
    // JSON tuples are unambiguous even when members contain the display separator.
    return values.length === 1 ? values[0] ?? '(Missing)' : JSON.stringify(values);
  }
  function validateGrain(data, config, rows, ctx) {
    const accounting = data.accounting || {};
    const summing = config.aggregation === 'sum';
    const measureAccounting = data.columns[config.y]?.accounting || {};
    if (summing && measureAccounting.safe_to_sum === false) return refuse(ctx, measureAccounting.reason || 'This raw measure is not safe to sum. Choose the analysis amount.');
    if (summing && accounting.safe_to_sum === false) return refuse(ctx, accounting.reason || 'This worksheet is not an additive fact table. Choose a reconciled contribution table or a safe subsection.');
    const roles = accounting.row_role_column;
    if (summing && Number.isInteger(roles) && rows.some(row => !(accounting.included_row_roles || ['fact']).includes(row[roles]))) {
      return refuse(ctx, 'Summary and detail rows cannot be summed together. Filter to one declared fact-row role.');
    }
    const groupedBy = new Set([...dimensions(config), config.series]);
    for (const column of accounting.independent_dimensions || []) {
      if (summing && !groupedBy.has(column) && new Set(rows.map(row => row[column])).size > 1) {
        return refuse(ctx, 'Keep ' + (data.columns[column]?.name || 'the accounting basis') + ' in the grouping or filter to one value; these amounts can overlap.');
      }
    }
  }
  function describeResult(result, data, config, ctx) {
    const fields = dimensions(config);
    if (fields.length > 1) result.groupingLabel = fields.map(column => data.columns[column]?.name || 'Field').join(' × ');
    if (data.note) result.note = [result.note, data.note].filter(Boolean).join(' ');
    if (data.accounting) result.accounting = data.accounting;
    const independent = [...(data.accounting?.independent_dimensions || []), ...(data.independent_series == null ? [] : [data.independent_series])];
    if (independent.some(column => new Set(filterRows(data, config, ctx).map(row => row[column])).size > 1)) {
      result.totalRefusal = 'Grand total is not shown: the displayed accounting groups can overlap.';
    }
    return result;
  }
  // The last step of every r3 build: the withheld-total sentence and the
  // warnings (each said once). A legacy build has neither and is unchanged.
  function finish(result, ctx) {
    if (!ctx) return result;
    if (ctx.totalRefusal) result.totalRefusal = ctx.totalRefusal;
    const warnings = [...new Set(ctx.warnings)];
    if (warnings.length) result.warnings = warnings;
    return result;
  }
  function validateRollup(data, config, ctx) {
    const independent = [...(data.accounting?.independent_dimensions || []), ...(data.independent_series == null ? [] : [data.independent_series])];
    if (config.aggregation === 'sum' && independent.some(column => column !== config.series &&
      new Set(filterRows(data,config,ctx).map(row => row[column])).size > 1)) {
      refuse(ctx, 'Other cannot combine overlapping accounting groups. Raise the group limit or put the accounting basis on Colour.');
    }
  }
  // The first fifteen are the classic dialog's; r3 appends the rest (W6).
  const chartTypes=['bar','pie','line','scatter','box','histogram','area','doughnut','waterfall','radar','horizontal','pareto','grouped','groupedhorizontal','multiline',
    'dot','multidot','multiarea','heatmap','highlight','treemap','bubbles','bullet','gantt'];
  const aggregations=['sum','mean','count','median','min','max','countd'];
  const SINGLE_TYPES=['bar','pie','line','scatter','box','histogram','area','doughnut','waterfall','radar','horizontal','pareto',
    'dot','heatmap','highlight','treemap','bubbles','bullet','gantt'];
  const MULTI_TYPES=['grouped','groupedhorizontal','multiline','multiarea','multidot'];
  const SERIES_OF={bar:'grouped',horizontal:'groupedhorizontal',line:'multiline',area:'multiarea',dot:'multidot'};
  const BASE_OF={grouped:'bar',groupedhorizontal:'horizontal',multiline:'line',multiarea:'area',multidot:'dot'};
  const INDIVIDUAL=['scatter','box','histogram'];
  const SHARES=['pie','doughnut','pareto','radar'];
  // Types whose Colour joins the grouping tuple and colours each mark (W11).
  const TUPLE_COLOUR=['box','pie','doughnut','waterfall','pareto','radar','heatmap','highlight','treemap','bubbles','bullet','gantt'];
  const SIZED=['scatter','dot','bar','horizontal','line','area','grouped','groupedhorizontal','multiline','multiarea','multidot'];
  const AGG_NAMES={sum:'SUM',mean:'AVG',count:'COUNT',countd:'COUNTD',median:'MEDIAN',min:'MIN',max:'MAX',first:'FIRST'};
  // Refusing to draw is worse than showing the head of the distribution: the tail
  // is pooled into one Other mark, so the grand total still balances. 60 is the
  // standing per-view contract of the two callers that pass no limit - the classic
  // dialog in charts.js and funding-comparison.js, whose own control reads "Enter
  // 1-60 recipients per view". At 60 they draw exactly what they drew before and
  // only fold, with a note, where they used to throw. Chart Studio passes its own
  // tighter default from its compiler.
  const DEFAULT_GROUP_LIMIT = 60;
  function groupLimit(config, ctx) {
    const n = config.maxGroups;
    // W10: 'all' draws every group, with no fold and no rollup note.
    if (n === 'all') return Infinity;
    if (n == null || n === 0) return DEFAULT_GROUP_LIMIT;
    if (!Number.isInteger(n) || n < 1) {
      if (!ctx || !ctx.lenient) throw new Error('Group limit must be a positive whole number.');
      const whole = Math.trunc(Number(n));
      if (Number.isFinite(whole) && whole >= 1) return whole;
      warn(ctx, 'The group limit was not a positive whole number, so the default of ' + DEFAULT_GROUP_LIMIT + ' is used.');
      return DEFAULT_GROUP_LIMIT;
    }
    return n;
  }
  // The one place a group of raw values becomes its mark's number. The Other
  // rollup calls it on the pooled raw values and never on already-aggregated
  // ones, so a pooled mean is the mean of the rows, not a mean of means.
  function aggregate(values, config, count) {
    if (count) return values.length;
    if (config.aggregation === 'countd') return new Set(values).size;
    if (config.aggregation === 'median') return quantile(values.slice().sort((a, b) => a - b), .5);
    // reduce, not Math.min(...values): the Other rollup pools every folded row and
    // a spread of that many arguments overflows the call stack. Every value here
    // is a finite number (numeric() already dropped null), so both forms agree.
    if (config.aggregation === 'min') return values.reduce((low, v) => v < low ? v : low, Infinity);
    if (config.aggregation === 'max') return values.reduce((high, v) => v > high ? v : high, -Infinity);
    return values.reduce((a, b) => a + b, 0) / (config.aggregation === 'mean' ? values.length : 1);
  }
  // One builder owns the rollup's name, so the mark's label and the sentence that
  // explains it can never drift apart. The engine's default of 60 puts a one-group
  // fold within reach of the legacy callers, so the count agrees with its noun and verb.
  const otherLabel = folded => 'Other (' + folded + (folded === 1 ? ' group)' : ' groups)');
  const rollupNote = (limit, total, folded, by) => 'Top ' + limit + ' of ' + total + ' groups by ' + by +
    '; the other ' + folded + (folded === 1 ? ' group is' : ' groups are') + ' combined into "' + otherLabel(folded) +
    '". Raise the group limit in Parameters to show more.';
  const splitNote = split => split.cells + ' multi-value ' + split.columns.join(' / ') + ' cells were split on ";" into ' +
    split.values + ' distinct values. Coverage counts count a split row once per value; they are not additive across groups and do not allocate money.';
  const SPLIT_REFUSAL = 'Grand total is not shown: split cells repeat the measure across values.';
  // Some worksheet cells hold several values joined with "; " - one filing that
  // names three ZIPs. Splitting them is opt-in because it repeats that row's
  // measure once per value, which answers a coverage question and overstates money.
  function explodeRows(data, config, ctx) {
    if (!config.splitMulti) return data;
    // Only grouping fields split: a scatter x is a measure and a histogram has
    // no x at all. A column named by both x and series is split once.
    const targets = [...dimensions(config), config.series].filter((column, at, all) => column >= 0 && all.indexOf(column) === at &&
      data.columns[column] && data.columns[column].type !== 'number');
    if (!targets.length) return data;
    const rows = [], row_numbers = [];
    let cells = 0, values = 0, paired = false;
    const splitting = new Set(); // Candidates that actually produced a split.
    data.rows.forEach((row, i) => {
      let batch = [row];
      for (const column of targets) {
        const tokens = String(row[column] ?? '').split(';').map(s => s.trim()).filter(Boolean);
        if (tokens.length < 2) continue;
        const parts = [...new Set(tokens)];
        if (parts.length > 1 && batch.length > 1) {
          if (!ctx || !ctx.lenient) throw new Error('Do not split multiple fields together: their pairings are unknown. Choose one coverage dimension.');
          if (!paired) warn(ctx, 'Two multi-value fields were split together; every pairing of their values is counted, although the true pairings are unknown.');
          paired = true;
        }
        cells++; values += parts.length; splitting.add(column);
        batch = batch.flatMap(current => parts.map(part => {
          const copy = current.slice(); copy[column] = part; return copy;
        }));
      }
      for (const emitted of batch) { rows.push(emitted); row_numbers.push(data.row_numbers[i]); }
    });
    if (!cells) return data;
    return Object.assign({}, data, {rows, row_numbers,
      // Name only the columns that actually split. A candidate with no multi-value
      // cell contributed nothing, and naming it would make the note claim it did.
      split:{cells, values, columns:targets.filter(column => splitting.has(column)).map(column => data.columns[column].name)}});
  }
  const timeColumn=c=>c.type==='date'||/\b(year|cycle|date)\b/i.test(c.name);
  const identifierColumn=c=>/(?:^|[\s_])(ids?(?:\(s\))?|identifier|codes?|zip(?:s|5|9)?|zipcode|postal|district|index|row number|filing number|transaction number)(?:$|[\s_()])/i.test(c.name);
  function chartColumns(columns) {
    const indexed=columns.map((c,index)=>({...c,index:c.index??index}));
    const measures=indexed.filter(c=>c.type==='number'&&!timeColumn(c)&&!identifierColumn(c)&&c.accounting?.safe_to_sum!==false).sort((a,b)=>measureScore(b)-measureScore(a));
    const categories=indexed.filter(c=>c.type==='category'&&!identifierColumn(c)&&!timeColumn(c)&&(c.distinct==null||c.distinct>1&&c.distinct<=60));
    const times=indexed.filter(timeColumn);
    return {indexed,measures,categories,times};
  }
  function measureScore(column) {
    return /\b(net giving|amount|total giving)\b/i.test(column.name)?3:/giving|contribution|receipt|disbursement|spend|donat|cost|dollar|total|fund/i.test(column.name)?2:1;
  }
  function recommend(columns) {
    const {measures,categories,times}=chartColumns(columns);
    const category=categories[0], date=times[0];
    const choices = [];
    if (measures.length) {
      choices.push({type:'box', x:category?.index ?? -1, y:measures[0].index, aggregation:'sum', label:'Distribution · box plot'});
      choices.push({type:'histogram', x:-1, y:measures[0].index, aggregation:'count', label:'Frequency · histogram'});
    }
    if (category) {
      choices.unshift({type:'bar', x:category.index, y:measures[0]?.index ?? -1, aggregation:measures.length ? 'sum' : 'count', label:'Compare groups · bar chart'});
      choices.push({type:'pie', x:category.index, y:-1, aggregation:'count', label:'Share of rows · pie chart'});
    }
    if (date && measures.length) choices.push({type:'line', x:date.index, y:measures[0].index, aggregation:'sum', label:'Over time · line chart'});
    if (measures.length > 1) choices.push({type:'scatter', x:measures[0].index, y:measures[1].index, aggregation:'sum', label:'Compare measures · scatter plot'});
    return choices;
  }
  function quickConfig(data, current, choice) {
    const {indexed,measures,categories,times}=chartColumns(data.columns);
    const time=times.find(c=>c.index===current.x)||times[0];
    const temporal=['annual','yoy','change','forecast'].includes(choice);
    if(!temporal&&!chartTypes.includes(choice))throw new Error('Choose a chart type.');
    const numericOnly=['scatter','histogram','box'].includes(choice);
    const rowCount=current.y===-1&&current.aggregation==='count'&&!numericOnly;
    const selectedMeasure=measures.find(c=>c.index===current.y);
    const y=rowCount?null:selectedMeasure||measures[0];
    const grouping=[...categories,...times];
    const category=grouping.find(c=>c.index===current.x)||categories[0]||times[0];
    const next={...current,type:temporal?'line':choice,transform:temporal?choice:'none',y:y?.index??-1,
      aggregation:!y?'count':selectedMeasure&&aggregations.includes(current.aggregation)?current.aggregation:'sum',x:category?.index??-1,series:-1};
    if(numericOnly&&!y)throw new Error('This chart needs numeric data.');
    if (choice === 'scatter') {
      const other=measures.find(c=>c.index===current.x&&c.index!==y.index)||measures.find(c=>c.index!==y.index);
      if (!other) throw new Error('Scatter needs two numeric statistics.');
      next.x = other.index;
    }
    if (choice === 'histogram') next.x = -1;
    if (choice === 'box') next.x=categories.find(c=>c.index===current.x)?.index??categories[0]?.index??-1;
    if (['line','area','multiline'].includes(choice) && time) next.x = time.index;
    if (temporal) {
      if (!time) throw new Error('This worksheet has no date, year or election-cycle column.');
      next.x = time.index; next.period = /cycle/i.test(time.name) ? 'cycle' : 'year';
      if (choice === 'yoy' && next.period === 'cycle') throw new Error('Year over year needs annual data; these are two-year election cycles.');
    }
    const rows=Array.isArray(data.rows)?filterRows(data,next):null;
    const multipleIndependent=data.independent_series!=null&&(!rows||new Set(rows.map(r=>r[data.independent_series])).size>1);
    if(multipleIndependent){
      const paired={bar:'grouped',horizontal:'groupedhorizontal',line:'multiline'};
      if(!temporal&&paired[next.type])next.type=paired[next.type];
      else if(temporal||!['grouped','groupedhorizontal','multiline'].includes(next.type))throw new Error('Filter to one Source / activity, or use a comparison chart to keep independent funding amounts separate.');
    }
    if(['grouped','groupedhorizontal','multiline'].includes(next.type)){
      let x=grouping.find(c=>c.index===next.x), series=grouping.find(c=>c.index===current.series&&c.index!==x?.index);
      if(multipleIndependent){
        const independent=indexed.find(c=>c.index===data.independent_series);
        if(x?.index!==independent.index)series=independent;
        else series=series||grouping.find(c=>c.index!==independent.index);
      }
      series=series||categories.find(c=>c.index!==x?.index)||times.find(c=>c.index!==x?.index);
      x=x||grouping.find(c=>c.index!==series?.index);
      if(!x||!series||x.index===series.index)throw new Error('This comparison needs two distinct grouping columns, such as recipient and source.');
      next.x=x.index;next.series=series.index;
    }
    if(rows&&['pie','doughnut','pareto','radar'].includes(next.type)){
      if(next.aggregation!=='count'&&rows.some(row=>{const value=numeric(row[next.y]);return value!==null&&value<0;}))throw new Error('This chart requires nonnegative values. Use a bar chart for signed amounts.');
      const usable=rows.filter(row=>next.aggregation==='count'||numeric(row[next.y])!==null);
      if(next.type==='radar'&&new Set(usable.map(row=>next.x<0?'All rows':String(row[next.x]??'(Missing)'))).size<3)throw new Error('Radar charts need at least three groups.');
    }
    return next;
  }

  /* ------------------------------------------------ r3 encodings (W2, W7, W11) */

  // A measure specification {column, aggregation} (W2, W7). Column -1 with
  // COUNT is the row count.
  const specOf = spec => spec && typeof spec === 'object' && Number.isInteger(Number(spec.column))
    ? {column:Number(spec.column), aggregation:spec.aggregation || 'sum'} : null;
  const specLabel = (data, spec) => spec.column < 0 ? 'Row count' :
    (AGG_NAMES[spec.aggregation] || 'SUM') + '(' + (data.columns[spec.column]?.name || 'Field') + ')';
  const measureList = config => Array.isArray(config.measures) && config.measures.length
    ? config.measures.map(specOf).filter(Boolean) : [{column:config.y, aggregation:config.aggregation}];
  const numericColumn = (data, column) => !!data.columns[column] && data.columns[column].type !== 'category' && data.columns[column].type !== 'date';
  // One aggregate of a spec over raw rows: tooltip, size, label and colour
  // fields reuse the measure arithmetic, so they can never disagree with it.
  function aggregateSpec(rows, spec) {
    const column = spec.column, how = spec.aggregation || 'sum';
    if (how === 'count') return column < 0 ? rows.length : rows.filter(row => present(row[column])).length;
    if (how === 'countd') return new Set(rows.filter(row => present(row[column])).map(row => String(row[column]).trim())).size;
    if (how === 'first') { const hit = rows.find(row => present(row[column])); return hit ? String(hit[column]) : null; }
    const values = [];
    for (const row of rows) { const v = numeric(row[column]); if (v !== null) values.push(v); }
    return values.length ? aggregate(values, {aggregation:how}, false) : null;
  }
  // A Label field on a dimension prints its member (the first present value);
  // on a measure it prints the aggregate.
  const labelOf = (data, rows, spec) => spec.aggregation === 'first' || data.columns[spec.column]?.type === 'category'
    ? aggregateSpec(rows, {column:spec.column, aggregation:'first'}) : aggregateSpec(rows, spec);
  function encodings(data, config, ctx) {
    const valid = spec => spec && (spec.column < 0 ? ['count'].includes(spec.aggregation) : !!data.columns[spec.column]);
    const pick = (value, what) => {
      if (value == null) return null;
      const spec = specOf(value);
      if (!valid(spec)) { warn(ctx, what + ' uses a field this worksheet does not have, so it is not drawn.'); return null; }
      if (!aggregations.includes(spec.aggregation) && spec.aggregation !== 'first') throw new Error('Choose an aggregation.');
      return spec;
    };
    const size = pick(config.size, 'Size'), label = pick(config.label, 'Label'), color = pick(config.colorMeasure, 'Colour');
    const tooltip = (Array.isArray(config.tooltip) ? config.tooltip : []).map(t => pick(t, 'A tooltip field')).filter(Boolean);
    if (!size && !label && !color && !tooltip.length) return null;
    return {size, label, color, tooltip, tooltipLabels:tooltip.map(spec => specLabel(data, spec))};
  }
  function encode(target, rows, enc, data) {
    if (!enc) return target;
    if (enc.size) target.size = aggregateSpec(rows, enc.size);
    if (enc.tooltip.length) target.extra = Object.fromEntries(enc.tooltip.map((spec, i) => [enc.tooltipLabels[i], aggregateSpec(rows, spec)]));
    if (enc.label) target.labelValue = labelOf(data, rows, enc.label);
    if (enc.color) target.colorValue = aggregateSpec(rows, enc.color);
    return target;
  }
  function describeEncodings(result, enc, data, marks, ctx) {
    if (!enc) return result;
    if (enc.size) {
      result.sizeLabel = specLabel(data, enc.size);
      if (!SIZED.includes(result.type)) warn(ctx, 'Size is not drawn on ' + result.type + ' marks; its values are in the tooltip and table.');
    }
    if (enc.tooltip.length) result.extraLabels = enc.tooltipLabels.slice();
    if (enc.label) result.labelLabel = enc.label.aggregation === 'first' || data.columns[enc.label.column]?.type === 'category'
      ? (data.columns[enc.label.column]?.name || 'Label') : specLabel(data, enc.label);
    if (enc.color) {
      const values = marks.map(m => m.colorValue).filter(v => typeof v === 'number' && Number.isFinite(v));
      result.colorLabel = specLabel(data, enc.color);
      result.colorRange = values.length ? [values.reduce((a, b) => a < b ? a : b), values.reduce((a, b) => a > b ? a : b)] : null;
    }
    return result;
  }
  // Stacking (W3) and a second value axis (W4) describe a result that already
  // has series; the renderers read them from the result.
  function seriesLayout(result, config, ctx) {
    const stack = config.stack;
    if (stack && stack !== 'none') {
      if (!['stacked','percent'].includes(stack)) warn(ctx, 'Unknown stacking "' + stack + '"; the series are drawn side by side.');
      else if (!result.series || !result.series.length) warn(ctx, 'Stacking needs several series: put a field on Colour or add a second measure.');
      else {
        result.stack = stack;
        if (stack === 'percent') {
          for (const group of result.groups) {
            const total = group.values.reduce((sum, v) => sum + (v.value > 0 ? v.value : 0), 0);
            for (const v of group.values) if (v.value != null) v.share = total ? v.value / total : null;
          }
          result.units = 'percent';
        }
      }
    }
    if (config.dualAxis) {
      const n = result.series ? result.series.length : 0;
      if (n < 2) warn(ctx, 'A second value axis needs two series: add a second measure or put a field on Colour.');
      else {
        const chosen = Array.isArray(config.dualRight) ? [...new Set(config.dualRight)].filter(i => Number.isInteger(i) && i >= 0 && i < n) : [];
        let right = chosen.length ? chosen.sort((a, b) => a - b) : Array.from({length:n - 1}, (_, i) => i + 1);
        if (right.length === n) right = right.filter(i => i !== 0);
        const left = Array.from({length:n}, (_, i) => i).filter(i => !right.includes(i));
        result.dual = {left, right, mark:config.dualMark === 'bar' ? 'bar' : 'line'};
      }
    }
    return result;
  }
  const emptyResult = (type, selected, excluded) => type === 'scatter'
    ? {type, points:[], selected, excluded} : {type, groups:[], selected, excluded};
  // Normalises one r3 configuration. A legacy configuration (no lenient flag and
  // no measures list) is returned untouched, so its build is today's.
  function prepare(data, config, ctx) {
    const lenient = ctx.lenient;
    const listed = Array.isArray(config.measures) && config.measures.length > 0;
    if (!lenient && !listed) return config;
    const c = Object.assign({}, config);
    const valid = column => Number.isInteger(column) && !!data.columns[column];
    if (lenient) {
      if (Array.isArray(c.dimensions)) {
        const unique = [...new Set(c.dimensions)], kept = unique.filter(valid);
        if (kept.length !== unique.length) warn(ctx, 'A grouping field this worksheet does not have was left out.');
        c.dimensions = kept;
      }
      if (c.x != null && c.x >= 0 && !valid(c.x)) { warn(ctx, 'The axis field is not in this worksheet, so it was left out.'); c.x = -1; }
      if (c.series != null && c.series >= 0 && !valid(c.series)) { warn(ctx, 'The Colour field is not in this worksheet, so it was left out.'); c.series = -1; }
    }
    let measures = listed ? c.measures.map(specOf).filter(Boolean) : [];
    for (const m of measures) if (!aggregations.includes(m.aggregation)) throw new Error('Choose an aggregation.');
    if (listed) {
      const usable = measures.filter(m => m.column < 0 ? m.aggregation === 'count' : valid(m.column));
      if (usable.length !== measures.length) {
        if (!lenient) throw new Error('Choose a numeric measure.');
        warn(ctx, 'A measure field this worksheet does not have was left out.');
      }
      measures = usable;
      if (measures.length) { c.y = measures[0].column; c.aggregation = measures[0].aggregation; }
      c.measures = measures;
    }
    if (lenient) {
      if (!valid(c.y) && !(c.aggregation === 'count' && (c.y == null || c.y < 0))) {
        if (c.y != null && c.y >= 0) warn(ctx, 'The measure field is not in this worksheet, so the marks count rows.');
        c.y = -1; c.aggregation = 'count';
        if (listed) c.measures = measures = [{column:-1, aggregation:'count'}];
      }
      if (!aggregations.includes(c.aggregation)) throw new Error('Choose an aggregation.');
      // A scatter needs two numeric fields; with fewer it becomes a dot plot
      // of what is there.
      if (c.type === 'scatter' && measures.length < 2) {
        const xOk = valid(c.x) && numericColumn(data, c.x), yOk = valid(c.y) && numericColumn(data, c.y);
        if (!xOk || !yOk) {
          warn(ctx, 'A scatter needs two numeric measures; this view is drawn as a dot plot.');
          const category = valid(c.x) && !numericColumn(data, c.x) ? c.x : -1;
          if (!yOk && xOk) { c.y = c.x; }
          else if (!yOk) { c.y = -1; c.aggregation = 'count'; }
          if (c.aggregation === 'count' && c.y >= 0) c.aggregation = 'sum';
          c.type = 'dot'; c.x = category;
          if (category >= 0 && Array.isArray(c.dimensions) && !c.dimensions.includes(category)) c.dimensions = [category, ...c.dimensions];
          if (listed) c.measures = measures = [{column:c.y, aggregation:c.aggregation}];
        }
      }
      if (['box','histogram'].includes(c.type) && !(valid(c.y) && numericColumn(data, c.y))) {
        warn(ctx, 'A ' + (c.type === 'box' ? 'box plot' : 'histogram') + ' needs a numeric measure; the marks count rows instead.');
        c.type = 'bar'; c.y = -1; c.aggregation = 'count';
        if (c.x < 0 && Array.isArray(c.dimensions) && c.dimensions.length) c.x = c.dimensions[0];
        if (listed) c.measures = measures = [{column:-1, aggregation:'count'}];
      }
      // Colour on every mark type (W11). Colour that repeats the axis field adds
      // nothing: every mark already takes its own colour.
      if (c.series != null && c.series >= 0) {
        const grouping = dimensions(c);
        if (c.type !== 'scatter' && c.type !== 'histogram' && grouping.includes(c.series)) {
          c.series = -1;
          if (BASE_OF[c.type] && measures.length < 2) c.type = BASE_OF[c.type];
        } else if (SERIES_OF[c.type]) c.type = SERIES_OF[c.type];
        else if (TUPLE_COLOUR.includes(c.type)) {
          c.seriesColumn = c.series;
          c.dimensions = [...grouping, c.series];
          if (c.x < 0) c.x = c.dimensions[0];
          c.series = -1;
        }
      }
      if (MULTI_TYPES.includes(c.type) && !valid(c.series) && measures.length < 2) c.type = BASE_OF[c.type];
      // Time analysis needs a time field and a mark that has periods to show.
      if (c.transform && c.transform !== 'none' && !['annual','yoy','change','forecast'].includes(c.transform)) {
        warn(ctx, 'Unknown time analysis "' + c.transform + '"; the view is drawn without it.'); c.transform = 'none';
      }
      if (c.transform && c.transform !== 'none') {
        if (!valid(c.x)) { warn(ctx, 'Time analysis needs a date, year or election-cycle field on the axis; the view is drawn without it.'); c.transform = 'none'; }
        else if (['scatter','box','histogram','gantt'].includes(c.type)) { warn(ctx, 'Time analysis is not applied to ' + c.type + ' marks.'); c.transform = 'none'; }
      }
    }
    // Several measures (W2).
    if (measures.length >= 2) {
      const names = list => list.map(m => specLabel(data, m)).join(', ');
      const extras = list => { c.tooltip = [...(Array.isArray(c.tooltip) ? c.tooltip : []), ...list]; };
      if (SERIES_OF[c.type] || MULTI_TYPES.includes(c.type)) c.type = SERIES_OF[c.type] || c.type;
      else if (c.type === 'scatter') {
        c.x = measures[0].column; c.y = measures[1].column; c.aggregation = measures[1].aggregation;
        if (c.xAggregation == null) c.xAggregation = measures[0].aggregation;
        let rest = measures.slice(2);
        if (!c.size && rest.length) { c.size = rest[0]; rest = rest.slice(1); }
        if (rest.length) extras(rest);
      } else if (c.type === 'bullet') {
        if (measures.length > 2) { extras(measures.slice(2)); warn(ctx, 'A bullet compares two measures; ' + names(measures.slice(2)) + ' are in the tooltip.'); }
        c.measures = measures.slice(0, 2);
      } else {
        extras(measures.slice(1));
        warn(ctx, 'A ' + c.type + ' view shows one measure; ' + names(measures.slice(1)) + ' are in the tooltip.');
        c.measures = [measures[0]];
      }
    }
    return c;
  }
  // The per-group arithmetic (W9): scatter points, boxes and histogram bins
  // over aggregates of the Detail groups instead of individual rows.
  function grainDimensions(config) {
    const listed = Array.isArray(config.dimensions) ? config.dimensions : [];
    if (config.type === 'scatter') return listed.filter(column => column !== config.x && column !== config.y);
    if (config.type === 'box') return [...new Set([...(config.x >= 0 ? [config.x] : []), ...listed])];
    return listed.filter(column => column !== config.y);
  }
  function histogramBins(values, config, ctx) {
    let min = Infinity, max = -Infinity;
    for (const v of values) { if (v < min) min = v; if (v > max) max = v; }
    let n, width, start = min;
    const bins = Number(config.bins), binWidth = Number(config.binWidth);
    if (min === max) { n = 1; width = 1; }
    else if (config.binWidth != null && Number.isFinite(binWidth) && binWidth > 0) {
      start = Math.floor(min / binWidth) * binWidth; width = binWidth;
      n = Math.max(1, Math.floor((max - start) / width) + 1);
      if (n > 1000) { warn(ctx, 'That bin width makes more than 1,000 bins, so 1,000 equal bins are drawn.'); n = 1000; start = min; width = (max - min) / n; }
    } else if (config.bins != null && Number.isInteger(bins) && bins >= 1) {
      n = Math.min(1000, bins); width = (max - min) / n;
      if (bins > 1000) warn(ctx, 'At most 1,000 bins are drawn.');
    } else {
      if (config.bins != null || config.binWidth != null) warn(ctx, 'The bin setting was not a positive number, so the standard bins are used.');
      n = Math.min(30, Math.ceil(Math.sqrt(values.length))); width = (max - min) / n;
    }
    const edges = Array.from({length:n}, (_, i) => ({label:`${(start + width*i).toPrecision(5)} – ${(start + width*(i+1)).toPrecision(5)}`, value:0}));
    return {bins:edges, index:v => Math.max(0, Math.min(n-1, Math.floor((v-start)/width)))};
  }
  function grainBuild(data, source, rows, config, ctx, enc) {
    const detail = grainDimensions(config), buckets = new Map();
    const keyOf = row => groupName(row, {dimensions:detail});
    for (const row of rows) { const key = keyOf(row); if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(row); }
    const ySpec = {column:config.y, aggregation:config.aggregation};
    let excluded = 0;
    const grained = {type:config.type, selected:rows.length};
    if (config.type === 'scatter') {
      const xSpec = {column:config.x, aggregation:config.xAggregation || config.aggregation};
      const points = [];
      for (const [label, members] of buckets) {
        const x = aggregateSpec(members, xSpec), y = aggregateSpec(members, ySpec);
        if (typeof x !== 'number' || typeof y !== 'number') { excluded += members.length; continue; }
        points.push(encode({x, y, label}, members, enc, data));
      }
      return describeEncodings(Object.assign(grained, {points, excluded, grain:'group'}), enc, data, points, ctx);
    }
    const values = [];
    for (const [label, members] of buckets) {
      const v = aggregateSpec(members, ySpec);
      if (typeof v !== 'number') { excluded += members.length; continue; }
      values.push({label, value:v, rows:members});
    }
    if (!values.length) { warn(ctx, 'No numeric values remain. Missing values are not treated as zero.'); return Object.assign(grained, {groups:[], excluded, grain:'group'}); }
    // Colour on a group-grain histogram or box (census B-S07, C-04 with W11): the
    // Colour field is one of the detail fields, so each group has one member, and
    // the bins stack by member / the boxes split by member.
    const colourColumn = config.type === 'histogram' ? config.series : config.seriesColumn;
    const coloured = ctx.lenient && Number.isInteger(colourColumn) && colourColumn >= 0 && !!data.columns[colourColumn];
    const memberOf = v => v.rows[0][colourColumn] == null ? '(Missing)' : String(v.rows[0][colourColumn]);
    if (config.type === 'histogram') {
      const {bins, index} = histogramBins(values.map(v => v.value), config, ctx);
      if (coloured) {
        const names = [...new Set(values.map(memberOf))];
        bins.forEach(bin => { bin.values = names.map(name => ({series:name, value:0, n:0})); });
        for (const v of values) { const bin = bins[index(v.value)], slot = bin.values[names.indexOf(memberOf(v))]; bin.value++; slot.value++; slot.n++; }
        Object.assign(grained, {groups:bins, excluded, grain:'group', series:names});
        seriesLayout(grained, config, ctx);
        return grained;
      }
      for (const v of values) bins[index(v.value)].value++;
      return Object.assign(grained, {groups:bins, excluded, grain:'group'});
    }
    // Box: one box per axis member (and Colour member) over the aggregates of its detail groups.
    const axis = config.x >= 0 ? config.x : -1, boxes = new Map(), names = [];
    for (const v of values) {
      const at = axis < 0 ? 'All rows' : v.rows[0][axis] == null ? '(Missing)' : String(v.rows[0][axis]);
      const member = coloured ? JSON.stringify([at, memberOf(v)]) : at;
      if (coloured && !names.includes(memberOf(v))) names.push(memberOf(v));
      if (!boxes.has(member)) boxes.set(member, {values:[], rows:[], colour:coloured ? memberOf(v) : null});
      boxes.get(member).values.push(v.value); boxes.get(member).rows.push(...v.rows);
    }
    const groups = [...boxes].map(([label, b]) => {
      const group = encode({label, ...box(b.values)}, b.rows, enc, data);
      if (coloured) group.seriesIndex = names.indexOf(b.colour);
      return group;
    });
    if (coloured) { grained.series = names; grained.colorField = data.columns[colourColumn].name || 'Colour'; }
    return describeEncodings(Object.assign(grained, {groups, excluded, grain:'group'}), enc, data, groups, ctx);
  }
  // A worksheet date as an ISO day, for Gantt spans.
  function isoDay(value) {
    const text = String(value ?? '').trim(), match = text.match(/^(\d{4}-\d{2}-\d{2})/);
    if (!match) return null;
    const day = new Date(match[1] + 'T00:00:00Z');
    return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === match[1] ? match[1] : null;
  }

  function build(data, config) {
    return buildWith(data, config, context(config));
  }
  function buildWith(data, config, ctx) {
    const lenient = ctx.lenient;
    config = prepare(data, config, ctx);
    for (const column of dimensions(config)) if (!Number.isInteger(column) || !data.columns[column]) throw new Error('Choose valid grouping fields.');
    validateGrain(data, config, filterRows(data, config, ctx), ctx);
    if (lenient && Array.isArray(config.measures)) for (const m of config.measures.slice(1)) validateGrain(data, {...config, y:m.column, aggregation:m.aggregation}, filterRows(data, config, ctx), ctx);
    if (config.splitMulti && config.aggregation !== 'count' && config.aggregation !== 'countd') {
      if (!lenient) throw new Error('Split multi-value cells is coverage-only. Choose Count, or use transaction-level ZIP data for money.');
      if (!INDIVIDUAL.includes(config.type)) refuse(ctx, SPLIT_REFUSAL);
    }
    if (config.splitMulti && ['scatter','box','histogram'].includes(config.type)) {
      if (!lenient) throw new Error('Split multi-value cells is available only for grouped coverage counts.');
      config = Object.assign({}, config, {splitMulti:false}); // nothing groups these marks, so there is nothing to split
    }
    if (MULTI_TYPES.includes(config.type) || config.type === 'bullet' && Array.isArray(config.measures) && config.measures.length >= 2) {
      const series = grouped(data, config, ctx);
      if (config.type === 'bullet') series.groupColumns = dimensions(config);
      return finish(describeResult(series, data, config, ctx), ctx);
    }
    const source = explodeRows(data, config, ctx);
    if (source.independent_series != null && !dimensions(config).includes(source.independent_series) && new Set(filterRows(source,config,ctx).map(r=>r[source.independent_series])).size > 1) {
      if (!lenient) throw new Error('Use side-by-side bars, horizontal comparison or separate lines to keep funding sources and activities separate.');
      warn(ctx, 'Funding sources and activities are combined in these marks, and they can overlap; put Source / activity on Colour to keep them apart.');
    }
    if (config.transform && config.transform !== 'none') return finish(describeResult(temporal(data, config, ctx), data, config, ctx), ctx);
    if (!SINGLE_TYPES.includes(config.type)) throw new Error('Choose a chart type.');
    if (!aggregations.includes(config.aggregation)) throw new Error('Choose an aggregation.');
    const rows = filterRows(source, config, ctx), points = [], groups = new Map();
    if (!rows.length) {
      if (!lenient) throw new Error('No rows match this subsection.');
      warn(ctx, 'No rows match this subsection.');
      return finish(describeResult(emptyResult(config.type, 0, 0), data, config, ctx), ctx);
    }
    const individual = ['box','scatter','histogram'].includes(config.type);
    const count = config.aggregation === 'count' && !individual;
    const distinct = config.aggregation === 'countd' && !individual;
    if (count && config.y >= 0 && !data.columns[config.y]) throw new Error('Choose a valid field to count.');
    if (!count && !data.columns[config.y]) throw new Error('Choose a numeric measure.');
    if (config.type === 'scatter' && !data.columns[config.x]) throw new Error('Choose an X measure.');
    const enc = encodings(data, config, ctx);
    if (config.grain === 'group' && individual && grainDimensions(config).length) {
      return finish(describeResult(grainBuild(data, source, rows, config, ctx, enc), data, config, ctx), ctx);
    }
    if (config.grain != null && config.grain !== 'row' && config.grain !== 'group') warn(ctx, 'Unknown grain "' + config.grain + '"; individual rows are used.');
    // Rows per group, kept only when an r3 encoding, a Gantt span or a
    // coloured histogram / scatter needs the raw rows behind a mark.
    const seriesColumn = lenient && ['scatter','histogram'].includes(config.type) && data.columns[config.series] ? config.series : -1;
    const keepRows = !!enc || config.type === 'gantt' || seriesColumn >= 0;
    const members = keepRows ? new Map() : null, pairs = keepRows && config.type === 'histogram' ? [] : null;
    const seriesNames = seriesColumn >= 0 ? new Set() : null;
    const memberOf = row => row[seriesColumn] == null ? '(Missing)' : String(row[seriesColumn]);
    let excluded = 0, excludedNegative = 0;
    for (const row of rows) {
      const group = config.type === 'histogram' ? 'All rows' : groupName(row, config);
      if ((count || distinct || config.type === 'gantt') && !groups.has(group)) groups.set(group, []);
      if (members) { if (!members.has(group)) members.set(group, []); members.get(group).push(row); }
      const y = count || distinct ? observation(row, config) : numeric(row[config.y]);
      if (y === null) { excluded++; continue; }
      if (!distinct && ['pie','doughnut','pareto','radar'].includes(config.type) && y < 0) {
        if (!lenient) throw new Error('This chart requires nonnegative values. Use a bar chart for signed amounts.');
        excludedNegative++; continue;
      }
      if (config.type === 'scatter') {
        const x = numeric(row[config.x]);
        if (x === null) { excluded++; continue; }
        const point = {x, y};
        if (lenient && Array.isArray(config.dimensions) && grainDimensions(config).length) point.label = groupName(row, {dimensions:grainDimensions(config)});
        if (seriesNames) { point.series = memberOf(row); seriesNames.add(point.series); }
        points.push(enc ? encode(point, [row], enc, data) : point); continue;
      }
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(y);
      if (pairs) pairs.push([y, row]);
    }
    if (!count && !distinct && config.type !== 'gantt' && excluded === rows.length) {
      if (!lenient) throw new Error('No numeric values remain. Missing values are not treated as zero.');
      warn(ctx, 'No numeric values remain. Missing values are not treated as zero.');
      return finish(describeResult(emptyResult(config.type, rows.length, excluded), data, config, ctx), ctx);
    }
    if (excludedNegative) warn(ctx, excludedNegative + (excludedNegative === 1 ? ' negative value is' : ' negative values are') + ' left out: shares need nonnegative values. A bar chart shows signed amounts.');
    if (config.type === 'scatter') {
      const scatter = {type:config.type, points, selected:rows.length, excluded};
      if (seriesNames) scatter.series = [...seriesNames];
      describeEncodings(scatter, enc, data, points, ctx);
      return finish(describeResult(scatter, data, config, ctx), ctx);
    }
    if (config.type === 'histogram') {
      const values = groups.get('All rows');
      if (!values || !values.length) {
        warn(ctx, 'No numeric values remain. Missing values are not treated as zero.');
        return finish(describeResult(emptyResult(config.type, rows.length, excluded), data, config, ctx), ctx);
      }
      if (config.bins == null && config.binWidth == null && !pairs) {
        const min = Math.min(...values), max = Math.max(...values);
        const n = min === max ? 1 : Math.min(30, Math.ceil(Math.sqrt(values.length)));
        const width = min === max ? 1 : (max - min) / n;
        const bins = Array.from({length:n}, (_, i) => ({label:`${(min + width*i).toPrecision(5)} – ${(min + width*(i+1)).toPrecision(5)}`, value:0}));
        for (const v of values) bins[Math.min(n-1, Math.floor((v-min)/width))].value++;
        return finish(describeResult({type:config.type, groups:bins, selected:rows.length, excluded}, data, config, ctx), ctx);
      }
      const {bins, index} = histogramBins(values, config, ctx);
      const binRows = pairs ? bins.map(() => []) : null;
      if (pairs) for (const [v, row] of pairs) binRows[index(v)].push(row);
      else for (const v of values) bins[index(v)].value++;
      const histogram = {type:config.type, groups:bins, selected:rows.length, excluded};
      if (pairs) {
        bins.forEach((bin, i) => { bin.value = binRows[i].length; });
        if (seriesNames) {
          for (const row of rows) if (numeric(row[config.y]) !== null) seriesNames.add(memberOf(row));
          const names = [...seriesNames];
          bins.forEach((bin, i) => {
            bin.values = names.map(name => { const n = binRows[i].filter(row => memberOf(row) === name).length; return {series:name, value:n, n}; });
          });
          histogram.series = names;
          seriesLayout(histogram, config, ctx);
        }
        if (enc) bins.forEach((bin, i) => encode(bin, binRows[i], enc, data));
        describeEncodings(histogram, enc, data, bins, ctx);
      }
      return finish(describeResult(histogram, data, config, ctx), ctx);
    }
    const limit = groupLimit(config, ctx);
    let entries = [...groups], rolled = null, otherRows = null;
    if (entries.length > limit) {
      validateRollup(source, config, ctx);
      // Rank by the number the mark itself would show, so the fold keeps the
      // groups the view is about. Selection is by size; the axis still lists the
      // survivors in the order the rows arrived, which is what a waterfall means.
      const key = new Map(entries.map(([label, values]) => [label,
        config.type === 'box' ? quantile(values.slice().sort((a,b)=>a-b), .5) : aggregate(values, config, count)]));
      const kept = new Set(entries.slice().sort((a, b) => key.get(b[0]) - key.get(a[0]) ||
        a[0].localeCompare(b[0], undefined, {numeric:true})).slice(0, limit).map(([label]) => label));
      const rest = entries.filter(([label]) => !kept.has(label)), pooled = rest.flatMap(([, values]) => values);
      if (members) otherRows = rest.flatMap(([label]) => members.get(label) || []);
      entries = entries.filter(([label]) => kept.has(label));
      rolled = Object.assign({label:otherLabel(rest.length)},
        config.type === 'box' ? box(pooled) : {value:aggregate(pooled, config, count), n:pooled.length},
        {other:true, folded:rest.length});
    }
    let output = entries.map(([label, values]) => config.type === 'box' ? {label, ...box(values)} :
      {label, value:aggregate(values, config, count), n:values.length});
    if (['line','area'].includes(config.type)) output.sort((a,b) => a.label.localeCompare(b.label, undefined, {numeric:true}));
    if (config.type==='pareto')output.sort((a,b)=>b.value-a.value);
    if (rolled) output.push(rolled); // Other is the tail of the view, never a member sorted into it.
    if (config.type === 'box' && lenient && output.some(g => !g.n)) output = output.filter(g => g.n);
    if (config.type === 'gantt') {
      // A Gantt bar spans the first to the last date of its group (W6).
      const dateColumn = Number(config.dateColumn);
      if (!Number.isInteger(dateColumn) || !data.columns[dateColumn]) {
        if (!lenient) throw new Error('Choose a date field for the Gantt bars.');
        warn(ctx, 'A Gantt view needs a date field; the groups are drawn as bars of row counts.');
        output.forEach(g => { g.value = g.n; });
        const bars = {type:'horizontal', groups:output, selected:rows.length, excluded};
        return finish(describeResult(bars, data, config, ctx), ctx);
      }
      let undated = 0;
      output = output.flatMap(g => {
        const days = (g.other ? otherRows : members.get(g.label) || []).map(row => isoDay(row[dateColumn])).filter(Boolean).sort();
        if (!days.length) { undated++; return []; }
        const first = days[0], last = days.at(-1);
        return [Object.assign(g, {value:(Date.parse(last) - Date.parse(first)) / 86400000, first, last, span:(Date.parse(last) - Date.parse(first)) / 86400000})];
      });
      if (undated) warn(ctx, undated + (undated === 1 ? ' group has' : ' groups have') + ' no valid dates and ' + (undated === 1 ? 'is' : 'are') + ' not drawn.');
    }
    if(config.type==='radar'&&output.length<3){
      if (!lenient) throw new Error('Radar charts need at least three groups.');
      warn(ctx, 'Radar charts read best with at least three groups; this one has ' + output.length + '.');
    }
    if (['pie','doughnut','pareto','radar'].includes(config.type) && !output.some(g => g.value > 0)) {
      if (!lenient) throw new Error('This chart needs a positive total.');
      warn(ctx, 'This chart needs a positive total; there is nothing to divide.');
      output = [];
    }
    const result = {type:config.type, groups:output, selected:rows.length, excluded};
    const notes = [];
    // selected counts exploded rows, so it can exceed the worksheet. The caller
    // needs the split facts to say "values from rows" rather than "7333 of 4500".
    if (source.split) { result.split = source.split; notes.push(splitNote(source.split)); }
    if (rolled) notes.push(rollupNote(limit, groups.size, rolled.folded, count ? 'row count' : config.aggregation === 'countd' ? 'distinct count' : config.aggregation));
    if (notes.length) result.note = notes.join(' ');
    if (excludedNegative) result.excludedNegative = excludedNegative;
    if (lenient && config.type === 'pareto') result.paretoOrder = output.map(g => g.label);
    // Colour joined into the grouping tuple (W11): each mark carries its member.
    if (Number.isInteger(config.seriesColumn) && config.seriesColumn >= 0) {
      const tuple = dimensions(config), at = tuple.indexOf(config.seriesColumn);
      const memberOfLabel = label => { if (tuple.length === 1) return label; try { const parts = JSON.parse(label); return parts[at] == null ? '(Missing)' : parts[at]; } catch (e) { return null; } };
      const names = [];
      for (const g of output) { if (g.other) continue; const m = memberOfLabel(g.label); if (m == null) continue; if (!names.includes(m)) names.push(m); g.seriesIndex = names.indexOf(m); }
      if (config.type === 'box') result.series = names;
      result.colorField = data.columns[config.seriesColumn]?.name || 'Colour';
    }
    if (enc) for (const g of output) encode(g, g.other ? otherRows || [] : members.get(g.label) || [], enc, data);
    describeEncodings(result, enc, data, output, ctx);
    if (config.stack && config.stack !== 'none' || config.dualAxis) seriesLayout(result, config, ctx);
    // The grouping fields behind each tuple label, for the delegated drawings (W6).
    if (['heatmap','highlight','treemap','bubbles','bullet','gantt'].includes(config.type)) result.groupColumns = dimensions(config);
    return finish(describeResult(result, data, config, ctx), ctx);
  }
  function grouped(data, config, ctx) {
    const lenient = !!(ctx && ctx.lenient);
    if (config.transform && config.transform !== 'none') {
      if (!lenient) throw new Error('Choose None for time analysis to compare separate series.');
      return temporal(data, config, ctx);
    }
    const measures = measureList(config);
    const byMeasures = Array.isArray(config.measures) && measures.length >= 2;
    const hasSeries = !!data.columns[config.series];
    // Lenient: a series view with nothing on the axis is one group, All rows.
    if (!byMeasures && (!data.columns[config.x] && !lenient || !data.columns[config.series])) throw new Error('Choose a grouping column and a separate series.');
    const source=explodeRows(data,config,ctx);
    if(source.independent_series!=null && !dimensions(config).includes(source.independent_series) && config.series!==source.independent_series && new Set(filterRows(source,config,ctx).map(r=>r[source.independent_series])).size>1){
      if (!lenient) throw new Error('Keep Source / activity as the grouping or separate series so independent amounts are not combined.');
      warn(ctx, 'Source / activity is combined inside these series, and its amounts can overlap; put it on Colour or the axis to keep it apart.');
    }
    for (const m of measures) if (!['sum','mean','count','median','min','max','countd'].includes(m.aggregation)) throw new Error('Choose an aggregation.');
    // Two identical measure pills stay two series ("SUM(Amount) (2)").
    const labels = byMeasures ? measures.map((m, k, all) => {
      const base = specLabel(data, m), repeat = all.slice(0, k).filter(o => specLabel(data, o) === base).length;
      return repeat ? base + ' (' + (repeat + 1) + ')' : base;
    }) : null;
    const order=source.independent_series==null || config.series===source.independent_series ? source.series_order||[] : [];
    // A series name is the Colour member, the measure (several measures and no
    // Colour), or both joined with " · " (W2).
    const nameOf = (member, k) => !byMeasures ? member : hasSeries ? member + ' · ' + labels[k] : labels[k];
    const specByName = new Map(), initial = [];
    const seed = (name, m) => { initial.push(name); if (!specByName.has(name)) specByName.set(name, m); };
    if (!byMeasures) for (const member of order) seed(member, measures[0]);
    else if (hasSeries) for (const member of order) measures.forEach((m, k) => seed(nameOf(member, k), m));
    else measures.forEach((m, k) => seed(labels[k], m));
    const rows=filterRows(source,config,ctx), groups=new Map(), series=new Set(initial);let excluded=0;
    const enc = encodings(data, config, ctx), cells = enc ? new Map() : null;
    for(const row of rows) {
      const label=groupName(row,config), member=hasSeries ? String(row[config.series]??'(Missing)') : null;
      if(!groups.has(label))groups.set(label,new Map());
      let found=0;
      for (let k = 0; k < measures.length; k++) {
        const m = measures[k], name = nameOf(member, k);
        series.add(name);
        if (!specByName.has(name)) specByName.set(name, m);
        if((m.aggregation==='count'||m.aggregation==='countd')&&!groups.get(label).has(name))groups.get(label).set(name,[]);
        if (cells) { if (!cells.has(label)) cells.set(label, new Map()); const byName = cells.get(label); if (!byName.has(name)) byName.set(name, []); byName.get(name).push(row); }
        const value=observation(row,{y:m.column,aggregation:m.aggregation});
        if(value===null)continue;
        found++;
        if(!groups.get(label).has(name))groups.get(label).set(name,[]);
        groups.get(label).get(name).push(value);
      }
      if(!found)excluded++;
    }
    if(!rows.length){
      if (!lenient) throw new Error('No rows match this subsection.');
      warn(ctx, 'No rows match this subsection.');
      return seriesLayout({type:config.type,groups:[],series:[...series],selected:0,excluded:0}, config, ctx);
    }
    const limit=groupLimit(config,ctx), specOfName=name=>specByName.get(name)||measures[0], countMode=name=>specOfName(name).aggregation==='count';
    const ranking=name=>specOfName(name)===measures[0];
    let entries=[...groups], rolled=null;
    if(entries.length>limit){
      validateRollup(source, config, ctx);
      // A label is ranked by everything drawn under it, across every series
      // of the first measure (several measures do not share one unit).
      const key=new Map(entries.map(([label,byName])=>[label,aggregate([...byName].filter(([name])=>ranking(name)).flatMap(([,values])=>values),measures[0],measures[0].aggregation==='count')]));
      const kept=new Set(entries.slice().sort((a,b)=>key.get(b[0])-key.get(a[0])||
        a[0].localeCompare(b[0],undefined,{numeric:true})).slice(0,limit).map(([label])=>label));
      const rest=entries.filter(([label])=>!kept.has(label)), pooled=new Map(), pooledRows=cells?new Map():null;
      entries=entries.filter(([label])=>kept.has(label));
      for(const [label,byName] of rest){
        for(const [name,values] of byName){
          if(!pooled.has(name))pooled.set(name,[]);
          const into=pooled.get(name);for(const v of values)into.push(v); // push(...values) overflows on a large fold.
        }
        if(pooledRows)for(const [name,list] of cells.get(label)||[]){if(!pooledRows.has(name))pooledRows.set(name,[]);const into=pooledRows.get(name);for(const r of list)into.push(r);}
      }
      rolled={label:otherLabel(rest.length),members:pooled,folded:rest.length,rows:pooledRows};
    }
    const mark=(label,byName,rowsByName)=>({label,values:[...series].map(name=>{
      const a=byName.get(name),v={series:name,value:!a?null:aggregate(a,specOfName(name),countMode(name)),n:a?.length||0};
      return rowsByName?encode(v,rowsByName.get(name)||[],enc,data):v;
    })});
    const output=entries.map(([label,byName])=>mark(label,byName,cells?cells.get(label)||new Map():null));
    if(rolled)output.push(Object.assign(mark(rolled.label,rolled.members,rolled.rows),{other:true,folded:rolled.folded}));
    let note='Series are independent; no combined total. Gaps mean unavailable observations, not zero.';
    if(source.split)note+=' '+splitNote(source.split);
    if(rolled)note+=' '+rollupNote(limit,groups.size,rolled.folded,measures[0].aggregation==='count'?'row count':measures[0].aggregation==='countd'?'distinct count':measures[0].aggregation);
    const result={type:config.type,groups:output,series:[...series],selected:rows.length,excluded,note};
    if(source.split)result.split=source.split; // selected counts exploded rows; the caller says so.
    if(byMeasures){
      if(!hasSeries)result.measureSeries=true;
      result.seriesConfig=result.series.map(name=>{const m=specOfName(name);return {column:m.column,aggregation:m.aggregation};});
    }
    if(enc){describeEncodings(result,enc,data,[],ctx);if(enc.color)warn(ctx,'A measure on Colour is not drawn on series marks; its values are in the tooltip.');}
    return seriesLayout(result,config,ctx);
  }
  function temporal(data, config, ctx) {
    if (ctx ? ctx.lenient : config.lenient) return temporalFlexible(data, config, ctx || context(config));
    validateGrain(data, config, filterRows(data, config));
    if (config.splitMulti) throw new Error('Time analysis does not split multi-value cells. Use a coverage-count chart or transaction-level dated facts.');
    if (dimensions(config).some(column => column !== config.x)) throw new Error('Time analysis needs one time grouping; filter other dimensions or use separate ordinary series.');
    if(!['annual','yoy','change','forecast'].includes(config.transform))throw new Error('Choose a time analysis.');
    if(!['bar','line','area'].includes(config.type))throw new Error('Time analysis supports bar, line and area charts.');
    const column=data.columns[config.x];
    if(!column)throw new Error('Choose a date or year column.');
    const cycleInput=/cycle/i.test(column.name), bucketCalendarYears=config.period==='cycle'&&!cycleInput;
    const bucketYear=year=>bucketCalendarYears?year+year%2:year;
    if(cycleInput&&config.period!=='cycle')throw new Error('Election cycles are two-year periods. Choose Election cycle instead of Calendar year.');
    if(config.period==='cycle'&&config.transform==='yoy')throw new Error('Year-over-year requires calendar-year data. Use Period change for election cycles.');
    if(!aggregations.includes(config.aggregation)||config.aggregation==='countd')throw new Error('Choose an aggregation.');
    const rows=filterRows(data,config), byYear=new Map(), incompleteYears=new Set(), sourceYears=new Map();let excluded=0;
    const subjectColumn=data.columns.find(c=>c.name==='Subject');
    if(subjectColumn&&new Set(rows.map(r=>r[subjectColumn.index])).size>1)throw new Error('Filter to one Subject for time analysis; overlapping subjects cannot be added together.');
    for(const row of rows) {
      const raw=String(row[config.x]??'').trim();
      const match=cycleInput?raw.match(/^(?:(\d{4})[-–])?(\d{4})$/):raw.match(/^(\d{4})(?:-\d{2}-\d{2}(?:T.*)?)?$/);
      const value=observation(row,config);
      if(!match){excluded++;continue;}
      const sourceYear=Number(match[cycleInput?2:1]), year=bucketYear(sourceYear);
      if(sourceYear<1800||year>2200||cycleInput&&(sourceYear%2!==0||match[1]&&Number(match[1])!==sourceYear-1)){excluded++;continue;}
      if(!cycleInput&&raw.length>4){
        const calendarDate=new Date(raw.slice(0,10)+'T00:00:00Z'), timestamp=new Date(raw);
        if(!Number.isFinite(calendarDate.getTime())||calendarDate.toISOString().slice(0,10)!==raw.slice(0,10)||!Number.isFinite(timestamp.getTime())){incompleteYears.add(year);excluded++;continue;}
      }
      if(!sourceYears.has(year))sourceYears.set(year,new Set());sourceYears.get(year).add(sourceYear);
      if(config.aggregation==='count'&&!byYear.has(year))byYear.set(year,[]);
      if(value===null){if(config.aggregation!=='count')incompleteYears.add(year);excluded++;continue;}
      if(!byYear.has(year))byYear.set(year,[]);byYear.get(year).push(value);
    }
    const groups=[...byYear].sort((a,b)=>a[0]-b[0]).map(([year,values])=>({label:String(year),year,n:values.length,value:config.aggregation==='count'?values.length:config.aggregation==='median'?quantile(values.slice().sort((a,b)=>a-b),.5):config.aggregation==='min'?Math.min(...values):config.aggregation==='max'?Math.max(...values):values.reduce((a,b)=>a+b,0)/(config.aggregation==='mean'?values.length:1)}));
    if(!groups.length)throw new Error('No valid dated numeric observations remain.');
    const step=config.period==='cycle'?2:1;
    const statistic=config.aggregation==='sum'?'totals':config.aggregation==='count'?'row counts':config.aggregation+' values';
    let output=groups, note=config.period==='cycle'?`Over time shows ${statistic} for each two-year election-cycle reporting period.`:`Over time shows calendar-year ${statistic}; partial years are not annualized.`;
    if(['yoy','change'].includes(config.transform)) {
      output=groups.flatMap(g=>{
        const prior=groups.find(p=>p.year===g.year-step);
        if(!prior||config.transform==='yoy'&&prior.value<=0)return [];
        return [{...g,value:config.transform==='yoy'?(g.value-prior.value)/prior.value*100:g.value-prior.value,prior:prior.value}];
      });
      note+=' '+(config.transform==='yoy'?'Year over year is (this year − previous year) / previous year × 100, rather than the amount itself. Nonpositive prior bases and missing prior years are unavailable.':'Absolute change from the immediately preceding period; missing periods are unavailable.');
      if(!output.length)throw new Error('No comparable consecutive periods with a valid prior base.');
    }
    if(config.transform==='forecast') {
      if(config.start||config.end||config.limit)throw new Error('Use all worksheet rows before forecasting; row bounds and row-count limits can cut a period short. Subject and category filters remain available.');
      const horizon=Number(config.horizon||2);
      if(!Number.isInteger(horizon)||horizon<1||horizon>10)throw new Error('Choose a projection horizon from 1 to 10 periods.');
      const asOf=new Date(config.as_of||new Date().toISOString());
      if(!Number.isFinite(asOf.getTime()))throw new Error('The forecast reference date is invalid.');
      const partial=new Set((data.partial_periods||[]).map(Number)), complete=Array.isArray(data.complete_periods)?new Set(data.complete_periods.map(Number)):null;
      const coverage=data.period_coverage||{};
      const through=data.verified_data_through?new Date(data.verified_data_through):null;
      const observed=groups.map(g=>{
        const members=bucketCalendarYears?[g.year-1,g.year]:[g.year];
        const openPeriod=g.year>=asOf.getUTCFullYear();
        const missingEvidence=incompleteYears.has(g.year)||members.some(year=>{
          const evidence=coverage[year], status=typeof evidence==='string'?evidence:evidence?.status;
          return partial.has(year)||complete&&!complete.has(year)||bucketCalendarYears&&!sourceYears.get(g.year)?.has(year)||evidence?.complete===false||['partial','incomplete','current','unknown'].includes(status);
        });
        const beforePeriodEnd=through&&(!Number.isFinite(through.getTime())||through.getTime()<Date.UTC(g.year,11,31));
        return {...g,partial:!!(openPeriod||missingEvidence||beforePeriodEnd)};
      });
      const history=observed.filter(g=>!g.partial), phases=config.period==='cycle'?[0,2]:[0,1,2,3];
      if(phases.some(phase=>history.filter(g=>g.year%4===phase).length<2))throw new Error(config.period==='cycle'?'An election-aware baseline needs at least four completed two-year cycles: two presidential-cycle and two midterm-cycle observations. Current, partial and missing-value periods cannot train it.':'An election-aware baseline needs at least eight completed calendar years: two observations in every phase of the four-year election calendar. Current, partial and missing-value years cannot train it.');
      if(history.some((g,i)=>i&&g.year-history[i-1].year!==step))throw new Error('The election-aware baseline needs consecutive completed periods; missing or partial history is not filled with zero.');
      // Seasonal-naïve benchmark: https://otexts.com/fpp3/simple-methods.html.
      // The four-year calendar determines matching, never an assumed dollar uplift.
      const predicted=Array.from({length:horizon},(_,i)=>{
        const year=observed.at(-1).year+step*(i+1), basis=history.findLast(g=>g.year%4===year%4);
        return {year,label:String(year),value:basis.value,forecast:true,n:0,forecast_basis_year:basis.year};
      });
      output=[...observed,...predicted];
      note+=' Dashed values are a same-election-phase historical baseline (seasonal naïve), not a calibrated prediction: each repeats the latest completed value from the same phase of the four-year calendar. Presidential and midterm differences come only from the selected history; no fixed uplift, extrapolated growth or causal effect is assumed.';
      if(observed.some(g=>g.partial))note+=' Current or partial periods remain visible as partial observations and are excluded from the baseline history.';
      note+=' Calendar closure alone does not establish collection completeness. Closed source periods without explicit completeness evidence are used as reported, with that limitation. No calibrated uncertainty interval is available; future totals can differ substantially.';
    }
    const additive = ['sum','count'].includes(config.aggregation);
    return {type:config.type,groups:output,selected:rows.length,excluded,note,temporal:true,
      transform:config.transform, units:config.transform==='yoy'?'percent':config.aggregation==='count'?'count':'source',
      observedTotal:additive?groups.reduce((total,g)=>total+g.value,0):null,
      forecastTotal:config.transform==='forecast'&&additive?output.filter(g=>g.forecast).reduce((total,g)=>total+g.value,0):null};
  }
  // W5: time analysis under config.lenient. Every non-axis dimension, Colour and
  // an unfiltered Subject join (with " / ") into one series key; each series
  // gets its own periods, change and forecast. Without a series key the result
  // has today's shape and values.
  function temporalFlexible(data, config, ctx) {
    if(!['annual','yoy','change','forecast'].includes(config.transform))throw new Error('Choose a time analysis.');
    const column=data.columns[config.x];
    if(!column)throw new Error('Choose a date or year column.');
    validateGrain(data, config, filterRows(data, config, ctx), ctx);
    if (config.splitMulti) warn(ctx, 'Time analysis reads multi-value cells whole; it does not split them.');
    if (config.size || config.label || config.colorMeasure || Array.isArray(config.tooltip) && config.tooltip.length) {
      warn(ctx, 'Size, Label, Tooltip and a Colour measure are not computed for time analysis; the periods show the measure itself.');
    }
    const baseType = BASE_OF[config.type] || config.type;
    const cycleInput=/cycle/i.test(column.name);
    let period = config.period;
    if (cycleInput && period !== 'cycle') { warn(ctx, 'Election cycles are two-year periods, so the view uses election cycles.'); period = 'cycle'; }
    const bucketCalendarYears=period==='cycle'&&!cycleInput;
    const bucketYear=year=>bucketCalendarYears?year+year%2:year;
    const transform = config.transform, percent = transform === 'yoy';
    if (period === 'cycle' && percent) warn(ctx, 'Year over year on election cycles is shown as cycle-over-cycle change (%).');
    const measures = measureList(config);
    for (const m of measures) if (!aggregations.includes(m.aggregation)) throw new Error('Choose an aggregation.');
    const rows=filterRows(data,config,ctx);
    const keyColumns = dimensions(config).filter(c => c !== config.x);
    if (data.columns[config.series] && config.series !== config.x && !keyColumns.includes(config.series)) keyColumns.push(config.series);
    const subjectColumn = data.columns.find(c => c.name === 'Subject');
    const subjectIndex = subjectColumn ? subjectColumn.index ?? data.columns.indexOf(subjectColumn) : -1;
    if (subjectIndex >= 0 && subjectIndex !== config.x && !keyColumns.includes(subjectIndex) && new Set(rows.map(r => r[subjectIndex])).size > 1) {
      keyColumns.push(subjectIndex);
      warn(ctx, 'Several Subjects overlap, so each Subject is drawn as its own series rather than added together.');
    }
    const multiMeasure = Array.isArray(config.measures) && measures.length >= 2;
    const wantSeries = keyColumns.length > 0 || multiMeasure;
    const useSeries = wantSeries && !!SERIES_OF[baseType];
    if (wantSeries && !useSeries) {
      warn(ctx, 'A ' + baseType + ' view over time shows period totals, so its separate series are added together.');
      if (keyColumns.includes(subjectIndex)) refuse(ctx, 'Grand total is not shown: overlapping subjects are added together.');
    }
    const used = useSeries ? measures : [measures[0]];
    const labels = used.map(m => specLabel(data, m));
    const keyOf = row => keyColumns.map(c => row[c] == null ? '(Missing)' : String(row[c])).join(' / ');
    const nameOf = (key, k) => !useSeries ? '' : multiMeasure ? (keyColumns.length ? key + ' · ' + labels[k] : labels[k]) : key;
    const state = new Map(), sourceYears = new Map(); let excluded = 0;
    const seriesState = name => { if (!state.has(name)) state.set(name, {name, spec:null, byYear:new Map(), incomplete:new Set()}); return state.get(name); };
    for (const row of rows) {
      const raw=String(row[config.x]??'').trim();
      const match=cycleInput?raw.match(/^(?:(\d{4})[-–])?(\d{4})$/):raw.match(/^(\d{4})(?:-\d{2}-\d{2}(?:T.*)?)?$/);
      if(!match){excluded++;continue;}
      const sourceYear=Number(match[cycleInput?2:1]), year=bucketYear(sourceYear);
      if(sourceYear<1800||year>2200||cycleInput&&(sourceYear%2!==0||match[1]&&Number(match[1])!==sourceYear-1)){excluded++;continue;}
      const key = useSeries ? keyOf(row) : '';
      if(!cycleInput&&raw.length>4){
        const calendarDate=new Date(raw.slice(0,10)+'T00:00:00Z'), timestamp=new Date(raw);
        if(!Number.isFinite(calendarDate.getTime())||calendarDate.toISOString().slice(0,10)!==raw.slice(0,10)||!Number.isFinite(timestamp.getTime())){
          used.forEach((_, k) => seriesState(nameOf(key, k)).incomplete.add(year));excluded++;continue;
        }
      }
      if(!sourceYears.has(year))sourceYears.set(year,new Set());sourceYears.get(year).add(sourceYear);
      let found = 0;
      used.forEach((m, k) => {
        const s = seriesState(nameOf(key, k)); s.spec = m;
        const counting = m.aggregation === 'count' || m.aggregation === 'countd';
        if (counting && !s.byYear.has(year)) s.byYear.set(year, []);
        const value = observation(row, {y:m.column, aggregation:m.aggregation});
        if (value === null) { if (!counting) s.incomplete.add(year); return; }
        found++;
        if (!s.byYear.has(year)) s.byYear.set(year, []); s.byYear.get(year).push(value);
      });
      if (!found) excluded++;
    }
    const step=period==='cycle'?2:1;
    const aggregation = used[0].aggregation;
    const statistic=aggregation==='sum'?'totals':aggregation==='count'?'row counts':aggregation==='countd'?'distinct counts':aggregation+' values';
    let note=period==='cycle'?`Over time shows ${statistic} for each two-year election-cycle reporting period.`:`Over time shows calendar-year ${statistic}; partial years are not annualized.`;
    if (['yoy','change'].includes(transform)) note += ' ' + (percent ? (period === 'cycle'
      ? 'Cycle over cycle is (this cycle − previous cycle) / previous cycle × 100, rather than the amount itself. Nonpositive prior bases and missing prior cycles are unavailable.'
      : 'Year over year is (this year − previous year) / previous year × 100, rather than the amount itself. Nonpositive prior bases and missing prior years are unavailable.')
      : 'Absolute change from the immediately preceding period; missing periods are unavailable.');
    if (useSeries) note += ' Each series is computed on its own; series are independent, with no combined total.';
    // Forecast settings are checked once; each series then trains on its own history.
    let horizon = 0, asOf = null;
    if (transform === 'forecast') {
      if(config.start||config.end||config.limit)warn(ctx, 'The forecast uses the row bounds and row-count limit you set; they can cut a period short.');
      horizon = Number(config.horizon||2);
      if(!Number.isInteger(horizon)||horizon<1){ const whole = Math.trunc(horizon); horizon = Number.isFinite(whole) && whole >= 1 ? whole : 2; warn(ctx, 'The projection horizon was not a positive whole number, so ' + horizon + ' periods are projected.'); }
      if(horizon>10){ if (horizon > 40) horizon = 40; warn(ctx, 'Projections beyond 10 periods repeat the same-phase baseline; ' + horizon + ' periods are projected.'); }
      asOf=new Date(config.as_of||new Date().toISOString());
      if(!Number.isFinite(asOf.getTime())){ warn(ctx, 'The forecast reference date is invalid, so today is used.'); asOf = new Date(); }
    }
    const partial=new Set((data.partial_periods||[]).map(Number)), complete=Array.isArray(data.complete_periods)?new Set(data.complete_periods.map(Number)):null;
    const coverage=data.period_coverage||{}, through=data.verified_data_through?new Date(data.verified_data_through):null;
    const short = [];
    const outputs = new Map();
    let anyPartial = false, anyForecast = false, totalGroups = [];
    for (const s of state.values()) {
      const spec = s.spec || used[0], counting = spec.aggregation === 'count';
      const groups=[...s.byYear].sort((a,b)=>a[0]-b[0]).map(([year,values])=>({label:String(year),year,n:values.length,value:aggregate(values,spec,counting)}));
      if (!useSeries) totalGroups = groups;
      let output = groups;
      if (['yoy','change'].includes(transform)) output = groups.flatMap(g=>{
        const prior=groups.find(p=>p.year===g.year-step);
        if(!prior||percent&&prior.value<=0)return [];
        return [{...g,value:percent?(g.value-prior.value)/prior.value*100:g.value-prior.value,prior:prior.value}];
      });
      if (transform === 'forecast' && groups.length) {
        const observed=groups.map(g=>{
          const members=bucketCalendarYears?[g.year-1,g.year]:[g.year];
          const openPeriod=g.year>=asOf.getUTCFullYear();
          const missingEvidence=s.incomplete.has(g.year)||members.some(year=>{
            const evidence=coverage[year], status=typeof evidence==='string'?evidence:evidence?.status;
            return partial.has(year)||complete&&!complete.has(year)||bucketCalendarYears&&!sourceYears.get(g.year)?.has(year)||evidence?.complete===false||['partial','incomplete','current','unknown'].includes(status);
          });
          const beforePeriodEnd=through&&(!Number.isFinite(through.getTime())||through.getTime()<Date.UTC(g.year,11,31));
          return {...g,partial:!!(openPeriod||missingEvidence||beforePeriodEnd)};
        });
        if (observed.some(g => g.partial)) anyPartial = true;
        const history=observed.filter(g=>!g.partial), phases=period==='cycle'?[0,2]:[0,1,2,3];
        const enough = !phases.some(phase=>history.filter(g=>g.year%4===phase).length<2) && !history.some((g,i)=>i&&g.year-history[i-1].year!==step);
        if (!enough) { short.push(s.name); output = observed; }
        else {
          anyForecast = true;
          // Seasonal-naïve benchmark: https://otexts.com/fpp3/simple-methods.html.
          const predicted=Array.from({length:horizon},(_,i)=>{
            const year=observed.at(-1).year+step*(i+1), basis=history.findLast(g=>g.year%4===year%4);
            return {year,label:String(year),value:basis.value,forecast:true,n:0,forecast_basis_year:basis.year};
          });
          output=[...observed,...predicted];
        }
      }
      outputs.set(s.name, output);
    }
    if (transform === 'forecast') {
      if (short.length) warn(ctx, (useSeries ? 'No baseline is projected for ' + short.join(', ') + ': ' : 'No baseline is projected: ') + (period==='cycle'
        ? 'an election-aware baseline needs at least four completed consecutive two-year cycles, two in each phase.'
        : 'an election-aware baseline needs at least eight completed consecutive calendar years, two in every phase of the four-year election calendar.') + ' The observed periods are shown.');
      if (anyForecast) note+=' Dashed values are a same-election-phase historical baseline (seasonal naïve), not a calibrated prediction: each repeats the latest completed value from the same phase of the four-year calendar. Presidential and midterm differences come only from the selected history; no fixed uplift, extrapolated growth or causal effect is assumed.';
      if(anyPartial)note+=' Current or partial periods remain visible as partial observations and are excluded from the baseline history.';
      note+=' Calendar closure alone does not establish collection completeness. Closed source periods without explicit completeness evidence are used as reported, with that limitation. No calibrated uncertainty interval is available; future totals can differ substantially.';
    }
    const units = percent ? 'percent' : aggregation === 'count' ? 'count' : 'source';
    const additive = ['sum','count'].includes(aggregation);
    const common = {selected:rows.length, excluded, note, temporal:true, transform, units};
    if (!useSeries) {
      const output = outputs.get('') || [];
      if (!output.length) warn(ctx, totalGroups.length ? 'No comparable consecutive periods with a valid prior base.' : 'No valid dated numeric observations remain.');
      return Object.assign({type:baseType, groups:output}, common, {
        observedTotal:additive?totalGroups.reduce((total,g)=>total+g.value,0):null,
        forecastTotal:transform==='forecast'&&additive?output.filter(g=>g.forecast).reduce((total,g)=>total+g.value,0):null});
    }
    const names = [...outputs.keys()], years = [...new Set([...outputs.values()].flatMap(list => list.map(g => g.year)))].sort((a, b) => a - b);
    if (!years.length) warn(ctx, ['yoy','change'].includes(transform) ? 'No comparable consecutive periods with a valid prior base.' : 'No valid dated numeric observations remain.');
    const groups = years.map(year => {
      const values = names.map(name => {
        const g = outputs.get(name).find(p => p.year === year);
        if (!g) return {series:name, value:null, n:0};
        const v = {series:name, value:g.value, n:g.n};
        if (g.prior != null) v.prior = g.prior;
        if (g.forecast) { v.forecast = true; v.forecast_basis_year = g.forecast_basis_year; }
        if (g.partial) v.partial = true;
        return v;
      });
      const group = {label:String(year), year, values};
      if (values.some(v => v.forecast) && values.every(v => v.value == null || v.forecast)) group.forecast = true;
      return group;
    });
    const result = Object.assign({type:SERIES_OF[baseType], groups, series:names}, common, {observedTotal:null, forecastTotal:null});
    if (multiMeasure) {
      if (!keyColumns.length) result.measureSeries = true;
      result.seriesConfig = names.map(name => { const s = state.get(name).spec || used[0]; return {column:s.column, aggregation:s.aggregation}; });
    }
    return seriesLayout(result, config, ctx);
  }
  // chartColumns / timeColumn / identifierColumn are exported so Chart Studio
  // classifies fields through this module instead of forking the rules.
  const api = {numeric, quantile, box, filterRows, recommend, quickConfig, build, temporal,
    chartColumns, timeColumn, identifierColumn, measureScore, explodeRows, groupLimit, DEFAULT_GROUP_LIMIT,
    aggregateSpec, specLabel:(data, spec) => specLabel(data, specOf(spec) || {column:-1, aggregation:'count'}),
    chartTypes:Object.freeze(chartTypes.slice()), aggregations:Object.freeze(aggregations.slice())};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MentatCharts = api;
})(globalThis);
