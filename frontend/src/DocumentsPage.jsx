import { useEffect, useRef, useState } from 'react';

const TYPES = [
  ['all', 'همه اسناد'], ['payment', 'واریز و برداشت'],
  ['trade', 'معاملات'], ['delivery', 'دریافت و تحویل کالا'], ['other', 'سایر'],
];
const INITIAL_FILTERS = {
  kind: 'all', search: '', customer: '', direction: 'all',
  from_date: '', to_date: '', product: '', document_id: '', page: 1, page_size: 25,
};
const INITIAL_ADVANCED = {
  customer: '', direction: 'all', from_date: '', to_date: '',
  product: '', document_id: '', page_size: 25,
};

const DIRECTION_OPTIONS = {
  all: [['all', 'همه جهت‌ها'], ['incoming', 'ورود به حساب'], ['outgoing', 'خروج از حساب'],
    ['customer_buys', 'خرید مشتری از ما'], ['customer_sells', 'فروش مشتری به ما']],
  payment: [['all', 'همه جهت‌ها'], ['incoming', 'دریافت وجه از مشتری'], ['outgoing', 'پرداخت وجه به مشتری']],
  delivery: [['all', 'همه جهت‌ها'], ['incoming', 'دریافت کالا از مشتری'], ['outgoing', 'تحویل کالا به مشتری']],
  trade: [['all', 'همه جهت‌ها'], ['customer_buys', 'خرید مشتری از ما'], ['customer_sells', 'فروش مشتری به ما']],
  other: [['all', 'همه جهت‌ها'], ['incoming', 'ورود به حساب'], ['outgoing', 'خروج از حساب']],
};

function currencyLabel(value) {
  const text = String(value || '').trim();
  if (/^(irr|rial|rials|ریال)$/i.test(text)) return 'ریال';
  if (/^(تومان|toman)$/i.test(text)) return 'تومان';
  return text || 'واحد منبع';
}

const missing = value => value == null || value === '' || String(value).trim().toLowerCase() === 'null';

function documentQuantities(source, formatNumber) {
  const count = source?.Count;
  const weight = source?.Vazn;
  const hasWeight = !missing(weight) && Number.isFinite(Number(weight)) && Number(weight) !== 0;
  const hasCount = !missing(count) && !(Number(count) === 0 && hasWeight);
  return [
    hasCount ? `تعداد ${formatNumber(count)}` : '',
    hasWeight ? `وزن ${formatNumber(weight)} گرم` : '',
  ].filter(Boolean);
}

export default function DocumentsPage({ api, formatNumber: fa, normalizeInput, onDocument,
  fixedKind, title = 'اسناد', onCreate, createLabel = 'ثبت سند', refreshVersion }) {
  const [filters, setFilters] = useState(() => ({ ...INITIAL_FILTERS, kind: fixedKind || 'all' }));
  const [advanced, setAdvanced] = useState(INITIAL_ADVANCED);
  const [search, setSearch] = useState('');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const refreshRequested = useRef(false);
  const previousRefreshVersion = useRef(refreshVersion);

  const change = (key, value) => setFilters(previous => ({ ...previous, [key]: value, page: 1 }));
  const changeAdvanced = (key, value) => setAdvanced(previous => ({ ...previous, [key]: value }));

  useEffect(() => {
    const timer = setTimeout(() => setFilters(previous => previous.search === search
      ? previous : { ...previous, search, page: 1 }), 350);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    const externallyRefreshed = previousRefreshVersion.current !== refreshVersion;
    previousRefreshVersion.current = refreshVersion;
    const query = new URLSearchParams(Object.entries(filters)
      .filter(([, value]) => value !== '' && value != null)
      .map(([key, value]) => [key, normalizeInput(value)]));
    if (fixedKind) query.set('kind', fixedKind);
    if (refreshRequested.current || externallyRefreshed) {
      query.set('refresh', 'true');
      refreshRequested.current = false;
    }
    setBusy(true);
    setError('');
    api(`/api/documents?${query}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [api, filters, normalizeInput, reload, fixedKind, refreshVersion]);

  const refresh = () => { refreshRequested.current = true; setReload(value => value + 1); };
  const pages = Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || filters.page_size)));
  const filtered = Object.entries(INITIAL_FILTERS).some(([key, value]) =>
    !['page', 'page_size', 'kind'].includes(key) && filters[key] !== value)
    || (!fixedKind && filters.kind !== 'all');
  const directionOptions = DIRECTION_OPTIONS[fixedKind] || DIRECTION_OPTIONS.all;
  const scopeLabel = fixedKind ? TYPES.find(([kind]) => kind === fixedKind)?.[1] : '';
  const resultLabel = filtered ? 'در نتایج' : fixedKind ? `از همه مشتریان · ${scopeLabel}` : 'برای همه مشتریان';

  return <section className="page documents-page">
    <div className="heading">
      <div><small>دفتر اسناد ته حساب</small><h1>{title}</h1><p>{fixedKind
        ? `${scopeLabel} همه مشتریان، از جدید به قدیم. برای جزئیات روی هر ردیف بزنید.`
        : 'اسناد همه مشتریان، از جدید به قدیم. برای جزئیات روی هر ردیف بزنید.'}</p></div>
      <div className="headingactions">
        {onCreate && <button className="primary" onClick={onCreate}>{createLabel}</button>}
        <button className="secondary" onClick={refresh} disabled={busy}>↻ تازه‌سازی</button>
      </div>
    </div>

    <div className="document-tools card">
      <label className="document-search">جستجو<input value={search} onChange={event => setSearch(event.target.value)}
        placeholder="شرح، مبلغ، شماره سند یا کد مشتری" type="search" maxLength={100}/></label>
      {!fixedKind && <div className="document-types" aria-label="نوع سند">
        {TYPES.map(([value, label]) => <button key={value} className={filters.kind === value ? 'chosen' : ''}
          aria-pressed={filters.kind === value} onClick={() => change('kind', value)}>{label}</button>)}
      </div>}
      <details className="document-filters"><summary>فیلترهای بیشتر</summary>
        <form className="filtergrid" onSubmit={event => { event.preventDefault(); setFilters(previous => ({ ...previous, ...advanced, page: 1 })); }}>
          <label className="field">کد مشتری (اختیاری)<input value={advanced.customer} inputMode="numeric"
            onChange={event => changeAdvanced('customer', event.target.value)} placeholder="همه مشتریان"/></label>
          <label className="field">از تاریخ شمسی<input value={advanced.from_date} placeholder="۱۴۰۵/۰۷/۰۱"
            onChange={event => changeAdvanced('from_date', event.target.value)}/></label>
          <label className="field">تا تاریخ شمسی<input value={advanced.to_date} placeholder="۱۴۰۵/۰۷/۳۰"
            onChange={event => changeAdvanced('to_date', event.target.value)}/></label>
          <label className="field">جهت<select value={advanced.direction} onChange={event => changeAdvanced('direction', event.target.value)}>
            {directionOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label className="field">محصول<input value={advanced.product} onChange={event => changeAdvanced('product', event.target.value)}/></label>
          <label className="field">شماره سند<input value={advanced.document_id} onChange={event => changeAdvanced('document_id', event.target.value)}/></label>
          <label className="field">تعداد ردیف<select value={advanced.page_size} onChange={event => changeAdvanced('page_size', Number(event.target.value))}>
            <option value={25}>۲۵</option><option value={50}>۵۰</option><option value={100}>۱۰۰</option>
          </select></label>
          <button className="primary" type="submit">اعمال فیلترها</button>
          <button className="secondary" type="button" onClick={() => { setSearch(''); setAdvanced(INITIAL_ADVANCED); setFilters({ ...INITIAL_FILTERS, kind: fixedKind || 'all' }); }}>پاک کردن فیلترها</button>
        </form>
      </details>
    </div>

    {busy && <div className="notice loading" role="status"><span className="spinner"/>
      {data ? 'در حال به‌روزرسانی اسناد…' : 'در حال دریافت اسناد همه مشتریان…'}</div>}
    {error && <div className="notice error" role="alert">{error}{data && ' · آخرین نتایج موفق نگه داشته شده‌اند.'}
      <button className="linkbtn" onClick={refresh} disabled={busy}>تلاش دوباره</button></div>}

    {data && <div className="card history">
      <div className="cardhead"><div><b>{fa(data.total)} سند {resultLabel}</b>
        <small>آخرین دریافت موفق: {data.fetched_at ? new Date(data.fetched_at).toLocaleString('fa-IR') : '—'}</small></div></div>
      {!data.items.length ? <div className="empty">{fixedKind && !filtered
        ? 'سندی در این بخش وجود ندارد.'
        : data.source_total ? (filtered ? 'سندی مطابق این فیلترها پیدا نشد. فیلترها را پاک کنید.' : 'منبع سندی برنگرداند.')
          : 'منبع سندی برنگرداند.'}</div>
        : <div className="tablewrap"><table><thead><tr>
          <th>تاریخ</th><th>مشتری</th><th>نوع سند / شرح</th><th>محصول</th><th>{fixedKind === 'delivery' ? 'مقدار' : 'مبلغ'}</th><th>شماره / ردیف</th><th/>
        </tr></thead><tbody>{data.items.map((row, index) => <tr
          key={`${row.customer_code ?? 'unknown'}-${row.id}-${row.radif}-${index}`} onClick={() => onDocument(row.source)}>
          <td>{row.date || '—'}<small>{row.time || ''}</small></td>
          <td><b>{row.customer_name || (row.customer_code == null ? 'مشتری در منبع مشخص نشده' : `مشتری ${fa(row.customer_code)}`)}</b>
            {row.customer_code != null && <small>کد ته حساب {fa(row.customer_code)}</small>}</td>
          <td><em className={`badge ${row.category}`}>{!missing(row.label) ? row.label : TYPES.find(([value]) => value === row.category)?.[1] || 'سایر'}</em>
            {!missing(row.description) && <small className="document-description">{row.description}</small>}
            {['1', 'true'].includes(String(row.temporary).toLowerCase()) && <small>سند موقت</small>}</td>
          <td>{missing(row.product) ? '—' : row.product}
            {fixedKind === 'trade' && documentQuantities(row.source, fa).map(value => <small key={value}>{value}</small>)}</td>
          <td className="document-amount">{fixedKind === 'delivery'
            ? documentQuantities(row.source, fa).length
              ? documentQuantities(row.source, fa).map(value => <small key={value}>{value}</small>)
              : '—'
            : missing(row.amount) ? '—' : `${fa(row.amount)} ${currencyLabel(row.currency)}`}</td>
          <td>{fa(row.id)} / {fa(row.radif)}</td>
          <td><button className="linkbtn" onClick={event => { event.stopPropagation(); onDocument(row.source); }}
            aria-label={`جزئیات سند ${row.id} ردیف ${row.radif}`}>جزئیات ←</button></td>
        </tr>)}</tbody></table></div>}
      <div className="cardfoot"><span>{fa(data.source_total)} سند در کل منبع</span><div className="pages">
        <button disabled={filters.page <= 1 || busy} onClick={() => setFilters(previous => ({ ...previous, page: previous.page - 1 }))}>قبلی</button>
        صفحه {fa(data.page)} از {fa(pages)}
        <button disabled={filters.page >= pages || busy} onClick={() => setFilters(previous => ({ ...previous, page: previous.page + 1 }))}>بعدی</button>
      </div></div>
    </div>}
  </section>;
}
