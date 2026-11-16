/** datetime-local is explicitly a Tashkent wall clock, independent of browser timezone. */
export function toTashkentInput(value?:string|null):string {
  if(!value)return '';
  const time=Date.parse(value);
  return Number.isFinite(time)?new Date(time+5*60*60*1000).toISOString().slice(0,16):'';
}

export function fromTashkentInput(value:string,original?:string|null):string|null {
  if(!value)return null;
  // Saving unrelated fields must preserve the original seconds and precision.
  if(original&&value===toTashkentInput(original))return original;
  return new Date(value+'+05:00').toISOString();
}

/** Splits a search snippet around the match range sent by the server; null when the range cannot be trusted. */
export function splitSnippet(text:string,match?:number[]):[string,string,string]|null {
  if(!match||match.length!==2)return null;
  const[start,end]=match;
  if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>text.length)return null;
  return [text.slice(0,start),text.slice(start,end),text.slice(end)];
}
