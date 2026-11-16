import type {Key} from './i18n';
/** Maps the machine-readable codes in `fieldCodes` (DRF validation codes plus the portal's own) to translated messages; raw server text is never shown. */
const byCode:Record<string,Key>={required:'vRequired',blank:'vRequired',null:'vRequired',max_length:'vTooLong',min_length:'vTooShort',invalid_choice:'vChoice',unique:'vUnique',max_value:'vNumber',min_value:'vNumber',does_not_exist:'vNotFound',control_chars:'vControlChars',phone_short:'vPhoneShort',birthday_range:'vBirthdayRange',date_range:'vDateRange',datetime_range:'vDateTimeRange',expires_before_publish:'vExpiry',empty_content:'vEmptyContent',unknown_field:'vUnknownField',unsupported_locale:'vLocale',not_object:'vObject'};
const dateFields=new Set(['birthday','documentDate','effectiveDate']),dateTimeFields=new Set(['publishedAt','expiresAt']);
export function validationKey(field:string,code:string):Key{
  if(code==='invalid'||code==='date'||code==='datetime'){
    if(dateFields.has(field))return 'vDate';
    if(dateTimeFields.has(field))return 'vDateTime';
    if(field==='workEmail')return 'vEmail';
    if(field==='mobilePhone')return 'vPhone';
  }
  return byCode[code]??'invalidValue';
}
export interface ValidationEntry{field:string;key:Key}
function walk(value:unknown,field:string,out:ValidationEntry[]){
  if(typeof value==='string'){if(!out.some(entry=>entry.field===field&&entry.key===validationKey(field,value)))out.push({field,key:validationKey(field,value)});return;}
  if(Array.isArray(value)){for(const item of value)walk(item,field,out);return;}
  if(value&&typeof value==='object')for(const[key,item] of Object.entries(value))walk(item,key,out);
}
/** One entry per failing field. Older servers send only `details`: those fields get the generic message. */
export function validationEntries(details:unknown,fieldCodes:unknown):ValidationEntry[]{
  const out:ValidationEntry[]=[];
  if(fieldCodes&&typeof fieldCodes==='object')walk(fieldCodes,'',out);
  if(!out.length&&details&&typeof details==='object')for(const field of Object.keys(details))out.push({field,key:'invalidValue'});
  return out;
}
