"""Untrusted-file work. Imported by the isolated Python worker, not HTTP views."""
import base64
import csv
import io
import re
import socket
import struct
import subprocess
import tempfile
import time
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

class ProcessingError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__(code)

# Scanned pages uploaded as plain images (purpose DOCUMENT). The original bytes stay
# stored unchanged; only the sandboxed worker decodes them.
SCAN_EXTENSIONS={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.tif':'TIFF','.tiff':'TIFF'}
SCAN_MODES={'1','L','LA','P','PA','RGB','RGBA','CMYK','YCbCr'}
SCAN_MAX_PIXELS=40_000_000        # per page: A0 at 150 dpi is ~46 MP, A3 at 300 dpi ~17 MP
SCAN_MAX_SIDE=12_000
SCAN_MAX_TOTAL_PIXELS=800_000_000  # 80 A4 pages at 300 dpi are ~700 MP
SCAN_OCR_SIDE=3508                 # longest side handed to OCR (A4 at 300 dpi)
SCAN_MIN_UPSCALE_SIDE=1400         # smaller rasters are enlarged 2x for OCR
DOCX_IMAGE_LIMITS={'count':20,'bytes':10*1024*1024,'total':30*1024*1024,'min_side':40,'min_pixels':20_000}

def scan_frames(data,extension,max_pages,first_only=False):
    """Open an uploaded raster under explicit limits; returns (page count, iterator of decoded frames).

    The format must match the extension (Pillow is restricted to that decoder), the pixel count is
    checked from the header before any pixel is decoded, and every frame is fully decoded so a
    truncated file fails here. Used by the sandboxed worker only."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS=SCAN_MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            image=Image.open(io.BytesIO(data),formats=[SCAN_EXTENSIONS[extension]])
    except (Image.DecompressionBombError,Image.DecompressionBombWarning,MemoryError) as error:
        raise ProcessingError('IMAGE_LIMIT') from error
    except Image.UnidentifiedImageError as error:
        raise ProcessingError('TYPE_MISMATCH') from error
    except Exception as error:
        raise ProcessingError('INVALID_IMAGE') from error
    count=getattr(image,'n_frames',1)
    if first_only:
        count=1
    elif count>1 and image.format!='TIFF':
        raise ProcessingError('UNSUPPORTED_FORMAT')  # animated PNG
    if count>max_pages:
        raise ProcessingError('PAGE_LIMIT')
    def frames():
        total=0
        for index in range(count):
            try:
                image.seek(index)
                width,height=image.size
                if image.mode not in SCAN_MODES:
                    raise ProcessingError('UNSUPPORTED_FORMAT')
                if width<1 or height<1 or width>SCAN_MAX_SIDE or height>SCAN_MAX_SIDE or width*height>SCAN_MAX_PIXELS:
                    raise ProcessingError('IMAGE_LIMIT')
                total+=width*height
                if total>SCAN_MAX_TOTAL_PIXELS:
                    raise ProcessingError('IMAGE_LIMIT')
                image.load()
            except ProcessingError:
                raise
            except MemoryError as error:
                raise ProcessingError('IMAGE_LIMIT') from error
            except Exception as error:
                raise ProcessingError('INVALID_IMAGE') from error
            yield image
    return count,frames()

def normalise_scan(frame):
    """Decoded frame -> grayscale page raster for OCR (EXIF orientation applied, transparency on white)."""
    from PIL import Image,ImageOps
    page=ImageOps.exif_transpose(frame)
    if page.mode in {'P','PA','LA','RGBA'}:
        page=page.convert('RGBA')
        white=Image.new('RGB',page.size,(255,255,255))
        white.paste(page,mask=page.getchannel('A'))
        page=white
    page=page.convert('L')
    longest=max(page.size)
    if longest>SCAN_OCR_SIDE:
        page.thumbnail((SCAN_OCR_SIDE,SCAN_OCR_SIDE),Image.LANCZOS)
    elif longest<SCAN_MIN_UPSCALE_SIDE:
        page=page.resize((page.width*2,page.height*2),Image.LANCZOS)
    return page

def inspect_scan_image(data,extension,max_pages):
    count,frames=scan_frames(data,extension,max_pages)
    for _ in frames:
        pass
    return count

def _response(sock):
    data=b''
    while len(data)<8192:
        chunk=sock.recv(1024)
        if not chunk:
            break
        data+=chunk
        if b'\0' in chunk:
            break
    return data.rstrip(b'\0').decode('utf-8','replace')

def clamav_scan(data,host,port=3310,max_signature_age_hours=72):
    if not host:
        raise ProcessingError('ANTIVIRUS_UNAVAILABLE')
    try:
        with socket.create_connection((host,port),timeout=15) as sock:
            sock.sendall(b'zVERSION\0')
            version=_response(sock)
        parts=version.split('/')
        if len(parts)<3:
            raise ProcessingError('ANTIVIRUS_SIGNATURES_UNKNOWN')
        stamp=datetime.strptime(parts[-1].strip(),'%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if age < -3600:
            raise ProcessingError('ANTIVIRUS_SIGNATURES_INVALID')
        if age>max_signature_age_hours*3600:
            raise ProcessingError('ANTIVIRUS_SIGNATURES_STALE')
        with socket.create_connection((host,port),timeout=30) as sock:
            sock.sendall(b'zINSTREAM\0')
            for offset in range(0,len(data),65536):
                chunk=data[offset:offset+65536]
                sock.sendall(struct.pack('!I',len(chunk))+chunk)
            sock.sendall(struct.pack('!I',0))
            result=_response(sock)
    except (OSError,ValueError) as error:
        raise ProcessingError('ANTIVIRUS_UNAVAILABLE') from error
    if result.endswith(' FOUND'):
        raise ProcessingError('MALWARE_DETECTED')
    if result!='stream: OK':
        raise ProcessingError('ANTIVIRUS_INCOMPLETE')
    return {'scannerVersion':parts[0][:150],'signatureVersion':parts[1][:100]}

def validate_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if len(entries)>2000 or sum(e.file_size for e in entries)>100*1024*1024:
            raise ProcessingError('ARCHIVE_LIMIT')
        names=set()
        for entry in entries:
            path=PurePosixPath(entry.filename.replace('\\','/'))
            if path.is_absolute() or '..' in path.parts or ':' in entry.filename or (entry.external_attr>>16)&0o170000==0o120000:
                raise ProcessingError('UNSAFE_ARCHIVE_PATH')
            if str(path) in names:
                raise ProcessingError('DUPLICATE_ARCHIVE_ENTRY')
            names.add(str(path))
            if entry.flag_bits & 1:
                raise ProcessingError('ENCRYPTED_FILE')
            if entry.file_size>10*1024*1024 and entry.file_size>max(entry.compress_size,1)*100:
                raise ProcessingError('ARCHIVE_RATIO_LIMIT')
            lower=entry.filename.lower()
            if 'vbaproject' in lower or 'embeddings' in PurePosixPath(lower).parts:
                raise ProcessingError('ACTIVE_CONTENT')
        return {e.filename for e in entries}

def validate_structure(data,name,purpose):
    import filetype
    extension=Path(name).suffix.lower()
    allowed={'DOCUMENT':{'.pdf','.docx'}|set(SCAN_EXTENSIONS),'IMPORT':{'.csv','.xlsx'},'PHOTO':{'.jpg','.jpeg','.png','.webp'},'COVER':{'.jpg','.jpeg','.png','.webp'}}
    if extension not in allowed.get(purpose,set()):
        raise ProcessingError('UNSUPPORTED_FORMAT')
    if extension=='.csv':
        try:
            text=data.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError('NUL')
        except (UnicodeDecodeError,ValueError) as error:
            raise ProcessingError('INVALID_CSV') from error
        return 'text/csv'
    if extension in {'.xlsx','.docx'}:
        try:
            names=validate_zip(data)
        except (zipfile.BadZipFile,RuntimeError) as error:
            raise ProcessingError('INVALID_OFFICE_FILE') from error
        if '[Content_Types].xml' not in names or ('xl/workbook.xml' if extension=='.xlsx' else 'word/document.xml') not in names:
            raise ProcessingError('TYPE_MISMATCH')
        return 'application/vnd.openxmlformats-officedocument.'+('spreadsheetml.sheet' if extension=='.xlsx' else 'wordprocessingml.document')
    detected=filetype.guess(data)
    expected={'.pdf':'application/pdf','.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.tif':'image/tiff','.tiff':'image/tiff'}[extension]
    if not detected or detected.mime!=expected:
        raise ProcessingError('TYPE_MISMATCH')
    return expected

def pdf_reader(data,max_pages):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(data),strict=True)
    if reader.is_encrypted:
        raise ProcessingError('ENCRYPTED_FILE')
    if len(reader.pages)>max_pages:
        raise ProcessingError('PAGE_LIMIT')
    visited=set()
    def inspect(obj,depth=0):
        if depth>35 or len(visited)>50000:
            raise ProcessingError('PDF_COMPLEXITY_LIMIT')
        if hasattr(obj,'get_object'):
            obj=obj.get_object()
        if id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj,dict):
            if set(map(str,obj))&{'/JavaScript','/JS','/Launch','/EmbeddedFiles','/RichMedia','/XFA','/AA','/OpenAction'}:
                raise ProcessingError('ACTIVE_CONTENT')
            if '/URI' in obj and urlsplit(str(obj['/URI']).strip()).scheme.lower() not in {'http','https','mailto'}:
                raise ProcessingError('ACTIVE_CONTENT')
            if str(obj.get('/S','')) in {'/SubmitForm','/ImportData','/GoToR','/GoToE','/Rendition','/Sound','/Movie'}:
                raise ProcessingError('ACTIVE_CONTENT')
            for value in obj.values():
                if isinstance(value,(dict,list)) or hasattr(value,'get_object'):
                    inspect(value,depth+1)
        elif isinstance(obj,list):
            for value in obj:
                inspect(value,depth+1)
    inspect(reader.trailer)
    return reader

def inspect_office(data):
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(('.xml','.rels')):
                    continue
                root=ElementTree.fromstring(archive.read(name))
                instructions=[]
                for element in root.iter():
                    local=element.tag.rsplit('}',1)[-1].lower()
                    if local in {'object','control','altchunk'}:
                        raise ProcessingError('ACTIVE_OFFICE_CONTENT')
                    if local=='relationship' and element.attrib.get('Type','').rsplit('/',1)[-1].lower() in {'oleobject','package','control','attachedtemplate'}:
                        raise ProcessingError('ACTIVE_OFFICE_CONTENT')
                    if local=='instrtext':instructions.append(element.text or '')
                    if local=='fldsimple':instructions.extend(value for key,value in element.attrib.items() if key.rsplit('}',1)[-1].lower()=='instr')
                    if local=='relationship' and element.attrib.get('TargetMode','').lower()=='external':
                        target=element.attrib.get('Target','').strip()
                        if not element.attrib.get('Type','').endswith('/hyperlink') or (not target.startswith('#') and urlsplit(target).scheme.lower() not in {'http','https','mailto'}):
                            raise ProcessingError('ACTIVE_OFFICE_CONTENT')
                combined=''.join(instructions)
                if re.search(r'\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|HYPERLINK)\b',combined,re.IGNORECASE):
                    raise ProcessingError('ACTIVE_OFFICE_CONTENT')
    except (DefusedXmlException,ElementTree.ParseError) as error:
        raise ProcessingError('ACTIVE_OR_INVALID_XML') from error

def inspect_clean_file(data,name,purpose,max_pages):
    mime=validate_structure(data,name,purpose)
    # Parsers run after AV, inside the same isolated worker limits.
    if mime=='application/pdf':
        pdf_reader(data,max_pages)
    if name.lower().endswith('.docx'):
        inspect_office(data)
        from docx import Document
        Document(io.BytesIO(data))
    if purpose=='DOCUMENT' and Path(name).suffix.lower() in SCAN_EXTENSIONS:
        inspect_scan_image(data,Path(name).suffix.lower(),max_pages)
    output={'mimeType':mime}
    if purpose in {'PHOTO','COVER'}:
        from PIL import Image,ImageOps
        Image.MAX_IMAGE_PIXELS=20_000_000
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            image=Image.open(io.BytesIO(data))
            image.load()
            image=ImageOps.exif_transpose(image).convert('RGB')  # phone photos are stored sideways with an EXIF flag the saved JPEG drops
            image.thumbnail((1600,1000) if purpose=='COVER' else (800,800))
            buffer=io.BytesIO()
            image.save(buffer,format='JPEG',quality=85)
        cleaned=buffer.getvalue()
        output.update(mimeType='image/jpeg',cleanedBase64=base64.b64encode(cleaned).decode())
    return output

def scan_file(data,name,purpose,host,port,max_pages):
    # The production worker performs these network scans in its trusted supervisor.
    info=clamav_scan(data,host,port)
    output=inspect_clean_file(data,name,purpose,max_pages)
    if output.get('cleanedBase64'):clamav_scan(base64.b64decode(output['cleanedBase64'],validate=True),host,port)
    return {**output,**info,'status':'APPROVED'}

def ocr_languages_ready(languages):
    result=subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,timeout=15,check=True)
    if set(languages.split('+'))-set(result.stdout.splitlines()[1:]):
        raise ProcessingError('OCR_LANGUAGES_UNAVAILABLE')

class OcrRun:
    """One file's OCR: shared time budget, orientation memory across pages and quality totals."""
    PAGE_RESERVE=15  # seconds kept for each page still to come before optional retries stop
    def __init__(self,languages,total_pages):
        self.languages,self.left,self.deadline=languages,total_pages,time.monotonic()+500
        self.state,self.quality_pages,self.attempted,self.checked={},[],0,False
    def page(self,image):
        """Recognise one page raster; None when it holds no text."""
        from .ocr import ocr_page,OcrFailure
        if not self.checked:
            ocr_languages_ready(self.languages)
            self.checked=True
        self.left-=1
        try:
            result=ocr_page(image,self.languages,self.deadline,self.state,self.deadline-self.PAGE_RESERVE*(self.left+1))
        except OcrFailure as error:
            raise ProcessingError(str(error)) from error
        self.attempted+=1
        if result:
            self.quality_pages.append(result['quality'])
        return result
    def quality(self,**extra):
        pages=self.quality_pages
        scored=[q for q in pages if q['lineCount']]
        rotations=[q['rotation'] for q in pages]
        value={'engine':'tesseract-script-ensemble','ocrPages':self.attempted,
               'meanConfidence':round(sum(q['meanConfidence'] for q in scored)/len(scored),2) if scored else None,
               'lowConfidenceLines':sum(q['lowConfidenceLines'] for q in pages),
               'profiles':list(dict.fromkeys(profile for q in pages for profile in q['profiles'])),
               'rotatedPages':sum(r!=0 for r in rotations),'sparsePages':sum(q['sparse'] for q in pages)}
        if value['rotatedPages']:
            value['rotations']=rotations
        return {**value,**extra}

def docx_pictures(document):
    """Embedded raster pictures of the document body in order: bounded, deduplicated, no vector formats."""
    namespace='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    kinds={'image/png':'.png','image/jpeg':'.jpg','image/tiff':'.tif'}
    limits,seen,result,total=DOCX_IMAGE_LIMITS,set(),[],0
    for element in document.element.body.iter():
        if not str(element.tag).endswith('}blip'):
            continue
        relation=element.get(namespace+'embed')
        if not relation or relation in seen:
            continue
        seen.add(relation)
        part=document.part.related_parts.get(relation)
        if part is None or part.content_type not in kinds:
            continue
        blob=part.blob
        if len(blob)>limits['bytes'] or total+len(blob)>limits['total']:
            continue
        total+=len(blob)
        result.append((kinds[part.content_type],blob))
        if len(result)>=limits['count']:
            break
    return result

def extract_docx(data,languages):
    from docx import Document
    validate_zip(data)
    doc=Document(io.BytesIO(data))
    parts=[p.text for p in doc.paragraphs]
    parts.extend(' | '.join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows)
    text='\n'.join(parts)
    if len(text)>2_000_000:
        raise ProcessingError('TEXT_LIMIT')
    pictures=docx_pictures(doc)
    if not pictures:
        return {'text':text,'pages':None}
    run=OcrRun(languages,len(pictures))
    picture_texts,search_parts=[],[]
    with tempfile.TemporaryDirectory(prefix='portal-docx-ocr-') as folder:
        for index,(extension,blob) in enumerate(pictures):
            try:
                _,frames=scan_frames(blob,extension,1,first_only=True)
                frame=next(frames)
            except ProcessingError:
                run.left-=1
                continue  # undecodable or oversized pictures are ignored; the DOCX itself stays valid
            width,height=frame.size
            if min(width,height)<DOCX_IMAGE_LIMITS['min_side'] or width*height<DOCX_IMAGE_LIMITS['min_pixels']:
                run.left-=1
                continue
            path=Path(folder)/f'picture-{index}.png'
            normalise_scan(frame).save(path)
            result=run.page(path)
            path.unlink(missing_ok=True)
            if result:
                if result['text'].strip():
                    picture_texts.append(result['text'])
                search_parts.append(result['searchText'])
    if picture_texts:
        text=(text.rstrip()+'\n\n' if text.strip() else '')+'\n\n'.join(picture_texts)
    if len(text)>2_000_000:
        raise ProcessingError('TEXT_LIMIT')
    output={'text':text,'pages':None}
    if run.attempted:
        output.update(searchText='\n'.join(search_parts),quality=run.quality(embeddedImages=run.attempted))
    return output

def extract_image(data,extension,languages,max_pages):
    count,frames=scan_frames(data,extension,max_pages)
    run=OcrRun(languages,count)
    texts,search_parts=[],[]
    with tempfile.TemporaryDirectory(prefix='portal-ocr-') as folder:
        for index,frame in enumerate(frames):
            path=Path(folder)/f'page-{index}.png'
            normalise_scan(frame).save(path)  # normalised raster; the stored original is never modified
            result=run.page(path)
            path.unlink(missing_ok=True)
            texts.append(result['text'] if result else '')
            if result:
                search_parts.append(result['searchText'])
            if sum(map(len,texts))>2_000_000:
                raise ProcessingError('TEXT_LIMIT')
    if not run.quality_pages:
        raise ProcessingError('OCR_NO_TEXT')
    return {'text':'\n\n'.join(texts),'searchText':'\n'.join(search_parts),'pages':count,'quality':run.quality()}

def extract_text(data,name,languages,max_pages):
    if name.lower().endswith('.docx'):
        return extract_docx(data,languages)
    if Path(name.lower()).suffix in SCAN_EXTENSIONS:
        return extract_image(data,Path(name.lower()).suffix,languages,max_pages)
    reader=pdf_reader(data,max_pages)
    texts=[]
    search_parts=[]
    run=OcrRun(languages,len(reader.pages))
    with tempfile.TemporaryDirectory(prefix='portal-ocr-') as folder:
        source=Path(folder)/'source.pdf'
        source.write_bytes(data)
        for index,page in enumerate(reader.pages):
            text=page.extract_text() or ''
            # A long searchable header does not mean the scan below it has a text layer.
            # Keep embedded text and OCR image-bearing pages, including mixed pages.
            if not text.strip() or len(page.images)>0:
                output=Path(folder)/f'page-{index}'
                if not run.checked:
                    ocr_languages_ready(languages)
                    run.checked=True
                subprocess.run(['pdftoppm','-f',str(index+1),'-l',str(index+1),'-singlefile','-r','300','-scale-to','3000','-png',str(source),str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45,check=True)
                result=run.page(Path(str(output)+'.png'))
                ocr=result['text'] if result else ''
                if result:
                    search_parts.append(result['searchText'])
                if text.strip():
                    known={line.strip().casefold() for line in text.splitlines() if line.strip()}
                    additional=[line for line in ocr.splitlines() if line.strip() and line.strip().casefold() not in known]
                    if additional:
                        text=text.rstrip()+'\n'+'\n'.join(additional)
                else:
                    text=ocr
                Path(str(output)+'.png').unlink(missing_ok=True)
            else:
                run.left-=1
            texts.append(text)
            if sum(map(len,texts))>2_000_000:
                raise ProcessingError('TEXT_LIMIT')
    if run.attempted and not run.quality_pages and not any(t.strip() for t in texts):
        raise ProcessingError('OCR_NO_TEXT')  # scanned pages without any recognisable text
    return {'text':'\n\n'.join(texts),'searchText':'\n'.join(search_parts),'pages':len(reader.pages),'quality':run.quality()}

def parse_import(data,name):
    rows=[]
    if name.lower().endswith('.csv'):
        text=data.decode('utf-8-sig')
        try:
            dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
        except csv.Error:
            dialect=csv.excel
        source=csv.reader(io.StringIO(text),dialect)
        for row in source:
            rows.append(row)
            if len(rows)>2001:
                raise ProcessingError('IMPORT_ROW_LIMIT')
    else:
        import openpyxl
        validate_zip(data)
        book=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)
        try:
            sheet=book.active
            if (sheet.max_row or 0)>2001 or (sheet.max_column or 0)>50:
                raise ProcessingError('IMPORT_LIMIT')
            for row in sheet.iter_rows():
                if len(row)>50:
                    raise ProcessingError('IMPORT_LIMIT')
                values=[]
                for cell in row:
                    if cell.data_type=='f':
                        raise ProcessingError('IMPORT_FORMULA_REJECTED')
                    value=cell.value
                    if hasattr(value,'date'):
                        value=value.date().isoformat()
                    values.append('' if value is None else str(value))
                rows.append(values)
                if len(rows)>2001:
                    raise ProcessingError('IMPORT_ROW_LIMIT')
        finally:
            book.close()
    if not rows:
        raise ProcessingError('IMPORT_EMPTY')
    for row in rows:
        if len(row)>50 or any(len(str(cell))>2000 for cell in row):
            raise ProcessingError('IMPORT_CELL_LIMIT')
    import json
    # rowNumbers keeps the real spreadsheet row of every kept row (blank rows are dropped but still counted).
    kept=[(number,row) for number,row in enumerate(rows[1:],start=2) if any(str(v).strip() for v in row)]
    result={'headers':[str(x).strip() for x in rows[0]],'rows':[[str(v) for v in row] for _,row in kept],'rowNumbers':[number for number,_ in kept]}
    if len(json.dumps(result).encode())>4*1024*1024:
        raise ProcessingError('IMPORT_RESULT_LIMIT')
    return result
