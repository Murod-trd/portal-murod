import type * as Generated from './api.generated';
export type Me = Generated.MeResponse;
export interface Page<T> { items:T[]; total:number; page:number; limit:number }
export type Employee = Generated.EmployeeResponse & { directReports?:Generated.DirectReportResponse[] };
export type Unit = Generated.UnitResponse;
export type Office = Generated.OfficeResponse;
export type Translation = Generated.TranslationResponse;
export type News = Generated.NewsResponse;
export type Version = Generated.VersionResponse;
export type Doc = Generated.DocumentResponse & { versions?:Version[] };
export type Birthday = Generated.BirthdayResponse;
export type FileState = Generated.FileStateResponse;
export type UserAccount = Generated.UserAccountResponse;
export type TrashItem = Generated.TrashItemResponse;
export class ApiError extends Error { constructor(public code:string,public status:number,public details?:unknown,public fieldCodes?:unknown){ super(code); } }
let token='';
export function setCsrf(value:string){token=value;}
export function query(values:Record<string,unknown>):string{const p=new URLSearchParams();for(const[k,v]of Object.entries(values))if(v!==''&&v!==undefined&&v!==null&&v!==false)p.set(k,String(v));return p.size?'?'+p.toString():'';}
export async function api<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
  const headers:Record<string,string>={Accept:'application/json'};
  if(method!=='GET'&&method!=='HEAD')headers['X-CSRFToken']=token;
  if(body!==undefined&&!(body instanceof FormData))headers['Content-Type']='application/json';
  const response=await fetch('/api/v1'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:body instanceof FormData?body:JSON.stringify(body),signal,cache:'no-store'});
  let data:Record<string,unknown>;
  try { const parsed:unknown=await response.json(); if(!parsed||typeof parsed!=='object')throw new Error(); data=parsed as Record<string,unknown>; } catch { throw new ApiError(response.status===413?'UPLOAD_SIZE_LIMIT':'INVALID_RESPONSE',response.status); }  // a proxy's own 413 page is not JSON
  if(!response.ok){if(response.status===401&&path!=='/auth/login')window.dispatchEvent(new Event('portal-session-expired'));throw new ApiError(typeof data.code==='string'?data.code:'REQUEST_FAILED',response.status,data.details,data.fieldCodes);}
  if(typeof data.csrfToken==='string')setCsrf(data.csrfToken);
  return data as T;
}
export const fileUrl=(id:string,inline=false)=>`/api/v1/files/${id}/download${inline?'?inline=true':''}`;
export const isManager=(me:Me)=>me.role==='ADMIN'||me.role==='HR';
