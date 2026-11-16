const uzMonths=['yanvar','fevral','mart','aprel','may','iyun','iyul','avgust','sentabr','oktabr','noyabr','dekabr'];

export function formatPortalDateTime(date:Date,locale:'ru'|'uz'|'en'):string {
  if(!Number.isFinite(date.getTime()))return '—';
  return `${formatPortalDate(date,locale,true)}, ${new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Tashkent'}).format(date)}`;
}

export function formatPortalDate(date:Date,locale:'ru'|'uz'|'en',includeYear:boolean):string {
  if(!Number.isFinite(date.getTime()))return '—';
  if(locale==='en')return new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'long',...(includeYear?{year:'numeric' as const}:{}),timeZone:'Asia/Tashkent'}).format(date);
  if(locale==='ru')return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',...(includeYear?{year:'numeric' as const}:{}),timeZone:'Asia/Tashkent'}).format(date);
  // Some embedded browsers lack Uzbek month names and render CLDR placeholders (M11).
  const parts=new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'numeric',year:'numeric',timeZone:'Asia/Tashkent'}).formatToParts(date);
  const value=(type:string)=>parts.find(part=>part.type===type)?.value??'';
  return `${Number(value('day'))}-${uzMonths[Number(value('month'))-1]}${includeYear?` ${value('year')}-yil`:''}`;
}
