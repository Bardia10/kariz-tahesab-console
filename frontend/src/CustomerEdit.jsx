import { useEffect, useRef, useState } from 'react';

const aliases = {
  full_name: ['Name', 'name', 'FullName', 'Name_Moshtari', 'MoshtariName'],
  group_name: ['GroupName', 'GoroupName', 'Gorooh', 'Group', 'GoroohName', 'Group_Name'],
  mobile: ['Mobile', 'mobile', 'Tel', 'Phone', 'MobileNo'],
  address: ['Address', 'address'],
  national_id: ['CodeMelli', 'CodeMeli', 'NationalCode', 'Code_Meli', 'national_id'],
};
const birthdateAliases = ['BDate', 'BirthDate', 'TarikhTavalod', 'Tarikh_Tavalod'];
const birthdateField = row => birthdateAliases.find(field => Object.prototype.hasOwnProperty.call(row || {}, field));
const codeOf = row => row?.Code ?? row?.MCode ?? row?.code ?? row?.MoshtariCode ?? '';
const has = (row, key) => aliases[key].find(name => Object.prototype.hasOwnProperty.call(row || {}, name));
const valueOf = (row, key) => { const field = has(row, key); return field === undefined || row[field] == null || String(row[field]).toLowerCase() === 'null' ? '' : String(row[field]); };
const groupName = group => group?.Name ?? group?.name ?? group?.Gorooh ?? group?.GoroohName ?? group?.GroupName ?? '';
const usable = value => value != null && String(value).toLowerCase() !== 'null';
const asciiDigits = value => String(value).replace(/[۰-۹٠-٩]/g, digit => String(digit.charCodeAt(0) >= 0x06f0 && digit.charCodeAt(0) <= 0x06f9 ? digit.charCodeAt(0) - 0x06f0 : digit.charCodeAt(0) - 0x0660));

async function postOperation(body) {
  const response = await fetch('/api/operations', {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || 'پاسخ سرور دریافت نشد؛ نتیجه عملیات نامشخص است.');
  return result;
}

export function CustomerEdit({ customer, groups = [], onClose, onSaved }) {
  const dialog = useRef(null), previousFocus = useRef(null), pending = useRef(false), sent = useRef(false), operationId = useRef(null), onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [form, setForm] = useState(() => Object.fromEntries(Object.keys(aliases).map(key => [key, valueOf(customer, key)])));
  const [changed, setChanged] = useState(() => new Set());
  const [busy, setBusy] = useState(false), [result, setResult] = useState(null);
  const customerCode = codeOf(customer);
  const put = (key, value) => { setForm(current => ({ ...current, [key]: value })); setChanged(current => new Set(current).add(key)); };
  const close = () => { if (!pending.current) onClose?.(); };

  useEffect(() => {
    previousFocus.current = document.activeElement;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const frame = requestAnimationFrame(() => dialog.current?.querySelector('input:not([disabled]),select:not([disabled]),button:not([disabled])')?.focus());
    const onKeyDown = event => {
      if (event.key === 'Escape') { event.preventDefault(); if (!pending.current) onCloseRef.current?.(); return; }
      if (event.key !== 'Tab' || !dialog.current) return;
      const items = [...dialog.current.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(el => el.offsetParent !== null);
      if (!items.length) { event.preventDefault(); dialog.current.focus(); return; }
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', onKeyDown); document.body.style.overflow = priorOverflow; previousFocus.current?.focus?.(); };
  }, []);

  async function submit(event) {
    event.preventDefault();
    const numericCode = Number(asciiDigits(customerCode));
    if (sent.current || !Number.isSafeInteger(numericCode)) return;
    const payload = { customer_code: numericCode };
    for (const key of Object.keys(aliases)) {
      const sourceField = has(customer, key);
      if (changed.has(key) || (sourceField !== undefined && usable(customer[sourceField]))) payload[key] = form[key];
    }
    const summary = `ویرایش اطلاعات مشتری در ته حساب آزمایشی؟\nکد مشتری: ${customerCode}\nنام: ${usable(payload.full_name) ? payload.full_name : 'در منبع موجود نیست'}\nگروه: ${usable(payload.group_name) ? payload.group_name : 'در منبع موجود نیست'}\nموبایل: ${usable(payload.mobile) ? payload.mobile : 'در منبع موجود نیست'}\nنشانی: ${usable(payload.address) ? payload.address : 'در منبع موجود نیست'}\nکد ملی: ${usable(payload.national_id) ? payload.national_id : 'در منبع موجود نیست'}\n\nپس از تأیید، درخواست به منبع ارسال می‌شود.`;
    if (!window.confirm(summary)) return;
    sent.current = true; pending.current = true; setBusy(true);
    try {
      const id = operationId.current || (operationId.current = crypto.randomUUID());
      const op = await postOperation({ operation_id: id, kind: 'customer_edit', payload });
      setResult(op); onSaved?.(op);
    } catch (error) {
      setResult({ status: 'uncertain', message: `${error.message} برای جلوگیری از ثبت تکراری، دوباره ارسال نکنید؛ وضعیت را در «عملیات این پنل» بررسی کنید.` });
    } finally { setBusy(false); pending.current = false; }
  }

  const currentGroup = form.group_name;
  const listedGroups = groups.map(groupName).filter(Boolean);
  return <div className="overlay customer-edit-modal" onMouseDown={event => event.target === event.currentTarget && close()}>
    <section ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby="customer-edit-title" tabIndex={-1}>
      <div className="modalhead"><div><small>ویرایش مستقیم در ته حساب آزمایشی</small><h2 id="customer-edit-title">ویرایش اطلاعات مشتری</h2></div><button className="icon" type="button" disabled={busy} aria-label="بستن پنجره" onClick={close}>×</button></div>
      <form className="creategrid" onSubmit={submit}>
        <label className="field">کد مشتری<input value={String(customerCode)} disabled aria-label="کد ثابت مشتری"/></label>
        <label className="field">نام کامل *<input required maxLength={150} value={form.full_name} onChange={event => put('full_name', event.target.value)}/></label>
        <label className="field">گروه مشتری<select value={form.group_name} onChange={event => put('group_name', event.target.value)}><option value="">انتخاب گروه منبع</option>{currentGroup && !listedGroups.includes(currentGroup) && <option value={currentGroup}>{currentGroup}</option>}{groups.map((group, index) => <option key={`${groupName(group)}-${index}`} value={groupName(group)}>{groupName(group)}</option>)}</select></label>
        <label className="field">شماره تماس<input inputMode="tel" maxLength={32} value={form.mobile} onChange={event => put('mobile', event.target.value)}/></label>
        <label className="field">کد ملی<input inputMode="numeric" maxLength={32} value={form.national_id} onChange={event => put('national_id', event.target.value)}/></label>
        <label className="field span2">نشانی<input maxLength={300} value={form.address} onChange={event => put('address', event.target.value)}/></label>
        {birthdateField(customer) && <label className="field">تاریخ تولد ثبت‌شده<input readOnly value={customer[birthdateField(customer)] == null ? '' : String(customer[birthdateField(customer)])}/></label>}
        <div className="span2 createactions"><span>اگر نتیجه نامشخص باشد، ارسال دوباره انجام نمی‌شود؛ وضعیت عملیات را بررسی کنید.</span><button className="primary" disabled={busy || !!result}>{busy ? 'در حال ثبت…' : 'تأیید و ذخیره تغییرات'}</button></div>
      </form>
      {result && <div className="opresult"><span className={`opstatus ${result.status || 'uncertain'}`}>{result.status === 'verified' ? 'ثبت و بررسی شد' : result.status === 'failed' ? 'ثبت نشد' : 'نتیجه نیازمند بررسی است'}</span>{result.message && <small className="opmessage">{result.message}</small>}</div>}
    </section>
  </div>;
}
