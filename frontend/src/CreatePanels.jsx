import { useEffect, useRef, useState } from 'react';
import CustomerSelector from './CustomerSelector.jsx';

const toEn=(v='')=>String(v).replace(/[\u06f0-\u06f9\u0660-\u0669]/g,d=>String(d.charCodeAt(0)-(d.charCodeAt(0)>=0x06f0?0x06f0:0x0660))).replace(/\u066b/g,'.').replace(/[\u066c,]/g,'');
const fa=v=>String(v??'').replace(/[0-9]/g,d=>String.fromCharCode(0x06f0+Number(d)));
const grouped=v=>{const x=toEn(v).replace(/,/g,'').replace(/[^0-9.]/g,'');const [whole,frac]=x.split('.');const digits=(whole||'').replace(/\B(?=(\d{3})+(?!\d))/g,'\u066c');return fa(digits)+(frac!==undefined?'\u066b'+fa(frac):'')};
const today=()=>new Intl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()).replaceAll('\u066b','/').replaceAll('-','/');
async function api(url,opts={}){const res=await fetch(url,{credentials:'include',...opts,headers:{...(opts.body?{'Content-Type':'application/json'}:{}),...opts.headers}});const body=await res.json().catch(()=>({}));if(!res.ok)throw Error(body.detail||'درخواست انجام نشد.');return body}

const creationTitle=active=>active==='payments'?'ثبت پرداخت یا برداشت':active==='deliveries'?'ثبت دریافت یا تحویل فیزیکی':'ایجاد معامله حسابداری';
export function CreationModal({active,customers,selected,catalog,onSaved,onClose}){
 const dialog=useRef(null),previousFocus=useRef(null),pending=useRef(false),onCloseRef=useRef(onClose);
 onCloseRef.current=onClose;
 const [busy,setBusy]=useState(false);
 const close=()=>{if(!pending.current)onCloseRef.current?.()};
 useEffect(()=>{
  previousFocus.current=document.activeElement;
  const priorOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
  const focusFirst=()=>dialog.current?.querySelector('input:not([disabled]),select:not([disabled]),button:not([disabled]),textarea:not([disabled]),[tabindex="0"]')?.focus();
  const frame=requestAnimationFrame(focusFirst);
  const onKeyDown=e=>{
   if(e.key==='Escape'){e.preventDefault();if(!pending.current)onCloseRef.current?.();return}
   if(e.key!=='Tab'||!dialog.current)return;
   const focusable=[...dialog.current.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(el=>el.offsetParent!==null);
   if(!focusable.length){e.preventDefault();dialog.current.focus();return}
   const first=focusable[0],last=focusable[focusable.length-1];
   if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
  };
  document.addEventListener('keydown',onKeyDown);
  return()=>{cancelAnimationFrame(frame);document.removeEventListener('keydown',onKeyDown);document.body.style.overflow=priorOverflow;previousFocus.current?.focus?.()}
 },[]);
 return <div className="overlay creation-modal" onMouseDown={e=>e.target===e.currentTarget&&close()}><section ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby="creation-modal-title" tabIndex={-1}>
  <div className="modalhead"><div><small>ثبت مستقیم در ته حساب آزمایشی</small><h2 id="creation-modal-title">{creationTitle(active)}</h2></div><button className="icon" type="button" aria-label="بستن پنجره" disabled={busy} onClick={close}>×</button></div>
  <CreatePanel active={active} customers={customers} selected={selected} catalog={catalog} onSaved={onSaved} onBusyChange={value=>{pending.current=value;setBusy(value)}}/>
 </section></div>
}

export function CustomerCreate({groups=[],onClose,onSaved}){const opId=useRef(null),sending=useRef(false);const [form,setForm]=useState({full_name:'',group_name:'',mobile:'',address:'',national_id:'',birthdate:''});const [busy,setBusy]=useState(false);const [result,setResult]=useState(null);const close=()=>{if(!sending.current)onClose?.()};const put=(k,v)=>setForm(s=>({...s,[k]:v}));const groupName=g=>g.Name||g.name||g.Gorooh||g.GoroohName||g.GroupName||'';async function submit(e){e.preventDefault();if(!window.confirm(`ایجاد مشتری در ته حساب آزمایشی؟\nنام: ${form.full_name}\nگروه: ${form.group_name||'انتخاب نشده'}\nاین کار پس از تأیید به منبع ارسال می‌شود.`))return;if(sending.current)return;sending.current=true;setBusy(true);try{const id=opId.current||(opId.current=crypto.randomUUID());const op=await api('/api/operations',{method:'POST',body:JSON.stringify({operation_id:id,kind:'customer_create',payload:form})});setResult(op);onSaved?.(op)}catch(err){setResult({status:'uncertain',message:err.message})}finally{setBusy(false);sending.current=false}}return <div className="overlay" onMouseDown={e=>e.target===e.currentTarget&&close()}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="customer-create-title"><div className="modalhead"><div><small>ثبت مستقیم در ته حساب آزمایشی</small><h2 id="customer-create-title">مشتری جدید</h2></div><button className="icon" disabled={busy} aria-label="بستن پنجره" onClick={close}>×</button></div><form className="creategrid" onSubmit={submit}><label className="field">نام کامل *<input required maxLength={150} value={form.full_name} onChange={e=>put('full_name',e.target.value)}/></label><label className="field">گروه مشتری *<select required value={form.group_name} onChange={e=>put('group_name',e.target.value)}><option value="">انتخاب گروه منبع</option>{groups.map((g,i)=><option key={i} value={groupName(g)}>{groupName(g)}</option>)}</select></label><label className="field">شماره تماس<input inputMode="tel" maxLength={32} value={form.mobile} onChange={e=>put('mobile',e.target.value)}/></label><label className="field">کد ملی<input inputMode="numeric" maxLength={32} value={form.national_id} onChange={e=>put('national_id',e.target.value)}/></label><label className="field span2">نشانی<input maxLength={300} value={form.address} onChange={e=>put('address',e.target.value)}/></label><label className="field">تاریخ تولد شمسی<input placeholder="اختیاری؛ سال/ماه/روز" value={form.birthdate} onChange={e=>put('birthdate',e.target.value)}/></label><div className="span2 createactions"><span>کد را ته حساب اختصاص می‌دهد. نتیجه نامشخص دوباره ارسال نمی‌شود.</span><button className="primary" disabled={busy||!!result}>{busy?'در حال ثبت…':'تأیید و ایجاد مشتری'}</button></div></form>{result&&<OperationResult op={result}/>}</section></div>}

export function CreatePanel({active,customers,selected,catalog,onSaved,onBusyChange}){
 const opId=useRef(null),sending=useRef(false);
 const [customer,setCustomer]=useState(selected||''),[mode,setMode]=useState(active==='payments'?'cash':active==='deliveries'?'gold':'trade');
 const [form,setForm]=useState({amount_toman:'',unit_price_toman:'',grams:'',count:'1',description:'',date:today(),direction:'deposit',bank_name:'',reference:'',product_key:''});
 const [busy,setBusy]=useState(false),[result,setResult]=useState(null);const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{setCustomer(selected||'')},[selected]);
 const put=(k,v)=>setForm(s=>({...s,[k]:v}));
 const goldModes=(catalog?.mapping?.source_gold_modes||[]).filter(p=>p.verified&&p.physical_enabled&&(active==='deliveries'||p.trade_enabled));
 const products=[...(catalog?.mapping?.products||[]).filter(p=>p.verified&&['coin','bar'].includes(p.source_family)),...goldModes];
 const product=products.find(p=>p.key===form.product_key)||products[0];
 useEffect(()=>{if(product&&form.product_key!==product.key)put('product_key',product.key)},[product?.key]);
 const numeric=v=>toEn(v).replace(/[\u066c,\s]/g,'').replace('\u066b','.');
 const totalToman=()=>{try{const rate=numeric(form.unit_price_toman);if(product?.source_family==='gold'){const grams=numeric(form.grams);if(!/^\d+(\.\d{1,4})?$/.test(grams)||!/^\d+(\.\d)?$/.test(rate))return '';const [gw,gd='']=grams.split('.'),[rw,rd='']=rate.split('.');const weight=BigInt(gw+gd.padEnd(4,'0')),price=BigInt(rw)*10n+BigInt((rd+'0').slice(0,1));const [fw,fd='']=String(product?.mesghal_to_gram_factor||4.3318).split('.'),factor=BigInt(fw+fd.padEnd(4,'0'));const rial=form.price_basis==='gram'?(weight*price+5000n)/10000n:(weight*price+factor/2n)/factor;const toman=rial/10n,rem=rial%10n;return fa(toman.toLocaleString('en-US')+(rem?'.'+rem:''))}if(!/^\d+(.\d)?$/.test(rate)||!/^\d+$/.test(numeric(form.count)))return '';const [whole,frac='']=rate.split('.');const rial=(BigInt(whole)*10n+BigInt((frac+'0').slice(0,1)))*BigInt(numeric(form.count));const q=rial/10n,rem=rial%10n;return fa(q.toLocaleString('en-US')+(rem?`.${rem}`:''))}catch{return ''}};
 async function submit(e){
  e.preventDefault();if(!customer||sending.current)return;
  const count=numeric(form.count),bank=active==='payments'&&mode==='bank';let kind,payload,summary;
  if(active==='payments'){
   kind=bank?'bank_payment':'cash_payment';payload={customer_code:Number(customer),direction:form.direction,amount_toman:numeric(form.amount_toman),date:form.date,description:form.description,...(bank?{bank_name:form.bank_name,reference:form.reference}:{})};
   summary=`مشتری: کد ${customer}\nنوع: ${form.direction==='deposit'?'دریافت وجه از مشتری':'پرداخت وجه به مشتری'} (${bank?`بانکی · ${form.bank_name}`:'نقدی'})${bank?`\nشماره پیگیری: ${form.reference}`:''}\nمبلغ: ${form.amount_toman} تومان\nتاریخ: ${form.date}`;
  }else if(active==='deliveries'&&(mode==='gold'||mode==='abshode')){
   const goldProduct=mode==='abshode'?goldModes.find(p=>p.key==='gold_abshode_750'):(catalog?.mapping?.products||[]).find(p=>p.key==='gold_750');
   kind='physical_gold';payload={customer_code:Number(customer),product_key:goldProduct?.key||'gold_750',direction:form.direction==='deposit'?'incoming':'outgoing',grams:numeric(form.grams),date:form.date,description:form.description};summary=`${form.direction==='deposit'?'دریافت کالا از مشتری':'تحویل کالا به مشتری'}
مشتری: کد ${customer}
محصول: ${goldProduct?.title_fa}\nحالت سند ته حساب: ${goldProduct?.source_mode_label||goldProduct?.source_name}\nوزن: ${form.grams} گرم
بدون قیمت فروش
تاریخ: ${form.date}`;
  }else if(active==='deliveries'){
   kind=`physical_${product?.source_family}`;payload={customer_code:Number(customer),product_key:product?.key,direction:form.direction==='deposit'?'incoming':'outgoing',count:Number(count),date:form.date,description:form.description};
   summary=`${form.direction==='deposit'?'دریافت کالا از مشتری':'تحویل کالا به مشتری'}\nمشتری: کد ${customer}\nمحصول: ${product?.title_fa}\nشناسه اصلی ته حساب: ${product?.source_identifier}\nتعداد: ${fa(count)}\nوزن هر واحد: ${fa(product?.source_weight)} گرم\nبدون قیمت\nتاریخ: ${form.date}`;
  }else if(trade&&product?.source_family==='gold'){
   const grams=numeric(form.grams),basis=form.price_basis||'mesghal';kind='trade_gold';payload={customer_code:Number(customer),product_key:product.key,direction:form.direction==='deposit'?'customer_buys':'customer_sells',grams,price_basis:basis,price_toman:numeric(form.unit_price_toman),date:form.date,description:form.description};summary=`${form.direction==='deposit'?'خرید مشتری از ما':'فروش مشتری به ما'}\nمشتری: کد ${customer}\nثبت حسابداری ته حساب: ${form.direction==='deposit'?'فروش':'خرید'}\nمحصول: ${product.title_fa}\nوزن: ${fa(grams)} گرم\n${basis==='mesghal'?'مظنه بر مثقال':'قیمت هر گرم'}: ${form.unit_price_toman} تومان\nمبلغ کل: ${totalToman()} تومان\nتاریخ: ${form.date}`;
  }else{
   kind=`trade_${product?.source_family}`;payload={customer_code:Number(customer),product_key:product?.key,direction:form.direction==='deposit'?'customer_buys':'customer_sells',count:Number(count),unit_price_toman:numeric(form.unit_price_toman),date:form.date,description:form.description};
   summary=`${form.direction==='deposit'?'خرید مشتری از ما':'فروش مشتری به ما'}\nمشتری: کد ${customer}\nثبت حسابداری ته حساب: ${form.direction==='deposit'?'فروش':'خرید'}\nمحصول: ${product?.title_fa}\nشناسه اصلی ته حساب: ${product?.source_identifier}\nتعداد: ${fa(count)}\nقیمت هر واحد: ${form.unit_price_toman} تومان\nمبلغ کل: ${totalToman()} تومان\nتاریخ: ${form.date}`;
  }
  if(!window.confirm(`خلاصه ثبت در ته حساب آزمایشی:\n${summary}\n\nپس از تأیید، درخواست فقط یک‌بار ارسال می‌شود.`))return;
  sending.current=true;setBusy(true);onBusyChange?.(true);
  try{const id=opId.current||(opId.current=crypto.randomUUID());const op=await api('/api/operations',{method:'POST',body:JSON.stringify({operation_id:id,kind,payload})});if(mounted.current){setResult(op);onSaved?.(op)}}catch(err){if(mounted.current)setResult({status:'uncertain',message:`ارتباط قطع شد: ${err.message} نتیجه را در «عملیات این پنل» بررسی کنید.`})}finally{sending.current=false;if(mounted.current){setBusy(false);onBusyChange?.(false)}}
 }
 const trade=active==='trades',delivery=active==='deliveries',gold=delivery&&(mode==='gold'||mode==='abshode'),goldTrade=trade&&product?.source_family==='gold',bank=active==='payments'&&mode==='bank';
 const canSubmit=!!customer&&!busy&&!result&&(active==='payments'?(!bank||!!form.bank_name&&!!form.reference.trim()):gold?!!form.grams:!!product&&(goldTrade?!!form.grams&&!!form.unit_price_toman:true));
 return <div className="card createcard">
  <div className="cardhead"><div><b>{active==='payments'?'ثبت پرداخت یا برداشت':delivery?'ثبت دریافت یا تحویل فیزیکی':'ایجاد معامله حسابداری'}</b><small>{trade?'خرید مشتری از ما، فروش ته حساب است؛ فروش مشتری به ما، خرید ته حساب است.':delivery?(gold?'رسید طلای متفرقه بدون قیمت فروش.':'سند فیزیکی است و قیمت ندارد.'):(bank?'سند بانکی حسابداری است؛ انتقال واقعی وجه انجام نمی‌شود.':'سند نقدی حسابداری.')}</small></div>{trade&&<em className="unverified">معامله آبشده فعال؛ معامله طلای متفرقه غیرفعال</em>}</div>
  {trade&&<div className="notice warningnotice">معامله طلای آبشده ۷۵۰ با مظنه یا قیمت هر گرم تأیید شده است. معامله طلای متفرقه تا زمانی که ته حساب نوع آن را در بازخوانی مشخص نکند، غیرفعال می‌ماند.</div>}
  <div className="createbody"><div className="createchoices">{active==='payments'?<><button type="button" className={mode==='cash'?'selected':''} onClick={()=>setMode('cash')}>نقدی</button><button type="button" className={mode==='bank'?'selected':''} disabled={!catalog?.source_catalogues?.bank_accounts?.length} onClick={()=>setMode('bank')}>بانکی</button></>:delivery?<><button type="button" className={mode==='gold'?'selected':''} onClick={()=>setMode('gold')}>دریافت یا تحویل طلای متفرقه ۷۵۰</button><button type="button" className={mode==='abshode'?'selected':''} onClick={()=>setMode('abshode')}>دریافت یا تحویل آبشده ۷۵۰</button><button type="button" className={mode==='physical'?'selected':''} onClick={()=>setMode('physical')}>دریافت یا تحویل سکه/شمش</button></>:<><button type="button" className={form.direction==='deposit'?'selected':''} onClick={()=>put('direction','deposit')}>خرید مشتری از ما</button><button type="button" className={form.direction==='payout'?'selected':''} onClick={()=>put('direction','payout')}>فروش مشتری به ما</button></>}</div>
   <form className="creategrid" onSubmit={submit}>
    <CustomerSelector items={customers} value={customer} onChange={setCustomer}/>
    {((trade&&!goldTrade)||(delivery&&!gold))&&<label className="field">محصول نگاشت‌شده *<select required value={product?.key||''} onChange={e=>put('product_key',e.target.value)}><option value="">انتخاب محصول تأییدشده</option>{products.map(p=><option key={p.key} value={p.key}>{p.title_fa} · {p.source_identifier||p.source_mode_label||p.source_name}</option>)}</select><small className="muted">شناسه یا حالت اصلی منبع برای ثبت ارسال می‌شود؛ عنوان فارسی فقط برای نمایش است.</small></label>}
    {active==='payments'?<><label className="field">جهت سند<select value={form.direction} onChange={e=>put('direction',e.target.value)}><option value="deposit">دریافت وجه از مشتری</option><option value="payout">پرداخت وجه به مشتری</option></select></label>{bank&&<><label className="field">حساب بانکی منبع *<select required value={form.bank_name} onChange={e=>put('bank_name',e.target.value)}><option value="">انتخاب از فهرست ته حساب</option>{(catalog?.source_catalogues?.bank_accounts||[]).map((a,i)=>{const n=a.Name_Bank||a.Name||'';return <option key={`${n}-${i}`} value={n}>{n}</option>})}</select></label><label className="field">شماره پیگیری *<input required maxLength={100} value={form.reference} onChange={e=>put('reference',e.target.value)}/></label></>}<label className="field">مبلغ (تومان) *<input required inputMode="decimal" value={form.amount_toman} onChange={e=>put('amount_toman',grouped(e.target.value))}/></label></>:<>
     {((trade&&!goldTrade)||(delivery&&!gold))&&<label className="field">تعداد *<input required inputMode="numeric" value={form.count} onChange={e=>put('count',grouped(e.target.value))}/></label>}
     {trade&&!goldTrade&&<><label className="field">قیمت هر واحد (تومان) *<input required inputMode="decimal" value={form.unit_price_toman} onChange={e=>put('unit_price_toman',grouped(e.target.value))}/></label><label className="field">مبلغ کل<span className="inputreadout">{totalToman()||'—'} تومان</span></label></>}
     {(gold||goldTrade)&&<label className="field">وزن (گرم) *<input required inputMode="decimal" value={form.grams} onChange={e=>put('grams',grouped(e.target.value))}/></label>}{goldTrade&&<><label className="field">مبنای قیمت<select value={form.price_basis||'mesghal'} onChange={e=>put('price_basis',e.target.value)}><option value="mesghal">مظنه (تومان بر مثقال)</option><option value="gram">قیمت هر گرم (تومان)</option></select></label><label className="field">قیمت *<input required inputMode="decimal" value={form.unit_price_toman} onChange={e=>put('unit_price_toman',grouped(e.target.value))}/></label><label className="field">مبلغ کل (تومان)<span className="inputreadout">{totalToman()||'—'} تومان</span></label></>}
     {delivery&&<label className="field">جهت فیزیکی<select value={form.direction} onChange={e=>put('direction',e.target.value)}><option value="deposit">دریافت کالا از مشتری</option><option value="payout">تحویل کالا به مشتری</option></select></label>}
    </>}
    <label className="field">تاریخ شمسی *<input required placeholder="۱۴۰۵/۰۷/۱۸" value={form.date} onChange={e=>put('date',e.target.value)}/></label><label className="field span2">شرح<input maxLength={300} value={form.description} onChange={e=>put('description',e.target.value)}/></label>
    <div className="span2 createactions"><span>{trade?'قیمت واحد به تومان وارد می‌شود؛ تبدیل دقیق به ریال در سرویس انجام می‌شود.':delivery?(gold?'رسید کالا سند فروش نیست و قیمت ندارد.':'مانده محصول قبل و بعد کنترل می‌شود؛ چون ته حساب ممکن است فیلد مانده صفر را حذف کند، پس از تحویل باید مانده مثبت بماند.'):'مبلغ تومان است و یک‌بار در API به ریال تبدیل می‌شود.'}</span><button className="primary" disabled={!canSubmit}>{busy?'در حال ثبت…':'بررسی خلاصه و ثبت'}</button></div>
   </form>{result&&<OperationResult op={result}/>}
  </div>
 </div>
}

export function OperationList({items,setItems,onVerified}){const [busy,setBusy]=useState('');async function refresh(){const r=await api('/api/operations');setItems(r.items||[])}async function verify(id){setBusy(id);try{const op=await api(`/api/operations/${id}/verify`,{method:'POST',body:'{}'});setItems(xs=>xs.map(x=>x.operation_id===id?op:x));onVerified?.(op)}finally{setBusy('')}}return <div className="card operationslist"><div className="cardhead"><div><b>عملیات ثبت‌شده</b><small>نتیجه نامشخص از اینجا بررسی می‌شود؛ ارسال دوباره خودکار انجام نمی‌شود.</small></div><button className="secondary" onClick={refresh}>تازه‌سازی</button></div>{!items.length?<div className="empty">هنوز عملیاتی از این پنل ثبت نشده است.</div>:items.map(op=><article className="operationrow" key={op.operation_id}><div><b>{op.kind==='customer_create'?'ایجاد مشتری':op.kind==='customer_edit'?'ویرایش اطلاعات مشتری':op.kind==='cash_payment'?'سند وجه نقد':op.kind==='bank_payment'?'سند وجه بانکی':op.kind.startsWith('physical_')?'سند فیزیکی کالا':op.kind.startsWith('trade_')?'معامله حسابداری':'دریافت طلای متفرقه ۷۵۰'}</b><small>شناسه محلی: {op.operation_id}</small><small>{op.created_at}</small>{op.message&&<p>{op.message}</p>}</div><OperationResult op={op}/>{['uncertain','submitting'].includes(op.status)&&<button className="secondary" disabled={busy===op.operation_id} onClick={()=>verify(op.operation_id)}>{busy===op.operation_id?'در حال بررسی…':'بررسی از منبع'}</button>}</article>)}</div>}

function OperationResult({op}){const labels={submitting:'در حال ثبت',verified:'ثبت و بررسی شد',failed:'ثبت نشد',uncertain:'نتیجه نیازمند بررسی است'};return <div className="opresult"><span className={`opstatus ${op.status||'uncertain'}`}>{labels[op.status]||labels.uncertain}</span>{op.message&&<small className="opmessage">{op.message}</small>}</div>}
