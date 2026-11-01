/* Plan amounts/counts come from Subscriptions. This table only filters and sorts. */
import {parseRegisterAmount} from './transaction-grid.js';

export const planColumns = [
  {key:'merchant', label:'Plan', type:'text', width:185},
  {key:'category', label:'Category', type:'text', width:175},
  {key:'account', label:'Account', type:'text', width:150},
  {key:'amount', label:'Charge / frequency', type:'money', width:165},
  {key:'monthly', label:'Monthly', type:'money', width:100},
  {key:'annual', label:'Annual', type:'money', width:100},
  {key:'occurrences', label:'Total charges', type:'count', width:95},
  {key:'occurrencesLast12Months', label:'Charges · last 12 months', type:'count', width:145},
  {key:'date', label:'Last charge', type:'date', width:110}
];
export const createPlanTableState = () => ({sortKey:'monthly', descending:true, filterKey:'merchant', filters:{}, scrollLeft:0, scrollTop:0});
const value = (row, key) => ['monthly','annual'].includes(key) && row.cadence === 'unknown' ? null : row[key] ?? null;
const active = filter => Object.values(filter || {}).some(item => item !== '');
const compare = (a, b) => typeof a === 'number' ? a - b : String(a).localeCompare(String(b), undefined, {sensitivity:'base',numeric:true});
const moneyBound = (text,digits) => Math.abs(parseRegisterAmount(text.replace(/^\./,'0.'),digits,true));

export function selectPlans(rows, state, digits = 2) {
  const filters = planColumns.filter(column => active(state.filters[column.key])).map(column => {
    const filter = state.filters[column.key];
    const bound = text => !text ? null : column.type === 'money' ? moneyBound(text,digits) : column.type === 'count' ? Number(text) : text;
    return {...column, ...filter, min:bound(filter.min), max:bound(filter.max)};
  });
  return rows.filter(row => filters.every(filter => {
    const item = value(row, filter.key);
    if (item === null) return false;
    if (filter.query && !String(item).toLocaleLowerCase().includes(filter.query.trim().toLocaleLowerCase())) return false;
    if (filter.cadence && row.cadence !== filter.cadence) return false;
    return (filter.min === null || item >= filter.min) && (filter.max === null || item <= filter.max);
  })).sort((a,b) => {
    const first = value(a, state.sortKey), second = value(b, state.sortKey);
    // Missing/unknown equivalents stay last in either direction.
    if (first === null || second === null) return first === second ? 0 : first === null ? 1 : -1;
    return compare(first,second) * (state.descending ? -1 : 1) || compare(a.merchant,b.merchant) || compare(a.account,b.account) || compare(a.id,b.id);
  });
}

export function renderSubscriptionPlans(parent, data, state, {format, digits}) {
  const el = (tag, text = '', className = '') => {const node = document.createElement(tag); node.textContent = text; node.className = className; return node;};
  const field = (parent, label, type = 'text') => {const wrapper = el('label',label), input = el(type === 'select' ? 'select' : 'input'); if (type !== 'select') input.type = type; wrapper.append(input); parent.append(wrapper); return input;};
  const option = (select, label, key) => {const node = el('option',label); node.value = key; select.append(node);};
  const toolbar = el('div','','subscription-plan-filters');
  const choice = field(toolbar,'Filter column','select');
  for (const column of planColumns) option(choice,column.label,column.key);
  choice.value = state.filterKey;
  const controls = el('div','','subscription-plan-filter-controls'); toolbar.append(controls);
  const clear = el('button','Clear all filters','button secondary'); clear.type = 'button'; toolbar.append(clear);
  const count = el('p','','settings-notice'); count.setAttribute('role','status'); count.setAttribute('aria-live','polite');
  const wrapper = el('div','','table-scroll subscription-plans-scroll'); wrapper.tabIndex = 0; wrapper.setAttribute('role','region'); wrapper.setAttribute('aria-label','Subscription plans');
  const table = el('table','','transactions-table subscription-plans-table'); table.setAttribute('aria-label','Your subscription plans');
  const columns = el('colgroup'), head = el('thead'), headings = el('tr'), body = el('tbody');
  const headers = new Map();
  for (const column of planColumns) {
    const col = el('col'); col.style.width = `${column.width}px`; columns.append(col);
    const th = el('th'), sort = el('button',column.label,'subscription-plan-sort'), filter = el('button','▾','subscription-plan-filter');
    th.scope = 'col'; th.dataset.planColumn = column.key; th.title = column.label;
    if (['money','count'].includes(column.type)) th.classList.add('subscription-plan-number');
    sort.type = filter.type = 'button'; sort.setAttribute('aria-label',`Sort by ${column.label}`);
    if (column.key === 'amount') sort.title = 'Sort by charge amount';
    filter.setAttribute('aria-label',`Filter ${column.label}`); filter.title = `Filter ${column.label}`;
    sort.addEventListener('click',() => {state.descending = state.sortKey === column.key ? !state.descending : column.type !== 'text'; state.sortKey = column.key; populate();});
    filter.addEventListener('click',() => {state.filterKey = column.key; choice.value = column.key; renderFilter(); controls.querySelector('input,select')?.focus();});
    th.append(sort,filter); headings.append(th); headers.set(column.key,{th,sort,filter});
  }
  head.append(headings); table.append(columns,head,body); wrapper.append(table); parent.append(toolbar,count,wrapper);
  wrapper.addEventListener('scroll',() => {state.scrollLeft = wrapper.scrollLeft; state.scrollTop = wrapper.scrollTop;});
  requestAnimationFrame(() => {if (wrapper.isConnected) {wrapper.scrollLeft = state.scrollLeft; wrapper.scrollTop = state.scrollTop;}});
  if (data.asOfDate && data.chargesSince) parent.append(el('p',`Classified charges through ${data.asOfDate}. Last 12 months: ${data.chargesSince} to ${data.asOfDate}. Equivalents use the latest charge and billing frequency.`, 'settings-notice'));
  const frequencies = {monthly:'/ month',annual:'/ year',quarterly:'/ quarter',weekly:'/ week',unknown:'· interval unknown'};
  function populate() {
    const rows = selectPlans(data.subscriptions,state,digits);
    body.replaceChildren();
    for (const row of rows) {
      const tr = el('tr');
      for (const column of planColumns) {
        const item = value(row,column.key);
        const text = item === null ? '—' : column.type === 'money' ? format(item) + (column.key === 'amount' ? ` ${frequencies[row.cadence]}` : '') : column.type === 'count' ? item.toLocaleString() : item;
        const cell = el('td',text, ['money','count'].includes(column.type) ? 'subscription-plan-number' : '');
        cell.title = item === null ? row.cadence === 'unknown' && ['monthly','annual'].includes(column.key) ? 'Billing interval needs review' : 'Available with Subscriptions 1.2 or newer' : String(text);
        if (column.key === 'merchant' && !row.active) {cell.title += ' · No recent charge'; cell.setAttribute('aria-label',cell.title); cell.classList.add('subscription-plan-inactive');}
        tr.append(cell);
      }
      body.append(tr);
    }
    if (!rows.length) {const tr = el('tr'), cell = el('td',data.subscriptions.length ? 'No plans match these filters.' : 'Classify your transactions to find subscriptions, or label a payment below.'); cell.colSpan = planColumns.length; tr.append(cell); body.append(tr);}
    const filterCount = Object.values(state.filters).filter(active).length;
    count.textContent = `${rows.length.toLocaleString()} of ${data.subscriptions.length.toLocaleString()} plans${filterCount ? ` · ${filterCount} column ${filterCount === 1 ? 'filter' : 'filters'} active` : ''}`;
    clear.hidden = !filterCount;
    for (const column of planColumns) {
      const {th,sort,filter} = headers.get(column.key), selected = state.sortKey === column.key;
      th.setAttribute('aria-sort', selected ? state.descending ? 'descending' : 'ascending' : 'none');
      sort.textContent = column.label + (selected ? state.descending ? ' ↓' : ' ↑' : '');
      filter.classList.toggle('is-active',active(state.filters[column.key]));
      choice.querySelector(`option[value="${column.key}"]`).textContent = column.label + (active(state.filters[column.key]) ? ' · filtered' : '');
    }
  }
  function renderFilter() {
    controls.replaceChildren();
    const column = planColumns.find(item => item.key === state.filterKey);
    const filter = state.filters[column.key] || {};
    const bind = (input,key) => {input.value = filter[key] || ''; input.addEventListener('input',() => {
      input.setCustomValidity('');
      if (!input.checkValidity()) return;
      if (column.type === 'money' && ['min','max'].includes(key) && input.value) {
        try {moneyBound(input.value,digits);} catch (error) {input.setCustomValidity(error.message); return;}
      }
      state.filters[column.key] = {...state.filters[column.key],[key]:input.value}; populate();
    });};
    if (column.type === 'text') bind(field(controls,`${column.label} contains`),'query');
    else {
      for (const [key,label] of [['min',column.type === 'date' ? 'From date' : 'At least'],['max',column.type === 'date' ? 'Through date' : 'At most']]) {
        const input = field(controls,`${column.label} · ${label}`,column.type === 'date' ? 'date' : 'number');
        if (column.type !== 'date') {input.min = '0'; input.step = String(column.type === 'count' ? 1 : 10 ** -digits); input.max = String(column.type === 'count' ? Number.MAX_SAFE_INTEGER : 1_000_000_000_000 / 10 ** digits);}
        bind(input,key);
      }
      if (column.key === 'amount') {
        const cadence = field(controls,'Billing frequency','select');
        for (const [key,label] of [['','Any frequency'],['monthly','Monthly'],['annual','Annual'],['quarterly','Quarterly'],['weekly','Weekly'],['unknown','Unknown']]) option(cadence,label,key);
        bind(cadence,'cadence');
      }
    }
  }
  choice.addEventListener('change',() => {state.filterKey = choice.value; renderFilter();});
  clear.addEventListener('click',() => {state.filters = {}; renderFilter(); populate();});
  renderFilter(); populate();
}
