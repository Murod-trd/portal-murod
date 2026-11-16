import {createContext,useContext,useState,useCallback,type ReactNode} from 'react';
import type {Me,Employee,News,Doc,Unit,Office} from './api';
import {dictionaries,type Key,errorText} from './i18n';
import {ApiError} from './api';
export type DialogState={type:'employee';employee?:Employee}|{type:'news';news?:News}|{type:'document';document?:Doc}|{type:'version';document:Doc}|{type:'unit';unit?:Unit}|{type:'office';office?:Office}|{type:'import'}|{type:'account'}|null;
export interface ConfirmOptions{title:string;description:string;danger?:boolean;requireText?:string;confirmLabel?:string}
interface State{me:Me;locale:'uz'|'ru'|'en';t:(key:Key)=>string;setLocale:(v:'uz'|'ru'|'en')=>void;refreshMe:()=>Promise<void>;notify:(text:string,kind?:'status'|'alert')=>void;showError:(e:unknown)=>void;dialog:DialogState;open:(d:DialogState)=>void;changed:number;onSaved:()=>void;confirm:(o:ConfirmOptions)=>Promise<string|null>;confirmOptions:ConfirmOptions|null;resolveConfirm:(v:string|null)=>void}
const Context=createContext<State|null>(null);
export function usePortal(){const value=useContext(Context);if(!value)throw new Error('Portal context missing');return value;}
export function PortalProvider({me,locale,setLocale,refreshMe,children}:{me:Me;locale:'uz'|'ru'|'en';setLocale:(v:'uz'|'ru'|'en')=>void;refreshMe:()=>Promise<void>;children:ReactNode}){
  const[dialog,open]=useState<DialogState>(null),[changed,setChanged]=useState(0),[toast,setToast]=useState<{text:string;kind:'status'|'alert'}|null>(null),[confirmation,setConfirmation]=useState<{options:ConfirmOptions;resolve:(v:string|null)=>void}|null>(null);
  const notify=useCallback((text:string,kind:'status'|'alert'='status')=>{setToast({text,kind});window.setTimeout(()=>setToast(null),4500);},[]);
  const t=(key:Key)=>dictionaries[locale][key];
  const showError=(e:unknown)=>notify(errorText(e instanceof ApiError?e.code:'REQUEST_FAILED',locale),'alert');
  const confirm=(options:ConfirmOptions)=>new Promise<string|null>(resolve=>setConfirmation({options,resolve}));
  const resolveConfirm=(value:string|null)=>{confirmation?.resolve(value);setConfirmation(null);};
  return <Context.Provider value={{me,locale,t,setLocale,refreshMe,notify,showError,dialog,open,changed,onSaved:()=>{setChanged(x=>x+1);open(null);notify(t('saved'));},confirm,confirmOptions:confirmation?.options??null,resolveConfirm}}>{children}{toast&&<div className="toast" role={toast.kind}>{toast.text}</div>}</Context.Provider>;
}
