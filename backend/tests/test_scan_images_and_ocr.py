"""Image scans as documents, page orientation, sparse text and DOCX pictures.

These tests run without Tesseract: the recognition passes are replaced by deterministic
stand-ins that behave the way real passes were measured to (docs/OCR_RESULTS.md); the real
binaries are exercised by the lab run and by the skipped-elsewhere OCR tests."""
import io
import time
import zlib
import struct

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw

from portal import models as m, ocr
from portal.domain import search_fold
from portal.file_processing import (ProcessingError, extract_text, inspect_clean_file, normalise_scan,
                                    scan_frames, validate_structure, docx_pictures)
from portal.file_views import valid_ocr_quality
from .test_content_and_files import approved_fixture
from .test_file_lifecycle import result, running_job


def raster(kind='PNG', size=(600, 400), mode='L', **options):
    image = Image.new(mode, size, 255 if mode in {'L', '1'} else (255, 255, 255))
    ImageDraw.Draw(image).rectangle((50, 50, 400, 120), fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format=kind, **options)
    return buffer.getvalue()


def tiff_pages(count, size=(120, 90)):
    frames = [Image.new('1', size, 1) for _ in range(count)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format='TIFF', save_all=True, append_images=frames[1:], compression='group4')
    return buffer.getvalue()


def error_code(call, *args):
    with pytest.raises(ProcessingError) as caught:
        call(*args)
    return caught.value.code


# ---- 1. image scans as documents -------------------------------------------------

@pytest.mark.parametrize('name,data,mime', [
    ('a.png', raster('PNG'), 'image/png'), ('a.jpg', raster('JPEG'), 'image/jpeg'),
    ('a.JPEG', raster('JPEG'), 'image/jpeg'), ('a.tif', tiff_pages(1), 'image/tiff'),
    ('a.tiff', tiff_pages(3), 'image/tiff')])
def test_image_scans_are_accepted_for_documents(name, data, mime):
    assert validate_structure(data, name, 'DOCUMENT') == mime
    assert inspect_clean_file(data, name, 'DOCUMENT', 80) == {'mimeType': mime}


def test_webp_stays_photo_only():
    webp = raster('WEBP')
    assert validate_structure(webp, 'a.webp', 'PHOTO') == 'image/webp'
    assert error_code(validate_structure, webp, 'a.webp', 'DOCUMENT') == 'UNSUPPORTED_FORMAT'


def test_extension_must_match_real_format():
    pdf = b'%PDF-1.7\n' + b'x' * 50
    assert error_code(inspect_clean_file, pdf, 'fake.png', 'DOCUMENT', 80) == 'TYPE_MISMATCH'
    assert error_code(inspect_clean_file, raster('JPEG'), 'a.png', 'DOCUMENT', 80) == 'TYPE_MISMATCH'
    assert error_code(inspect_clean_file, raster('PNG'), 'a.tif', 'DOCUMENT', 80) == 'TYPE_MISMATCH'
    assert error_code(inspect_clean_file, raster('GIF'), 'a.png', 'DOCUMENT', 80) == 'TYPE_MISMATCH'


def test_pixel_limit_rejects_decompression_bomb_from_a_tiny_file():
    bomb = raster('PNG', (7000, 7000), '1')  # ~49 MP: tens of bytes per megapixel on disk
    assert len(bomb) < 100_000
    assert error_code(inspect_clean_file, bomb, 'bomb.png', 'DOCUMENT', 80) == 'IMAGE_LIMIT'
    assert error_code(inspect_clean_file, raster('PNG', (13000, 100), '1'), 'wide.png', 'DOCUMENT', 80) == 'IMAGE_LIMIT'


def test_png_header_claiming_gigapixels_is_not_decoded():
    def chunk(tag, body):
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 60000, 60000, 1, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0' * 100)) + chunk(b'IEND', b''))
    assert error_code(inspect_clean_file, data, 'huge.png', 'DOCUMENT', 80) == 'IMAGE_LIMIT'


def test_multipage_tiff_uses_the_pdf_page_limit():
    assert inspect_clean_file(tiff_pages(3), 'a.tif', 'DOCUMENT', 3) == {'mimeType': 'image/tiff'}
    assert error_code(inspect_clean_file, tiff_pages(4), 'a.tif', 'DOCUMENT', 3) == 'PAGE_LIMIT'


def test_total_pixel_budget_across_tiff_pages(monkeypatch):
    monkeypatch.setattr('portal.file_processing.SCAN_MAX_TOTAL_PIXELS', 2 * 120 * 90)
    assert error_code(inspect_clean_file, tiff_pages(3), 'a.tif', 'DOCUMENT', 80) == 'IMAGE_LIMIT'


def test_truncated_and_garbage_images_fail_cleanly():
    jpeg = raster('JPEG', (800, 600), 'RGB')
    assert error_code(inspect_clean_file, jpeg[:len(jpeg) // 2], 'cut.jpg', 'DOCUMENT', 80) == 'INVALID_IMAGE'
    assert error_code(inspect_clean_file, jpeg[:600] + bytes(range(256)) * 20, 'bad.jpg', 'DOCUMENT', 80) in {'INVALID_IMAGE', 'TYPE_MISMATCH'}
    png = raster('PNG', (800, 600), 'RGB')
    assert error_code(inspect_clean_file, png[:len(png) // 2], 'cut.png', 'DOCUMENT', 80) == 'INVALID_IMAGE'


def test_animated_and_unsupported_modes_are_rejected():
    frames = [Image.new('RGB', (50, 50), color) for color in ('red', 'blue')]
    buffer = io.BytesIO()
    frames[0].save(buffer, format='PNG', save_all=True, append_images=frames[1:])
    # Whichever layer notices first (signature sniffing or the frame count), it is a clean rejection.
    assert error_code(inspect_clean_file, buffer.getvalue(), 'anim.png', 'DOCUMENT', 80) in {'UNSUPPORTED_FORMAT', 'TYPE_MISMATCH'}
    assert error_code(scan_frames, buffer.getvalue(), '.png', 80) == 'UNSUPPORTED_FORMAT'
    sixteen = io.BytesIO()
    Image.new('I;16', (60, 60), 1000).save(sixteen, format='PNG')
    assert error_code(inspect_clean_file, sixteen.getvalue(), 'deep.png', 'DOCUMENT', 80) == 'UNSUPPORTED_FORMAT'


def test_scanning_never_alters_the_original_bytes():
    data = raster('JPEG', (500, 300), 'RGB')
    before = bytes(data)
    inspect_clean_file(data, 'a.jpg', 'DOCUMENT', 80)
    assert data == before
    assert 'cleanedBase64' not in inspect_clean_file(data, 'a.jpg', 'DOCUMENT', 80)


def test_normalised_raster_is_grayscale_bounded_and_oriented():
    big = Image.new('RGB', (6000, 4000), 'white')
    assert max(normalise_scan(big).size) == 3508 and normalise_scan(big).mode == 'L'
    small = Image.new('RGB', (600, 400), 'white')
    assert normalise_scan(small).size == (1200, 800)
    transparent = Image.new('RGBA', (2000, 1500), (0, 0, 0, 0))
    assert normalise_scan(transparent).getextrema() == (255, 255)  # transparency becomes paper white
    sideways = Image.new('RGB', (2000, 1000), 'white')
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    sideways.save(buffer, format='JPEG', exif=exif)
    _, frames = scan_frames(buffer.getvalue(), '.jpg', 80)
    assert normalise_scan(next(frames)).size == (1000, 2000)


@pytest.fixture
def fake_ocr(monkeypatch):
    """Stand-in for tesseract-backed page recognition; records the raster it was given."""
    calls = []
    monkeypatch.setattr('portal.file_processing.ocr_languages_ready', lambda languages: None)
    def page(image, languages, deadline, state=None, probe_deadline=None):
        with Image.open(image) as raster_page:
            calls.append((image.name, raster_page.mode, raster_page.size, probe_deadline is not None))
        index = len(calls)
        return {'text': f'page {index} text', 'searchText': f'page {index} text\nvariant {index}',
                'quality': {'meanConfidence': 90.0, 'lowConfidenceLines': 1, 'lineCount': 2, 'profiles': ['eng'],
                            'rotation': 90 if index == 2 else 0, 'words': 40, 'sparse': index == 3}}
    monkeypatch.setattr('portal.ocr.ocr_page', page)
    return calls


def test_multipage_tiff_is_recognised_page_by_page(fake_ocr):
    data = tiff_pages(3, (1500, 1000))
    before = bytes(data)
    output = extract_text(data, 'scan.TIF', 'eng', 80)
    assert output['text'] == 'page 1 text\n\npage 2 text\n\npage 3 text' and output['pages'] == 3
    assert 'variant 3' in output['searchText'] and 'variant 3' not in output['text']
    assert [call[1] for call in fake_ocr] == ['L', 'L', 'L']
    quality = output['quality']
    assert quality['ocrPages'] == 3 and quality['rotatedPages'] == 1 and quality['rotations'] == [0, 90, 0]
    assert quality['sparsePages'] == 1 and quality['meanConfidence'] == 90.0
    assert data == before


def test_scan_without_any_text_is_an_error_not_a_silent_ready(monkeypatch):
    monkeypatch.setattr('portal.file_processing.ocr_languages_ready', lambda languages: None)
    monkeypatch.setattr('portal.ocr.ocr_page', lambda *args, **kwargs: None)
    assert error_code(extract_text, raster('PNG'), 'blank.png', 'eng', 80) == 'OCR_NO_TEXT'


def test_ocr_failures_surface_as_processing_errors(monkeypatch):
    monkeypatch.setattr('portal.file_processing.ocr_languages_ready', lambda languages: None)
    def timeout(*args, **kwargs):
        raise ocr.OcrFailure('OCR_TIMEOUT')
    monkeypatch.setattr('portal.ocr.ocr_page', timeout)
    assert error_code(extract_text, raster('PNG'), 'a.png', 'eng', 80) == 'OCR_TIMEOUT'


# ---- 3. pictures inside DOCX -----------------------------------------------------

def docx_with(*pictures, paragraph='Body text first'):
    from docx import Document
    from docx.shared import Inches
    document = Document()
    if paragraph:
        document.add_paragraph(paragraph)
    for data in pictures:
        document.add_picture(io.BytesIO(data), width=Inches(2))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def picture(size, shade=0):
    image = Image.new('RGB', size, (255, 255, 255))
    ImageDraw.Draw(image).rectangle((5, 5, size[0] // 2, size[1] // 2), fill=(shade, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_docx_pictures_are_recognised_after_body_text_and_icons_skipped(fake_ocr):
    data = docx_with(picture((32, 32)), picture((900, 300), 10), picture((800, 400), 20))
    output = extract_text(data, 'with-pictures.docx', 'eng', 80)
    assert output['text'] == 'Body text first\n\npage 1 text\n\npage 2 text'
    assert len(fake_ocr) == 2  # the 32x32 logo is below the minimum size
    assert output['pages'] is None and output['quality']['embeddedImages'] == 2
    assert 'variant 1' in output['searchText']


def test_docx_without_pictures_never_touches_ocr(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('no OCR for a text-only DOCX')
    monkeypatch.setattr('portal.file_processing.subprocess.run', forbidden)
    monkeypatch.setattr('portal.ocr.ocr_page', forbidden)
    output = extract_text(docx_with(), 'plain.docx', 'eng', 80)
    assert output == {'text': 'Body text first', 'pages': None}


def test_docx_picture_count_and_duplicates_are_bounded(monkeypatch, fake_ocr):
    monkeypatch.setitem(__import__('portal.file_processing', fromlist=['x']).DOCX_IMAGE_LIMITS, 'count', 2)
    same = picture((700, 300), 5)
    output = extract_text(docx_with(same, same, picture((700, 300), 50), picture((700, 300), 90)), 'many.docx', 'eng', 80)
    assert len(fake_ocr) == 2 and output['quality']['embeddedImages'] == 2
    from docx import Document
    assert len(docx_pictures(Document(io.BytesIO(docx_with(same, same))))) == 1


def test_docx_picture_size_limits_skip_instead_of_failing(monkeypatch, fake_ocr):
    monkeypatch.setitem(__import__('portal.file_processing', fromlist=['x']).DOCX_IMAGE_LIMITS, 'bytes', 500)
    noisy = io.BytesIO()
    Image.frombytes('RGB', (200, 200), (bytes(range(256)) * 470)[:120000]).save(noisy, format='PNG')
    output = extract_text(docx_with(noisy.getvalue()), 'big.docx', 'eng', 80)
    assert output['text'].strip() == 'Body text first' and output['pages'] is None and not fake_ocr and 'quality' not in output


def test_docx_picture_without_text_keeps_the_body(monkeypatch):
    monkeypatch.setattr('portal.file_processing.ocr_languages_ready', lambda languages: None)
    monkeypatch.setattr('portal.ocr.ocr_page', lambda *args, **kwargs: None)
    output = extract_text(docx_with(picture((900, 300))), 'photo.docx', 'eng', 80)
    assert output['text'].strip() == 'Body text first'


def test_docx_active_content_checks_are_unchanged():
    from portal.file_processing import inspect_office
    import zipfile
    source = io.BytesIO(docx_with(picture((900, 300))))
    out = io.BytesIO()
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(out, 'w') as new:
        for item in old.infolist():
            payload = old.read(item.filename)
            if item.filename == 'word/_rels/document.xml.rels':
                payload = payload.replace(b'</Relationships>', b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="x.bin"/></Relationships>')
            new.writestr(item, payload)
    assert error_code(inspect_office, out.getvalue()) == 'ACTIVE_OFFICE_CONTENT'


# ---- 2/4. orientation and sparse text, with stand-in recognition passes -------------

def rows(text, confidence, count=1):
    return [{'text': text, 'confidence': confidence, 'box': (0, 20 * i, 400, 18)} for i in range(count)]


GOOD = rows('Quarterly report must be submitted before the fifth day', 94, 20)
GARBAGE = rows('JejWI ec2ned eA Ipejos ebgiyle) iudexuoe)', 40, 20)


class Recogniser:
    """Behaves like tesseract on a page that is really upright after `needs` degrees clockwise."""

    def __init__(self, needs=0, sparse=None, fail_after=None):
        self.needs, self.sparse, self.calls, self.fail_after = needs, sparse or [], [], fail_after

    def __call__(self, image, profile, deadline, psm=3):
        view = image.name
        self.calls.append((view, profile, psm))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise ocr.OcrFailure('OCR_TIMEOUT')
        if psm == 11:
            return self.sparse
        angle = 0 if not view.startswith('view-') else int(view.split('-')[1].split('.')[0])
        return GOOD if angle == self.needs else GARBAGE


@pytest.fixture
def page_file(tmp_path):
    path = tmp_path / 'page.png'
    image = Image.new('L', (400, 300), 255)
    ImageDraw.Draw(image).rectangle((20, 20, 300, 200), fill=0)
    image.save(path)
    return path


def far():
    return time.monotonic() + 500


def test_upright_page_pays_nothing_for_orientation(monkeypatch, page_file):
    recogniser = Recogniser()
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far())
    assert [call[1] for call in recogniser.calls] == ['Latin+uzb_cyrl+eng', 'Latin', 'uzb_cyrl']  # the old three passes
    assert result['quality']['rotation'] == 0 and result['quality']['sparse'] is False


@pytest.mark.parametrize('needs', [90, 180, 270])
def test_rotated_page_is_read_in_its_best_orientation(monkeypatch, page_file, needs):
    recogniser = Recogniser(needs)
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    state = {}
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), state)
    assert result['quality']['rotation'] == needs and 'Quarterly report' in result['text']
    assert result['quality']['meanConfidence'] == 94.0
    assert state['rotation'] == needs
    # the orientation is remembered: the next page starts with it and needs no retries
    recogniser.calls.clear()
    ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), state)
    assert recogniser.calls[0][0] == f'view-{needs}.png' and len(recogniser.calls) == 3


def test_retries_stop_when_the_time_budget_is_spent(monkeypatch, page_file):
    recogniser = Recogniser(90)
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), {}, probe_deadline=time.monotonic() - 1)
    assert result['quality']['rotation'] == 0 and all(not call[0].startswith('view-') for call in recogniser.calls)
    assert 'JejWI' in result['text']  # best result so far, with its poor confidence recorded
    assert result['quality']['meanConfidence'] == 40.0


def test_budget_running_out_during_retries_keeps_the_best_result(monkeypatch, page_file):
    recogniser = Recogniser(180, fail_after=2)  # baseline and the 90-degree probe succeed, then time is up
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), {})
    assert result['quality']['rotation'] == 0 and result['text']


def test_budget_running_out_in_script_passes_after_rotation_keeps_the_combined_pass(monkeypatch, page_file):
    recogniser = Recogniser(90, fail_after=2)  # baseline, 90-degree probe (good), then Latin pass times out
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), {})
    assert result['quality']['rotation'] == 90 and result['quality']['profiles'] == ['Latin+uzb_cyrl+eng']


def test_an_upright_page_timeout_is_still_an_error(monkeypatch, page_file):
    monkeypatch.setattr(ocr, 'run_pass', Recogniser(fail_after=1))
    with pytest.raises(ocr.OcrFailure) as caught:
        ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far(), {})
    assert str(caught.value) == 'OCR_TIMEOUT'


def test_marginal_improvement_does_not_rotate(monkeypatch, page_file):
    def passes(image, profile, deadline, psm=3):
        return rows('slightly noisy but real text here today', 66, 20 if image.name == 'page.png' else 22)
    monkeypatch.setattr(ocr, 'run_pass', passes)
    state = {}
    assert ocr.ocr_page(page_file, 'eng', far(), state)['quality']['rotation'] == 0
    assert ocr.ocr_page(page_file, 'eng', far(), state)['quality']['rotation'] == 0
    assert state['probe'] is False  # two pages where retries did not help: stop paying for them


def test_blank_page_is_not_rotated_or_sparsely_scanned(monkeypatch, tmp_path):
    path = tmp_path / 'blank.png'
    Image.new('L', (400, 300), 255).save(path)
    recogniser = Recogniser()
    monkeypatch.setattr(ocr, 'run_pass', lambda *args, **kwargs: recogniser(*args, **kwargs) and [])
    assert ocr.ocr_page(path, 'Latin+uzb_cyrl+eng', far()) is None
    assert len(recogniser.calls) == 1


def test_sparse_words_reach_the_search_index_only(monkeypatch, page_file):
    labels = [{'text': 'Plita 12mm', 'confidence': 91, 'box': (0, 0, 1, 1)}, {'text': 'Ø200', 'confidence': 88, 'box': (0, 0, 1, 1)},
              {'text': 'xx', 'confidence': 20, 'box': (0, 0, 1, 1)}, {'text': '|', 'confidence': 97, 'box': (0, 0, 1, 1)}]
    recogniser = Recogniser(sparse=labels)
    def few_words(image, profile, deadline, psm=3):
        recogniser.calls.append((image.name, profile, psm))
        return rows('A-A Plita', 90) if psm == 3 else labels
    monkeypatch.setattr(ocr, 'run_pass', few_words)
    result = ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far())
    assert result['text'] == 'A-A Plita' and result['quality']['sparse'] is True
    assert 'Plita 12mm' in result['searchText'] and 'Ø200' in result['searchText']
    assert 'xx' not in result['searchText'] and '|' not in result['searchText'].split('\n')
    sparse_calls = [call for call in recogniser.calls if call[2] == 11]
    assert len(sparse_calls) == 3  # upright, +90 and +270 views of a very sparse page
    assert all(call[0] != 'page.png' for call in sparse_calls)  # plain copies without the resolution tag


def test_dense_pages_do_not_run_the_sparse_pass(monkeypatch, page_file):
    recogniser = Recogniser()
    monkeypatch.setattr(ocr, 'run_pass', recogniser)
    ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far())
    assert all(call[2] == 3 for call in recogniser.calls)


def test_sparse_pass_failure_never_fails_the_page(monkeypatch, page_file):
    def passes(image, profile, deadline, psm=3):
        if psm == 11:
            raise ocr.OcrFailure('OCR_FAILED')
        return rows('A-A Plita', 90)
    monkeypatch.setattr(ocr, 'run_pass', passes)
    assert ocr.ocr_page(page_file, 'Latin+uzb_cyrl+eng', far())['text'] == 'A-A Plita'


def test_rotation_score_prefers_many_confident_characters_over_confidence_alone():
    lucky = ocr.page_stats(rows('ok', 99))
    real = ocr.page_stats(GOOD)
    garbage = ocr.page_stats(GARBAGE)
    assert real['score'] > 4 * garbage['score'] and real['score'] > 20 * lucky['score']
    assert ocr.is_poor(garbage) and ocr.is_poor(lucky) and not ocr.is_poor(real)


def test_recognize_remains_available_for_single_images(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, 'run_pass', lambda image, profile, deadline, psm=3: rows('plain', 90))
    assert ocr.recognize(tmp_path / 'x.png', 'eng', far())['text'] == 'plain'


# ---- 5. protocol, search, download ----------------------------------------------------

@pytest.mark.parametrize('quality', [
    {}, {'engine': 'tesseract-script-ensemble', 'ocrPages': 2, 'meanConfidence': None, 'lowConfidenceLines': 0, 'profiles': ['a']},
    {'meanConfidence': 90, 'rotatedPages': 1, 'sparsePages': 0, 'embeddedImages': 2, 'rotations': [0, 90, 180, 270]}])
def test_stored_and_new_quality_shapes_validate(quality):
    assert valid_ocr_quality(quality)


@pytest.mark.parametrize('quality', [
    {'rotations': [45]}, {'rotations': [True]}, {'rotations': 'x'}, {'rotations': [0] * 201}, {'rotatedPages': -1},
    {'rotatedPages': True}, {'sparsePages': 1.5}, {'embeddedImages': 'many'}, {'unknown': 1}])
def test_new_quality_fields_are_validated(quality):
    assert not valid_ocr_quality(quality)


def test_search_folding_treats_diameter_signs_like_o():
    assert search_fold('Ø200') == search_fold('⌀10') .replace('10', '200') == 'o200'
    assert search_fold('Özbek') == 'ozbek'


def post_file(api, name, data):
    return api.client.post('/api/v1/files', {'file': SimpleUploadedFile(name, data), 'purpose': 'DOCUMENT'},
                           HTTP_ORIGIN=settings.APP_ORIGIN, HTTP_X_CSRFTOKEN=api.token)


@pytest.mark.django_db
def test_upload_accepts_image_scans_and_still_rejects_other_formats(hr):
    api = hr[2]
    for name, data in [('scan.jpg', raster('JPEG')), ('scan.PNG', raster('PNG')), ('scan.tif', tiff_pages(1)), ('scan.tiff', tiff_pages(1))]:
        assert post_file(api, name, data).status_code == 202, name
    for name in ['scan.webp', 'scan.gif', 'scan.bmp', 'scan.svg']:
        response = post_file(api, name, raster('PNG'))
        assert response.status_code == 400 and response.json()['code'] == 'UNSUPPORTED_FORMAT'
    jobs = m.ProcessingJob.objects.filter(kind='SCAN')
    assert jobs.count() == 4 and not m.ProcessingJob.objects.exclude(kind='SCAN').exists()


@pytest.mark.django_db
@pytest.mark.parametrize('mime,status', [('image/png', 200), ('image/jpeg', 200), ('image/tiff', 200), ('image/webp', 400)])
def test_scan_result_mime_types_for_documents(hr, mime, status):
    user, _, api = hr
    file = m.FileObject.objects.create(original_name='scan.bin', mime_type='application/octet-stream', size_bytes=10, checksum='0' * 64,
                                       purpose='DOCUMENT', security_status='SCANNING', created_by=user)
    file.file.save('x.upload', __import__('django.core.files.base', fromlist=['x']).ContentFile(b'0123456789'), save=False)
    file.save()
    job, token = running_job(file, 'SCAN')
    response = result(api, job, token, {'status': 'APPROVED', 'scannerVersion': 'test-scanner', 'signatureVersion': 'test-signatures', 'mimeType': mime})
    assert response.status_code == status
    file.refresh_from_db()
    assert (file.security_status, file.mime_type) == (('APPROVED', mime) if status == 200 else ('SCANNING', 'application/octet-stream'))


@pytest.mark.django_db
def test_extraction_result_with_new_metadata_is_stored_and_searchable(hr):
    user, _, api = hr
    document = m.Document.objects.create(title='Drawing', category='REGULATION', created_by=user, status='PUBLISHED')
    file = approved_fixture(user, 'plan.png', b'\x89PNG fixture')
    version = m.DocumentVersion.objects.create(document=document, label='1', file=file, created_by=user)
    document.current_version = version
    document.save()
    job, token = running_job(file)
    quality = {'engine': 'tesseract-script-ensemble', 'ocrPages': 1, 'meanConfidence': 88.5, 'lowConfidenceLines': 0, 'profiles': ['Latin'],
               'rotatedPages': 1, 'sparsePages': 1, 'rotations': [90]}
    response = result(api, job, token, {'text': 'A-A Plita 12mm', 'searchText': 'A-A Plita 12mm\nØ200\nsparsevariantword', 'pages': 1, 'quality': quality})
    assert response.status_code == 200, response.content
    version.refresh_from_db()
    assert version.ocr_quality == quality and 'sparsevariantword' not in version.extracted_text
    assert api.get('/api/v1/documents?inside=true&q=sparsevariantword').json()['total'] == 1
    assert api.get('/api/v1/documents?inside=true&q=%C3%B8200').json()['total'] == 1
    assert api.get('/api/v1/search?q=sparsevariantword').json()['documents']['total'] == 1
    detail = api.get(f'/api/v1/documents/{document.id}/versions/{version.id}').json()
    assert detail['ocrQuality']['rotatedPages'] == 1 and 'sparsevariantword' not in detail['previewText']


@pytest.mark.django_db
def test_old_extraction_results_without_new_fields_still_load(hr):
    user, _, api = hr
    file = approved_fixture(user, 'old.pdf')
    job, token = running_job(file)
    document = m.Document.objects.create(title='Old', category='REGULATION', created_by=user, status='PUBLISHED')
    version = m.DocumentVersion.objects.create(document=document, label='1', file=file, created_by=user)
    response = result(api, job, token, {'text': 'old style', 'pages': 1})
    assert response.status_code == 200
    m.DocumentVersion.objects.filter(pk=version.pk).update(ocr_quality={'meanConfidence': 80, 'lowConfidenceLines': 2, 'ocrPages': 1, 'engine': 'x', 'profiles': []})
    document.current_version = version
    document.save()
    detail = api.get(f'/api/v1/documents/{document.id}/versions/{version.id}')
    assert detail.status_code == 200 and detail.json()['ocrQuality']['lowConfidenceLines'] == 2


@pytest.mark.django_db
@pytest.mark.parametrize('mime,inline', [('image/png', True), ('image/jpeg', True), ('application/pdf', True), ('image/tiff', False)])
def test_scan_download_and_inline_preview(hr, mime, inline):
    user, _, api = hr
    file = approved_fixture(user, 'scan.bin', b'0123456789')
    file.mime_type = mime
    file.save()
    response = api.client.get(f'/api/v1/files/{file.id}/download?inline=true')
    assert response.status_code == 200 and response['Content-Type'] == mime
    assert response['X-Content-Type-Options'] == 'nosniff'
    assert response['Content-Disposition'].startswith('inline') == inline
    assert api.client.get(f'/api/v1/files/{file.id}/download')['Content-Disposition'].startswith('attachment')
