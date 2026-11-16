"""Bounded OCR with script-specific recognition and explicit quality metadata.

Secondary real recognition hypotheses aid search only. Never rewrite original files
or manufacture Uzbek spelling using a dictionary.

Page pipeline (ocr_page): combined-language pass -> if the result is poor, try the
page rotated by 90/180/270 degrees and keep the best by a character-and-confidence
score -> script ensemble on the chosen orientation -> for sparse pages (drawings,
stamps) an extra sparse-text pass whose words go to the search variants only.
No orientation model is used (no osd.traineddata): orientation is decided by
recognising the page, which needs only the models already installed.
"""
import csv
import io
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

# A page is "poor" when the combined pass has a character-weighted confidence below
# this value or fewer words than POOR_WORDS. Measured (docs/OCR_RESULTS.md): upright
# dense pages 92-96, the same pages in a wrong orientation 38-43.
POOR_CONFIDENCE = 70
POOR_WORDS = 4
# A rotation replaces the upright result only if it is clearly better.
ROTATION_MARGIN = 1.5
ROTATION_MIN_SCORE = 8
ROTATIONS = (0, 90, 180, 270)
# Pages with fewer main-pass words than this get a sparse-text pass; very sparse
# pages whose ink is mostly not text (drawings) are additionally read sideways.
# Dense pages never pay for it.
SPARSE_WORDS = 60
VERY_SPARSE_WORDS = 20
GRAPHIC_INK = 0.004
SPARSE_MIN_CONFIDENCE = 60
SPARSE_MAX_LINES = 400
# Pages that already read as blank paper are not worth orientation retries.
BLANK_DARK_FRACTION = 0.0005


class OcrFailure(Exception):
    pass


def parse_tsv(value):
    lines = {}
    for row in csv.DictReader(io.StringIO(value), delimiter='\t', quoting=csv.QUOTE_NONE):
        try:
            if int(row['level']) != 5 or not row.get('text', '').strip():
                continue
            confidence = float(row['conf'])
            box = tuple(int(row[k]) for k in ('left', 'top', 'width', 'height'))
            if not 0 <= confidence <= 100 or min(box) < 0:
                continue
            key = tuple(int(row[k]) for k in ('page_num', 'block_num', 'par_num', 'line_num'))
        except (KeyError, TypeError, ValueError):
            raise OcrFailure('OCR_INVALID_RESULT') from None
        lines.setdefault(key, []).append((row['text'].strip(), confidence, box))
        if len(lines) > 5000:
            raise OcrFailure('OCR_RESULT_LIMIT')
    result = []
    for words in lines.values():
        left = min(w[2][0] for w in words)
        top = min(w[2][1] for w in words)
        right = max(w[2][0]+w[2][2] for w in words)
        bottom = max(w[2][1]+w[2][3] for w in words)
        weight = sum(len(w[0]) for w in words)
        result.append({'text': ' '.join(w[0] for w in words),
                       'confidence': sum(len(w[0])*w[1] for w in words)/weight,
                       'box': (left, top, right-left, bottom-top)})
    return result


def overlap(a, b):
    x, y, w, h = a
    xx, yy, ww, hh = b
    area = max(0, min(x+w, xx+ww)-max(x, xx))*max(0, min(y+h, yy+hh)-max(y, yy))
    return area/max(1, min(w*h, ww*hh))


def check_languages(languages):
    names = languages.split('+')
    if not names or any(not re.fullmatch(r'[A-Za-z_]+', name) for name in names):
        raise OcrFailure('OCR_LANGUAGES_UNAVAILABLE')
    return names


def run_pass(image, profile, deadline, psm=3):
    """One Tesseract run; returns recognised lines. Paths are arguments, never a shell string."""
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise OcrFailure('OCR_TIMEOUT')
    try:
        value = subprocess.run(['tesseract', str(image), 'stdout', '-l', profile,
                                '--psm', str(psm), '--oem', '1',
                                '-c', 'tessedit_create_tsv=1', '-c', 'tessedit_create_txt=0'],
                               capture_output=True, text=True, timeout=min(60, remaining),
                               check=True, env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
    except subprocess.TimeoutExpired:
        raise OcrFailure('OCR_TIMEOUT') from None
    except (OSError, subprocess.CalledProcessError):
        raise OcrFailure('OCR_FAILED') from None
    if len(value.stdout) > 8*1024*1024:
        raise OcrFailure('OCR_RESULT_LIMIT')
    return parse_tsv(value.stdout)


def alnum(text):
    return sum(c.isalnum() for c in text)


def page_stats(rows):
    """Character-weighted confidence and a score that rewards many confident characters.

    score = sum(alphanumeric characters * (confidence/100)^2). A page in the wrong
    orientation yields many low-confidence characters and scores several times below the
    upright page, while a nearly empty page cannot win on a few lucky words."""
    chars = sum(alnum(r['text']) for r in rows)
    words = sum(len(r['text'].split()) for r in rows)
    if not chars:
        return {'words': words, 'chars': 0, 'confidence': 0.0, 'score': 0.0}
    return {'words': words, 'chars': chars,
            'confidence': sum(alnum(r['text'])*r['confidence'] for r in rows)/chars,
            'score': sum(alnum(r['text'])*(r['confidence']/100)**2 for r in rows)}


def is_poor(stats):
    return stats['confidence'] < POOR_CONFIDENCE or stats['words'] < POOR_WORDS


def select_lines(variants, languages):
    baseline = variants[languages]
    selected = []
    for line in baseline:
        letters = [c for c in line['text'] if c.isalpha()]
        cyrillic = sum('Ѐ' <= c <= 'ԯ' for c in letters)
        preferred = 'uzb_cyrl' if cyrillic > len(letters)/2 else 'Latin'
        choices = []
        for profile in ['Latin', 'uzb_cyrl']:
            matches = [(overlap(line['box'], other['box']), other)
                       for other in variants.get(profile, [])]
            matches = [(score, other) for score, other in matches if score >= .65]
            if matches:
                candidate = max(matches, key=lambda item: item[0])[1]
                letters = [c for c in candidate['text'] if c.isalpha()]
                cyrillic = sum('Ѐ' <= c <= 'ԯ' for c in letters)
                actual_script = 'uzb_cyrl' if cyrillic > len(letters)/2 else 'Latin'
                if letters and actual_script == profile:
                    choices.append((candidate['confidence'], profile == preferred, candidate))
        # A combined-language pass may render English in Cyrillic homoglyphs.
        # Compare independently recognized lines; script majority only breaks
        # confidence ties. No vocabulary or hard-coded word substitutions.
        candidate = max(choices, key=lambda item: item[:2])[2] if choices else line
        selected.append(candidate if candidate['confidence'] >= 35 else line)
    return selected


def ensemble(image, languages, deadline, baseline=None, keep_on_timeout=False):
    """Combined pass plus independent script passes; `baseline` reuses an existing combined pass.
    With `keep_on_timeout` an exhausted budget keeps the passes finished so far."""
    names = check_languages(languages)
    profiles = [languages]
    if 'Latin' in names and 'uzb_cyrl' in names:
        profiles.extend(['Latin', 'uzb_cyrl'])
    variants = {languages: baseline if baseline is not None else run_pass(image, languages, deadline)}
    if variants[languages]:
        for profile in profiles[1:]:
            try:
                variants[profile] = run_pass(image, profile, deadline)
            except OcrFailure as error:
                if keep_on_timeout and str(error) == 'OCR_TIMEOUT':
                    break
                raise
    selected = select_lines(variants, languages) if variants[languages] else []
    search = list(dict.fromkeys(line['text'] for rows in variants.values() for line in rows))
    return selected, search, list(variants)


def quality_of(selected, profiles):
    return {'meanConfidence': round(sum(v['confidence'] for v in selected)/len(selected), 2) if selected else 0.0,
            'lowConfidenceLines': sum(v['confidence'] < 65 for v in selected),
            'lineCount': len(selected), 'profiles': profiles}


def recognize(image, languages, deadline):
    selected, search, profiles = ensemble(image, languages, deadline)
    if not selected:
        raise OcrFailure('OCR_NO_TEXT')
    text = '\n'.join(line['text'] for line in selected)
    search = '\n'.join(search)
    if max(len(text), len(search)) > 2_000_000:
        raise OcrFailure('TEXT_LIMIT')
    return {'text': text, 'searchText': search, 'quality': quality_of(selected, profiles)}


class Views:
    """Rotated copies of one page raster, created on demand in a private folder."""

    def __init__(self, image, folder):
        self.image, self.folder, self.paths = Path(image), Path(folder), {}

    def path(self, rotation, plain=False):
        """`rotation` degrees clockwise. The ordinary view keeps the raster's resolution tag
        (identical to the previous behaviour at 0 degrees); the plain view drops it so that
        Tesseract estimates resolution itself, which matters for line drawings."""
        if rotation == 0 and not plain:
            return self.image
        key = (rotation, plain)
        if key not in self.paths:
            from PIL import Image
            with Image.open(self.image) as source:
                dpi = source.info.get('dpi')
                view = source.convert('L') if plain else source.copy()
                if rotation:
                    view = view.rotate(-rotation, expand=True, fillcolor=255 if view.mode == 'L' else (255, 255, 255))
                target = self.folder/f'view-{rotation}{"-plain" if plain else ""}.png'
                if dpi and not plain:
                    view.save(target, dpi=dpi)
                else:
                    view.save(target)
            self.paths[key] = target
        return self.paths[key]

    def blank(self):
        from PIL import Image
        with Image.open(self.image) as source:
            gray = source.convert('L')
            gray.thumbnail((800, 800))
            histogram = gray.histogram()
        return sum(histogram[:128]) < BLANK_DARK_FRACTION*max(1, sum(histogram))


def graphic_ink(image, boxes):
    """Fraction of the page covered by dark pixels outside the recognised text lines (drawing lines,
    shapes, stamps). Plain text pages score near zero; technical drawings do not."""
    from PIL import Image, ImageChops, ImageDraw
    with Image.open(image) as source:
        gray = source.convert('L')
        scale = min(1.0, 800/max(gray.size))
        gray = gray.resize((max(1, int(gray.width*scale)), max(1, int(gray.height*scale))))
    ink = gray.point(lambda value: 255 if value < 128 else 0)
    mask = Image.new('L', gray.size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, width, height in boxes:
        draw.rectangle((left*scale-2, top*scale-2, (left+width)*scale+2, (top+height)*scale+2), fill=255)
    outside = ImageChops.subtract(ink, mask).histogram()[255]
    return outside/max(1, gray.width*gray.height)


def choose_rotation(order, results):
    """results: rotation -> (rows, stats). Upright is kept unless another rotation is clearly better."""
    best = max(results, key=lambda r: (results[r][1]['score'], r == order[0]))
    upright = results.get(0)
    if best != 0 and upright is not None:
        floor = max(ROTATION_MIN_SCORE, upright[1]['score']*ROTATION_MARGIN)
        if results[best][1]['score'] < floor:
            return 0
    return best


def sparse_lines(views, rotation, languages, deadline, probe_deadline, sideways):
    """Sparse-text recognition (psm 11) of the corrected page and, for very sparse pages, its sideways views."""
    found = []
    angles = [rotation]+([(rotation+90) % 360, (rotation+270) % 360] if sideways else [])
    for angle in angles:
        if probe_deadline is not None and time.monotonic() > probe_deadline:
            break
        try:
            rows = run_pass(views.path(angle, plain=True), languages, deadline, psm=11)
        except OcrFailure:
            break  # optional pass: never fails the file, keeps what exists
        for row in rows:
            if row['confidence'] >= SPARSE_MIN_CONFIDENCE and alnum(row['text']) >= 2:
                found.append(row['text'])
    return list(dict.fromkeys(found))[:SPARSE_MAX_LINES]


def ocr_page(image, languages, deadline, state=None, probe_deadline=None):
    """OCR one page raster. `state` carries orientation knowledge across the pages of one file;
    `probe_deadline` is the time after which optional extra passes (rotations, sparse text) stop.
    Returns text/searchText/quality, or None when the page has no recognisable text."""
    check_languages(languages)
    state = state if state is not None else {}
    hint = state.get('rotation', 0)
    order = [hint]+[r for r in ROTATIONS if r != hint]
    tried_extra = False
    with tempfile.TemporaryDirectory(prefix='portal-ocr-view-') as folder:
        views = Views(image, folder)
        results = {}
        for position, rotation in enumerate(order):
            if position:
                if not state.get('probe', True) or (probe_deadline is not None and time.monotonic() > probe_deadline):
                    break
                if position == 1 and views.blank():
                    break
                tried_extra = True
            try:
                rows = run_pass(views.path(rotation), languages, deadline)
            except OcrFailure:
                if position:
                    break  # budget exhausted or optional pass failed: keep the best so far
                raise
            results[rotation] = (rows, page_stats(rows))
            if not is_poor(results[rotation][1]):
                break
        chosen = choose_rotation(order, results)
        rows, stats = results[chosen]
        selected, search, profiles = ensemble(views.path(chosen), languages, deadline, baseline=rows, keep_on_timeout=tried_extra)
        quality = quality_of(selected, profiles)
        quality.update(rotation=chosen, words=stats['words'], sparse=False)
        extra = []
        if (stats['words'] < SPARSE_WORDS and (probe_deadline is None or time.monotonic() <= probe_deadline)
                and not (stats['words'] == 0 and views.blank())):
            sideways = False
            if stats['words'] < VERY_SPARSE_WORDS:
                try:
                    sideways = graphic_ink(views.path(chosen), [line['box'] for line in rows]) >= GRAPHIC_INK
                except OSError:
                    sideways = False
            extra = sparse_lines(views, chosen, languages, deadline, probe_deadline, sideways)
            quality['sparse'] = True
    # Orientation memory for the following pages of the same file.
    if chosen != 0:
        state['rotation'], state['misses'] = chosen, 0
    elif hint:
        state['rotation'] = 0
    elif tried_extra:
        state['misses'] = state.get('misses', 0)+1
        if state['misses'] >= 2:
            state['probe'] = False  # this file's poor pages are not an orientation problem
    if not selected and not extra:
        return None
    known = {line['text'].casefold() for line in selected}
    text = '\n'.join(line['text'] for line in selected)
    search_text = '\n'.join(dict.fromkeys(search+[line for line in extra if line.casefold() not in known]))
    if max(len(text), len(search_text)) > 2_000_000:
        raise OcrFailure('TEXT_LIMIT')
    return {'text': text, 'searchText': search_text, 'quality': quality}
