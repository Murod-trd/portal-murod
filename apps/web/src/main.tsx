import React,{useCallback,useEffect,useRef,useState,type FormEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {ArrowRight,KeyRound,LoaderCircle,Shield} from 'lucide-react';
import QRCode from 'qrcode';
import {api,ApiError,type Me} from './api';
import {dictionaries,errorText,type Key} from './i18n';
import {PortalProvider} from './context';
import {ConfirmHost,Field} from './ui';
import {DialogHost} from './forms';
import {CityArt} from './pages';
import {Layout,Brand,LocaleToggle} from './layout';
import './styles.css';
import {readLocalePreference,chooseLocale} from './locale-preference';

function loadLocale():'uz'|'ru'|'en'{return readLocalePreference(()=>localStorage.getItem('ti.locale'))??'uz';}
function App(){
  const[locale,setLocaleState]=useState<'uz'|'ru'|'en'>(loadLocale),[me,setMe]=useState<Me|null>(null),[loading,setLoading]=useState(true);
  const localePreference=useRef(readLocalePreference(()=>localStorage.getItem('ti.locale')));
  const applyLocale=useCallback((v:'uz'|'ru'|'en')=>{setLocaleState(v);document.documentElement.lang=v;},[]);
  const setLocale=useCallback((v:'uz'|'ru'|'en')=>{localePreference.current=v;applyLocale(v);try{localStorage.setItem('ti.locale',v);}catch{/* UI preference only. */}},[applyLocale]);
  const refreshMe=useCallback(async()=>{try{const value=await api<Me>('/me');setMe(value);applyLocale(chooseLocale(localePreference.current,value.locale));}catch(e){if(e instanceof ApiError&&e.status===401)setMe(null);else throw e;}finally{setLoading(false);}},[applyLocale]);
  useEffect(()=>{void refreshMe().catch(()=>setLoading(false));const expired=()=>setMe(null);window.addEventListener('portal-session-expired',expired);return()=>window.removeEventListener('portal-session-expired',expired);},[refreshMe]);
  const t=(key:Key)=>dictionaries[locale][key];
  useEffect(()=>{if(!me||me.mustChangePassword||(me.mfaRequired&&!me.mfaVerified))document.title=`${t(me?'security':'signIn')} · Toshkent Invest`;},[me,locale]);
  if(loading)return <div className="app-loading"><span className="brand-mark"/><LoaderCircle className="spin"/><p>{t('loading')}</p></div>;
  if(!me)return <Login locale={locale} setLocale={setLocale} onLogin={refreshMe}/>;
  if(me.mustChangePassword||(me.mfaRequired&&!me.mfaVerified))return <SecurityGate me={me} locale={locale} setLocale={setLocale} refreshMe={refreshMe}/>;
  return <PortalProvider me={me} locale={locale} setLocale={v=>{setLocale(v);void api('/me','PATCH',{locale:v}).catch(()=>undefined);}} refreshMe={refreshMe}><Layout onLogout={()=>setMe(null)}/><DialogHost/><ConfirmHost/></PortalProvider>;
}
function Login({locale,setLocale,onLogin}:{locale:'uz'|'ru'|'en';setLocale:(v:'uz'|'ru'|'en')=>void;onLogin:()=>Promise<void>}){
  const t=(k:Key)=>dictionaries[locale][k];const[username,setUsername]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');useEffect(()=>{void api('/auth/csrf').catch(()=>setError(t('error')));},[]);
  const submit=async(e:FormEvent)=>{e.preventDefault();setBusy(true);setError('');try{await api('/auth/login','POST',{username,password});setPassword('');await onLogin();}catch(e){setError(errorText(e instanceof ApiError?e.code:'REQUEST_FAILED',locale));}finally{setBusy(false);}};
  return <main className="auth-layout"><section className="auth-brand-panel"><div className="auth-wordmark"><span className="brand-mark light"><i/><i/><i/><i/></span><strong>Toshkent Invest</strong></div><div className="auth-quote"><span className="eyebrow">{t('portal')}</span><h1>{t('welcome')}</h1><p>{t('homeSubtitle')}</p></div><div className="auth-city"><CityArt/></div><span className="auth-panel-footer">{t('privatePortal')} · Asia/Tashkent</span></section><section className="auth-form-panel"><div className="auth-top"><span>{t('privatePortal')}</span><LocaleToggle locale={locale} setLocale={setLocale}/></div><form className="login-form" onSubmit={submit}><div className="auth-icon"><KeyRound size={26}/></div><h2>{t('loginTitle')}</h2><p>{t('loginSubtitle')}</p><Field label={t('username')} required><input autoFocus required autoComplete="username" value={username} maxLength={64} onChange={e=>setUsername(e.target.value)}/></Field><Field label={t('password')} required><input type="password" required autoComplete="current-password" value={password} maxLength={128} onChange={e=>setPassword(e.target.value)}/></Field>{error&&<div className="notice notice-error" role="alert">{error}</div>}<button className="button full" disabled={busy}>{busy?<LoaderCircle className="spin" size={18}/>:null}{t('signIn')}<ArrowRight size={17}/></button><p className="login-help">{t('loginHelp')}</p><div className="network-help"><Shield size={17}/>{t('networkHelp')}</div></form><span className="auth-copyright">Toshkent Invest · {t('portal')}</span></section></main>;
}
function SecurityGate({me,locale,setLocale,refreshMe}:{me:Me;locale:'uz'|'ru'|'en';setLocale:(v:'uz'|'ru'|'en')=>void;refreshMe:()=>Promise<void>}){
  const t=(k:Key)=>dictionaries[locale][k];const[current,setCurrent]=useState(''),[newPassword,setNewPassword]=useState(''),[repeat,setRepeat]=useState(''),[code,setCode]=useState(''),[uri,setUri]=useState(''),[qr,setQr]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{setError('');},[locale]);
  const invoke=async(path:string,body:unknown)=>{setBusy(true);setError('');try{const result=await api<{totpURI?:string}>(path,'POST',body);if(result.totpURI){setUri(result.totpURI);setQr(await QRCode.toDataURL(result.totpURI,{width:220,margin:1,color:{dark:'#153f38',light:'#ffffff'}}));setCurrent('');}else await refreshMe();}catch(e){setError(errorText(e instanceof ApiError?e.code:'REQUEST_FAILED',locale));}finally{setBusy(false);}};
  const submit=(e:FormEvent)=>{e.preventDefault();if(me.mustChangePassword){if(!current){setError(t('passwordCurrentRequired'));return;}if(Array.from(newPassword).length<12||Array.from(newPassword).length>128){setError(t('passwordLength'));return;}if(newPassword!==repeat){setError(t('passwordMismatch'));return;}void invoke('/me/change-password',{currentPassword:current,newPassword});}else if(!me.mfaEnrolled&&!uri)void invoke('/me/mfa/start',{password:current});else void invoke('/me/mfa/verify',{code});};
  return <main className="security-layout"><div className="security-top"><Brand subtitle={t('portal')}/><LocaleToggle locale={locale} setLocale={setLocale}/></div><form className="security-card panel" noValidate={me.mustChangePassword} onSubmit={submit}><div className="auth-icon"><Shield size={27}/></div><div className="security-steps"><span className={me.mustChangePassword?'current':'done'}>1</span><i/><span className={!me.mustChangePassword?'current':''}>2</span></div><h1>{t(me.mustChangePassword?'passwordChange':me.mfaEnrolled?'mfaLogin':'mfaTitle')}</h1><p>{t(me.mustChangePassword?'passwordChangeDesc':me.mfaEnrolled?'mfaLoginDesc':'mfaDescription')}</p>{!me.mustChangePassword&&me.mfaEnrolled&&<p>{t('mfaHelp')}</p>}{me.mustChangePassword?<><Field label={t('currentPassword')}><input type="password" required autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)}/></Field><Field label={t('newPassword')}><input aria-label={t('newPassword')} aria-describedby="new-password-requirements" type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={newPassword} onChange={e=>setNewPassword(e.target.value)}/><small id="new-password-requirements">{t('passwordRequirements')}</small></Field><Field label={t('repeatPassword')}><input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={repeat} onChange={e=>setRepeat(e.target.value)}/></Field></>:!me.mfaEnrolled&&!uri?<Field label={t('mfaPassword')}><input type="password" required autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)}/></Field>:<>{qr&&<div className="qr-setup"><img src={qr} alt={t('mfaQr')}/><a href={uri}>{t('setupKey')} ↗</a></div>}<Field label={t('otpCode')}><input className="otp-input" autoFocus required inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></Field></>}{error&&<div className="notice notice-error" role="alert">{error}</div>}<button className="button full" disabled={busy}>{busy&&<LoaderCircle size={18} className="spin"/>}{t(me.mustChangePassword?'continue':me.mfaEnrolled||uri?'verify':'mfaStart')}<ArrowRight size={17}/></button><button type="button" className="text-button logout-in-security" onClick={()=>void api('/auth/logout','POST',{}).then(refreshMe)}>{t('logout')}</button></form><p className="muted small">{t('support')}</p></main>;
}


createRoot(document.getElementById('root')!).render(<React.StrictMode><BrowserRouter><App/></BrowserRouter></React.StrictMode>);
