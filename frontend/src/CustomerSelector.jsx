import { consoleUrl } from './deployment.js';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

const toEn = (value = '') => String(value)
  .replace(/[\u06f0-\u06f9\u0660-\u0669]/g, digit => String(digit.charCodeAt(0) - (digit.charCodeAt(0) >= 0x06f0 ? 0x06f0 : 0x0660)))
  .replace(/\u066b/g, '.')
  .replace(/[\u066c,]/g, '')
  .replace(/ي/g, 'ی')
  .replace(/ك/g, 'ک');
const fa = value => String(value ?? '').replace(/[0-9]/g, digit => String.fromCharCode(0x06f0 + Number(digit)));
const name = row => row?.Name || row?.name || row?.FullName || row?.Name_Moshtari || row?.MoshtariName || 'نام ثبت نشده';
const code = row => row?.Code ?? row?.MCode ?? row?.code ?? row?.MoshtariCode ?? '';
const phone = row => row?.Mobile || row?.mobile || row?.Tel || row?.Phone || row?.MobileNo || '';
const matchesCode = (row, value) => String(code(row)) === String(value);

async function fetchJson(url, signal) {
  const response = await fetch(consoleUrl(url), { credentials: 'include', signal });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || 'دریافت فهرست مشتریان ناموفق بود.');
  return body;
}

export default function CustomerSelector({ items = [], value = '', onChange }) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [result, setResult] = useState({ items: [], total: 0 });
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [selectedRecord, setSelectedRecord] = useState(null);
  const requestId = useRef(0);
  const searchId = useId();

  useEffect(() => {
    const controller = new AbortController();
    const id = ++requestId.current;
    setBusy(true);
    setError('');
    const timer = setTimeout(async () => {
      setBusy(true);
      setError('');
      try {
        const params = new URLSearchParams({ q: toEn(query.trim()), page: String(page), page_size: '25' });
        const response = await fetchJson(`/api/customer-search?${params}`, controller.signal);
        if (id === requestId.current) setResult(response);
      } catch (err) {
        if (err.name !== 'AbortError' && id === requestId.current) setError(err.message || 'دریافت فهرست مشتریان ناموفق بود.');
      } finally {
        if (id === requestId.current) setBusy(false);
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, page]);

  useEffect(() => {
    if (!value) { setSelectedRecord(null); return; }
    const known = [...items, ...(result.items || [])].find(row => matchesCode(row, value));
    if (known) { setSelectedRecord(known); return; }
    if (selectedRecord && matchesCode(selectedRecord, value)) return;
    const controller = new AbortController();
    let active = true;
    fetchJson(`/api/customers/${encodeURIComponent(value)}`, controller.signal)
      .then(response => { if (active && response.item) setSelectedRecord(response.item); })
      .catch(err => { if (active && err.name !== 'AbortError') setSelectedRecord(null); });
    return () => { active = false; controller.abort(); };
  }, [value, items, result.items, selectedRecord]);

  const rows = useMemo(() => {
    const pageItems = Array.isArray(result.items) ? result.items : [];
    if (!value || pageItems.some(row => matchesCode(row, value))) return pageItems;
    const selected = selectedRecord || items.find(row => matchesCode(row, value)) || { Code: value, Name: 'مشتری انتخاب‌شده' };
    return [selected, ...pageItems];
  }, [items, result.items, selectedRecord, value]);
  const pageCount = Math.max(1, Math.ceil((Number(result.total) || 0) / 25));

  return <div className="field customer-selector">
    <label htmlFor={searchId}>مشتری <span className="required">*</span></label>
    <input id={searchId} type="search" placeholder="نام، موبایل یا کد را جستجو کنید" value={query}
      onChange={event => { setQuery(event.target.value); setPage(1); }} aria-label="جستجوی مشتری با نام، موبایل یا کد" />
    <select required value={value} onChange={event => onChange?.(event.target.value)} aria-label="انتخاب مشتری">
      <option value="">انتخاب مشتری</option>
      {rows.map((row, index) => <option key={`${code(row)}-${index}`} value={code(row)}>
        {name(row)} · کد {fa(code(row))}{phone(row) ? ` · ${phone(row)}` : ''}
      </option>)}
    </select>
    <div className="customer-selector-footer" aria-live="polite">
      <span>{error || (busy ? 'در حال جستجو…' : result.total === 0 ? 'مشتری‌ای با این جستجو پیدا نشد.' : `${fa(result.total)} مشتری · صفحه ${fa(page)} از ${fa(pageCount)}`)}</span>
      <div className="customer-selector-pages">
        <button type="button" className="secondary" disabled={busy || page <= 1} onClick={() => setPage(current => current - 1)}>قبلی</button>
        <button type="button" className="secondary" disabled={busy || page >= pageCount} onClick={() => setPage(current => current + 1)}>بعدی</button>
      </div>
    </div>
  </div>;
}
