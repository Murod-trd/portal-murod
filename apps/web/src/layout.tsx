import {useEffect,useState,useRef,type ReactNode} from 'react';
import {Link,NavLink,Navigate,Route,Routes,useLocation,useNavigate} from 'react-router-dom';
import {ArrowLeft,ArrowUp,Search,LogOut,Menu,X,Globe2,ChevronDown,Shield,Users,LayoutDashboard,Newspaper,Network,FileText,UserRound,Settings2,LifeBuoy,ChevronRight} from 'lucide-react';
import {api,isManager} from './api';
import {usePortal} from './context';
import {Avatar} from './ui';
import type {Key} from './i18n';
import {HomePage,NewsPage,ArticlePage,EmployeesPage,EmployeePage,StructurePage,DocumentsPage,DocumentPage,HrPage,TrashPage,ProfilePage,AdminPage,SearchPage} from './pages';
export function LocaleToggle({locale,setLocale}:{locale:'uz'|'ru'|'en';setLocale:(v:'uz'|'ru'|'en')=>void}){return <label className="locale-picker"><Globe2 size={16}/><select aria-label={locale==='ru'?'Язык интерфейса':locale==='en'?'Interface language':'Interfeys tili'} value={locale} onChange={e=>setLocale(e.target.value as 'uz'|'ru'|'en')}><option value="uz">O‘zbekcha</option><option value="ru">Русский</option><option value="en">English</option></select></label>;}
export function Brand({subtitle='Jamoa portali'}:{subtitle?:string}){return <Link className="brand" to="/"><span className="brand-mark"><i/><i/><i/><i/></span><span><strong>Toshkent Invest</strong><small>{subtitle}</small></span></Link>;}

export function Layout({onLogout,children}:{onLogout:()=>void;children?:ReactNode}){
  const {t,locale,setLocale,me,showError}=usePortal();
  const navigate=useNavigate(),location=useLocation();
  const initialHistoryIndex=useRef(Number.isInteger(window.history.state?.idx)?window.history.state.idx:0);
  const [showTop,setShowTop]=useState(false);
  useEffect(()=>{const onScroll=()=>setShowTop(window.scrollY>400);onScroll();window.addEventListener('scroll',onScroll,{passive:true});return()=>window.removeEventListener('scroll',onScroll);},[]);
  const back=()=>{const index=window.history.state?.idx;if(Number.isInteger(index)&&index>initialHistoryIndex.current){navigate(-1);return;}const parent=location.pathname.startsWith('/employees/')?'/employees':location.pathname.startsWith('/documents/')?'/documents':location.pathname.startsWith('/news/')?'/news':'/';navigate(parent);};
  const toTop=()=>{window.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});document.getElementById('portal-main')?.focus({preventScroll:true});};
  const [search,setSearch]=useState(''),[menu,setMenu]=useState(false);const menuButton=useRef<HTMLButtonElement>(null),menuClose=useRef<HTMLButtonElement>(null);
  const nav=[{to:'/',key:'home' as Key,icon:LayoutDashboard},{to:'/news',key:'news' as Key,icon:Newspaper},{to:'/employees',key:'people' as Key,icon:Users},{to:'/structure',key:'structure' as Key,icon:Network},{to:'/documents',key:'documents' as Key,icon:FileText}];
  const current=nav.find(item=>item.to==='/'?location.pathname==='/':location.pathname.startsWith(item.to));
  const section=current?.key??(location.pathname.startsWith('/hr')?'management':location.pathname.startsWith('/admin')?'admin':location.pathname.startsWith('/profile')?'profile':location.pathname.startsWith('/trash')?'trash':'search');
  useEffect(()=>setMenu(false),[location.pathname]);
  useEffect(()=>{if(menu)menuClose.current?.focus();},[menu]);
  const closeMenu=()=>{setMenu(false);menuButton.current?.focus();};
  const logout=async()=>{try{await api('/auth/logout','POST',{});onLogout();navigate('/');}catch(e){showError(e);}};
  return <div className="app-shell">
    {showTop&&<button type="button" className="back-to-top" onClick={toTop} aria-label={t('backToTop')} title={t('backToTop')}><ArrowUp size={22}/><span>{t('backToTop')}</span></button>}
    <a className="skip-link" href="#portal-main">{t('skipToContent')}</a>
    <aside id="portal-navigation" className={'app-sidebar '+(menu?'is-open':'')} onKeyDown={e=>{if(menu&&e.key==='Escape'){e.preventDefault();closeMenu();}}}>
      <Brand subtitle={t('portal')}/>
      <button ref={menuClose} className="icon-button sidebar-close" aria-label={t('close')} onClick={closeMenu}><X size={20}/></button>
      <div className="sidebar-caption">{t('workspace')}</div>
      <nav className="primary-nav" aria-label={t('navigation')}>
        {nav.map(({to,key,icon:Icon})=><NavLink end={to==='/'} key={to} to={to}><Icon size={19}/><span>{t(key)}</span><ChevronRight className="nav-arrow" size={15}/></NavLink>)}
      </nav>
      <div className="sidebar-section">
        <div className="sidebar-caption">{t('personalSpace')}</div>
        <nav className="primary-nav" aria-label={t('personalSpace')}>
          <NavLink to="/profile"><UserRound size={19}/><span>{t('profile')}</span></NavLink>
          {isManager(me)&&<NavLink to="/hr"><Settings2 size={19}/><span>{t('management')}</span></NavLink>}
          {me.role==='ADMIN'&&<NavLink to="/admin"><Shield size={19}/><span>{t('admin')}</span></NavLink>}
        </nav>
      </div>
      <div className="sidebar-bottom">
        <div className="sidebar-support"><LifeBuoy size={20}/><div><strong>{t('needHelp')}</strong><small>{t('itSupport')}</small></div></div>
        <button className="sidebar-logout" onClick={()=>void logout()}><LogOut size={18}/>{t('logout')}</button>
      </div>
    </aside>
    <div className="app-workspace">
      <header className="site-header"><div className="header-top">
        <button ref={menuButton} className="icon-button mobile-menu-button" aria-expanded={menu} aria-controls="portal-navigation" aria-label={t('navigation')} onClick={()=>setMenu(x=>!x)}>{menu?<X size={21}/>:<Menu size={21}/>}</button>
        {location.pathname!=='/'&&<button type="button" className="header-back text-button" onClick={back} aria-label={t('back')}><ArrowLeft size={18}/><span>{t('back')}</span></button>}<div className="page-breadcrumb"><span>Toshkent Invest</span><ChevronRight size={14}/><strong>{t(section)}</strong></div>
        <form className="header-search" onSubmit={e=>{e.preventDefault();navigate('/search?q='+encodeURIComponent(search));}}><Search size={18}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder={t('searchHint')} aria-label={t('globalSearch')}/></form>
        <div className="header-actions"><button className="icon-button mobile-search-button" aria-label={t('globalSearch')} onClick={()=>navigate('/search')}><Search size={20}/></button><LocaleToggle locale={locale} setLocale={setLocale}/><details className="profile-menu"><summary><Avatar name={me.name} size="sm"/><span>{me.name}<small>{t(me.role==='ADMIN'?'adminRole':me.role==='HR'?'hrRole':'employeeRole')}</small></span><ChevronDown size={14}/></summary><div onClick={e=>e.currentTarget.closest('details')?.removeAttribute('open')}><Link to="/profile">{t('profile')}</Link>{isManager(me)&&<Link to="/hr">{t('management')}</Link>}{me.role==='ADMIN'&&<Link to="/admin">{t('admin')}</Link>}<button onClick={()=>void logout()}><LogOut size={15}/>{t('logout')}</button></div></details></div>
      </div></header>
      {me.demo&&<div className="demo-banner"><span className="demo-dot"/>{t('demo')}</div>}
      <main id="portal-main" className="main-content" tabIndex={-1}>{children??<Routes><Route path="/" element={<HomePage/>}/><Route path="/news" element={<NewsPage/>}/><Route path="/news/:id" element={<ArticlePage/>}/><Route path="/employees" element={<EmployeesPage/>}/><Route path="/employees/:id" element={<EmployeePage/>}/><Route path="/structure" element={<StructurePage/>}/><Route path="/structure/:id" element={<StructurePage/>}/><Route path="/documents" element={<DocumentsPage/>}/><Route path="/documents/:id" element={<DocumentPage/>}/><Route path="/documents/:id/versions/:versionId" element={<DocumentPage/>}/><Route path="/hr" element={<HrPage/>}/><Route path="/trash" element={<TrashPage/>}/><Route path="/profile" element={<ProfilePage/>}/><Route path="/admin" element={<AdminPage/>}/><Route path="/search" element={<SearchPage/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes>}</main>
      <footer className="site-footer"><strong>Toshkent Invest</strong><span>{t('privatePortal')}</span><p>{t('support')}</p><a href="https://www.toshkentinvest.uz/" target="_blank" rel="noopener noreferrer">{t('officialWebsite')}</a></footer>
    </div>
  </div>;
}
