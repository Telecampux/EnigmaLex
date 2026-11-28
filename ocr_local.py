"""OCR Tesseract local, sin enviar imágenes ni texto a servicios externos."""
import csv
import io
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from procesamiento import check_cancelled
from entidades import edit_distance, normalized_word


class OCRError(RuntimeError):
    pass


def executable():
    candidates = [os.environ.get('TESSERACT_CMD'),
                  str(Path(__file__).resolve().parent / 'Tesseract-OCR/tesseract.exe'),
                  shutil.which('tesseract'),
                  str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tesseract-OCR/tesseract.exe'),
                  str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Tesseract-OCR/tesseract.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise OCRError('Falta Tesseract OCR. Ejecute instalar_ocr.ps1 para instalarlo.')


def _recognize_pass(image, psm=3, threshold_method=None):
    check_cancelled()
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='PNG')
    tessdata = Path(__file__).resolve().parent / 'tessdata'
    command = [executable(), 'stdin', 'stdout']
    if (tessdata / 'spa.traineddata').is_file() and (tessdata / 'eng.traineddata').is_file():
        command += ['--tessdata-dir', str(tessdata)]
    command += ['-l', 'spa+eng', '--psm', str(psm), '-c', 'tessedit_create_tsv=1']
    if threshold_method is not None:
        command += ['--dpi', '300', '-c', f'thresholding_method={threshold_method}']
    try:
        environment = os.environ.copy()
        environment.setdefault('OMP_THREAD_LIMIT', '1')
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            deadline = time.monotonic() + 180
            payload = buffer.getvalue()
            try:
                while True:
                    check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 180)
                    try:
                        stdout, stderr = process.communicate(input=payload, timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
                check_cancelled()
            except BaseException:
                process.kill()
                process.communicate()
                raise
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OCRError('No se pudo completar el OCR. No se generó una copia anonimizada.') from exc
    if result.returncode:
        raise OCRError('Tesseract no pudo leer la imagen. Compruebe los idiomas español e inglés con instalar_ocr.ps1.')
    words, pieces, offset, previous = [], [], 0, None
    for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8-sig')), delimiter='\t', quoting=csv.QUOTE_NONE):
        text = (row.get('text') or '').strip()
        if row.get('level') != '5' or not text:
            continue
        line = tuple(row[key] for key in ('page_num', 'block_num', 'par_num', 'line_num'))
        separator = '' if previous is None else (' ' if line == previous else '\n')
        pieces.extend((separator, text))
        offset += len(separator)
        x, y, w, h = (int(row[key]) for key in ('left', 'top', 'width', 'height'))
        words.append((offset, offset + len(text), (x, y, x+w, y+h), float(row['conf'])))
        offset += len(text)
        previous = line
    return ''.join(pieces), words


def recognize(image):
    """Read normal text and sparse forms, preserving coordinates on the original.

    Additional passes keep their own reading order and image coordinates.
    The caller merges proposals that cover the same physical words.
    """
    from PIL import ImageOps, ImageFilter
    check_cancelled()
    image = image.convert('RGB')
    denoised = ImageOps.grayscale(image).filter(ImageFilter.GaussianBlur(1))
    text, words = _recognize_pass(denoised, threshold_method=2)
    enhanced = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1, percent=130, threshold=3))
    # A legible page is already located by the full-page pass. The expensive
    # sparse and cropped passes are reserved for pages with little usable text.
    substantial = sum(len(text[a:b]) >= 4 and confidence >= 35
                      for a, b, _, confidence in words)
    if len(words) >= 80 and substantial >= max(20, len(words) // 3):
        return text, words
    passes = [(enhanced, 11, 0, 0)]
    # Camera scans can turn paper texture into hundreds of spurious letters.
    # Denoising before binarization recovers dark print on shaded ID cards.
    if len(words) < 80 or substantial < max(10, len(words) // 3):
        smoothed = ImageOps.grayscale(image).filter(ImageFilter.GaussianBlur(1.2))
        for threshold in (85, 110):
            binary = smoothed.point(lambda value, limit=threshold: 255 if value > limit else 0)
            passes.append((binary, 11, 0, 0))
    # Columns isolate the small label/value pairs of clinical forms from the
    # neighbouring columns that otherwise interrupt the patient's name/address.
    for column in range(3):
        left = max(0, image.width * column // 3 - image.width // 40)
        right = min(image.width, image.width * (column + 1) // 3 + image.width // 40)
        passes.append((enhanced.crop((left, 0, right, image.height)), 3, left, 0))
    for column in range(4):
        left = image.width * column // 4
        right = min(image.width, left + image.width * 2 // 5)
        passes.append((enhanced.crop((left, 0, right, image.height * 2 // 5)), 6, left, 0))
    # Two independent OCR workers match the two physical cores of the older
    # machines used for this application. Preserve pass order for offsets.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: _recognize_pass(item[0], item[1]), passes))
    for (crop, psm, dx, dy), (extra_text, extra_words) in zip(passes, results):
        check_cancelled()
        # A strip may cut a word (e.g. example.com -> example.cor).
        # Never propose those fragments as independent sensitive values.
        kept = []
        characters = list(extra_text)
        for a, b, box, confidence in extra_words:
            x0, y0, x1, y1 = box
            clipped = ((dx > 0 and x0 <= 4) or
                       (dx + crop.width < image.width and x1 >= crop.width - 4) or
                       (dy + crop.height < image.height and y1 >= crop.height - 4))
            if clipped:
                characters[a:b] = ' ' * (b-a)
            else:
                kept.append((a, b, box, confidence))
        extra_text = ''.join(characters)
        offset = len(text) + 1
        text += '\n' + extra_text
        for a, b, (x0, y0, x1, y1), confidence in kept:
            words.append((offset + a, offset + b,
                          (x0 + dx, y0 + dy, x1 + dx, y1 + dy), confidence))
    return text, words


_HANDWRITTEN_LABEL = re.compile(
    r'(?im)^[ \t*•-]*(?P<label>nombres?(?:[ \t]+(?:y[ \t]+apellidos|completo|del[ \t]+paciente))?'
    r'|apellidos?|firma(?:[ \t]+del[ \t]+(?:paciente|solicitante))?'
    r'|c[ée]dula(?:[ \t]+de[ \t]+ciudadan[íi]a)?|identificaci[óo]n|c\.?c\.?'
    r'|accionante|radicado|nui|t\.?d\.?|patio|huella)'
    r'[ \t]*(?P<colon>:|(?=$))')


def _unread_ink(image, probe, printed):
    """Find pen strokes left after accounting for confidently read print."""
    from PIL import ImageChops, ImageDraw

    left, top, right, bottom = probe
    if right - left < 20 or bottom - top < 8:
        return None
    crop = image.crop(probe)
    red, _, blue = crop.split()
    blue_ink = ImageChops.subtract(blue, red).point(lambda value: 255 if value >= 27 else 0)
    dark_ink = crop.convert('L').point(lambda value: 255 if value < 155 else 0)
    drawing = ImageDraw.Draw(dark_ink)
    for x0, y0, x1, y1 in printed:
        if x1 > left and x0 < right and y1 > top and y0 < bottom:
            drawing.rectangle((x0-left-2, y0-top-2, x1-left+2, y1-top+2), fill=0)
    ink = ImageChops.lighter(dark_ink, blue_ink)
    width, height = ink.size
    pixels = ink.tobytes()
    rows = []
    for row in range(height):
        line = pixels[row*width:(row+1)*width]
        count = line.count(255)
        if 2 <= count < width * .65:  # Ignore printed horizontal rules.
            rows.append((row, line.find(b'\xff'), line.rfind(b'\xff') + 1, count))
    if len(rows) < 5 or sum(row[3] for row in rows) < 25:
        return None
    x0, x1 = min(row[1] for row in rows), max(row[2] for row in rows)
    y0, y1 = rows[0][0], rows[-1][0] + 1
    if x1 - x0 < 12 or y1 - y0 < 6:
        return None
    margin = max(4, round((y1-y0) * .15))
    return (max(left, left+x0-margin), max(top, top+y0-margin),
            min(right, left+x1+margin), min(bottom, top+y1+margin))


def _read_handwritten_line(crop):
    from PIL import ImageOps

    crop = ImageOps.autocontrast(crop.convert('L'))
    if crop.height < 64:
        crop = ImageOps.scale(crop, min(3, 64 / crop.height))
    crop = ImageOps.expand(crop, border=12, fill=255)
    candidates = [_recognize_pass(crop, mode) for mode in (7, 13)]
    reading, tokens = max(candidates, key=lambda item: sum(
        confidence for _, _, _, confidence in item[1]) / max(1, len(item[1])))
    return reading.strip() if tokens and min(word[3] for word in tokens) >= 55 else ''


def _blue_ink_lines(image):
    """Find line-sized regions of blue ink, including fields without a label."""
    from PIL import ImageChops

    red, _, blue = image.split()
    saturation = ImageChops.subtract(blue, red).point(lambda value: 255 if value >= 27 else 0)
    darkness = red.point(lambda value: 255 if value < 180 else 0)
    mask = ImageChops.darker(saturation, darkness)
    bounds = mask.getbbox()
    if bounds is None:
        return
    left, top, right, bottom = bounds
    width = right - left
    pixels = mask.crop(bounds).tobytes()
    gap = max(3, round(image.height * .002))
    rows = []
    for y in range(bottom - top):
        if y % 128 == 0:
            check_cancelled()
        line = pixels[y * width:(y + 1) * width]
        count = line.count(255)
        if 2 <= count <= image.width * .65:
            rows.append((y, line.find(b'\xff'), line.rfind(b'\xff') + 1, count))
    start = 0
    for stop in range(1, len(rows) + 1):
        if stop < len(rows) and rows[stop][0] - rows[stop - 1][0] <= gap:
            continue
        band = rows[start:stop]
        start = stop
        height = band[-1][0] - band[0][0] + 1
        x0 = min(row[1] for row in band)
        x1 = max(row[2] for row in band)
        if not (6 <= height <= max(48, image.height * .09)
                and x1 - x0 >= 12 and sum(row[3] for row in band) >= 25):
            continue
        margin = max(4, round(height * .15))
        box = (max(0, left + x0 - margin), max(0, top + band[0][0] - margin),
               min(image.width, left + x1 + margin), min(image.height, top + band[-1][0] + 1 + margin))
        yield box, ImageChops.invert(mask.crop(box))


def handwriting_regions(image, text, words):
    """Locate handwritten fields and isolated blue ink, preserving scan coordinates."""
    from PIL import ImageDraw

    width, height = image.size
    printed = [box for _, _, box, confidence in words if confidence >= 72]
    regions = []
    anchors = []
    for match in _HANDWRITTEN_LABEL.finditer(text):
        check_cancelled()
        boxes = [box for a, b, box, confidence in words
                 if a < match.end() and b > match.start('label') and confidence >= 45]
        if not boxes:
            continue
        x0, y0, x1, y1 = (round(min(box[0] for box in boxes)), round(min(box[1] for box in boxes)),
                          round(max(box[2] for box in boxes)), round(max(box[3] for box in boxes)))
        line = max(8, y1 - y0)
        if any(abs(x0-x) < line and abs(y0-y) < line for x, y in anchors):
            continue
        anchors.append((x0, y0))
        signature = match['label'].casefold().startswith('firma')
        probe_right = min(width, x1 + max(14 * line, width // 2))
        probes = [(max(0, x1 + 2), max(0, y0-line//2), probe_right, min(height, y1+line))]
        next_text = text[match.end():].split('\n', 1)[0].strip()
        if not next_text or signature:
            left = max(0, x0-line)
            probes.append((left, y1+2, probe_right, min(height, y1+2*line)))
            if signature:
                probes.append((left, max(0, y0-3*line), probe_right, max(0, y0-2)))
        for probe in probes:
            box = _unread_ink(image, probe, printed)
            if not box:
                continue
            if any(min(box[2], old[2]) > max(box[0], old[0])
                   and min(box[3], old[3]) > max(box[1], old[1]) for old, _, _ in regions):
                continue
            reading = '' if signature else _read_handwritten_line(image.crop(box))
            regions.append((box, match['label'], reading))
    for box, crop in _blue_ink_lines(image):
        # Only skip lines already covered by a proposed field.
        if any(old[0] <= box[0] + 2 and old[1] <= box[1] + 2
               and old[2] >= box[2] - 2 and old[3] >= box[3] - 2 for old, _, _ in regions):
            continue
        x0, y0, x1, y1 = box
        ink = crop.point(lambda value: 255 - value)
        remaining = ImageDraw.Draw(ink)
        total = ink.histogram()[255]
        for a, b, c, d in printed:
            if a < x1 and x0 < c and b < y1 and y0 < d:
                remaining.rectangle((a - x0 - 2, b - y0 - 2, c - x0 + 2, d - y0 + 2), fill=0)
        if ink.histogram()[255] < max(25, total * .2):
            continue
        regions.append((box, 'tinta azul', _read_handwritten_line(crop)))
    # A gray handwritten name often follows the printed closing "Atentamente".
    # Search only that small area; pale marks elsewhere on a scan are too ambiguous.
    seen_closings = set()
    for match in re.finditer(r'(?i)\batentamente\b', text):
        anchors = [box for a, b, box, confidence in words
                   if a < match.end() and b > match.start() and confidence >= 60]
        if not anchors:
            continue
        x0 = min(box[0] for box in anchors)
        y0 = min(box[1] for box in anchors)
        x1 = max(box[2] for box in anchors)
        y1 = max(box[3] for box in anchors)
        anchor_key = (x0, y0, x1, y1)
        if anchor_key in seen_closings:
            continue
        seen_closings.add(anchor_key)
        line_height = max(12, y1 - y0)
        probe = (max(0, x0 - line_height), min(height, y1 + 2),
                 min(width, x0 + max(x1 - x0 + 10 * line_height, width // 2)),
                 min(height, y1 + 5 * line_height))
        if probe[2] <= probe[0] or probe[3] <= probe[1]:
            continue
        crop = image.crop(probe).convert('L')
        pale = crop.point(lambda value: 255 if 70 <= value <= 225 else 0)
        mask_draw = ImageDraw.Draw(pale)
        for _, _, (a, b, c, d), confidence in words:
            if confidence >= 72:
                mask_draw.rectangle((a - probe[0] - 5, b - probe[1] - 5,
                                     c - probe[0] + 5, d - probe[1] + 5), fill=0)
        bounds = pale.getbbox()
        if not bounds or pale.histogram()[255] < 100 or bounds[2] - bounds[0] < 2 * line_height:
            continue
        box = (max(0, probe[0] + bounds[0] - 6), max(0, probe[1] + bounds[1] - 6),
               min(width, probe[0] + bounds[2] + 6), min(height, probe[1] + bounds[3] + 6))
        if any(min(box[2], old[2]) > max(box[0], old[0])
               and min(box[3], old[3]) > max(box[1], old[1]) for old, _, _ in regions):
            continue
        regions.append((box, 'nombre manuscrito tras Atentamente', ''))
    return regions


def identity_card_regions(image, text, words, image_boxes):
    """Identify Colombian ID images from combinations of official field labels."""
    from math import ceil, floor

    markers = ('REPUBLICA', 'COLOMBIA', 'CEDULA', 'CIUDADANIA', 'NUIP', 'APELLIDOS',
               'NOMBRES', 'NACIMIENTO', 'ESTATURA', 'EXPEDICION')
    labels = []
    for start, stop, box, _ in words:
        word = normalized_word(text[start:stop])
        if len(word) < 4:
            continue
        matched = next((label for label in markers
                        if label == word or (len(label) >= 6 and abs(len(label) - len(word)) <= 1
                                             and edit_distance(label, word) == 1)), None)
        if matched:
            labels.append((matched, (box[0]+box[2])/2, (box[1]+box[3])/2))
    regions = []
    # Prefer a separate embedded picture to a page-sized scan containing it.
    for x0, y0, x1, y1 in sorted(image_boxes, key=lambda box: (box[2]-box[0])*(box[3]-box[1])):
        if x1 - x0 < 100 or y1 - y0 < 80:
            continue
        nearby = {label for label, x, y in labels if x0 <= x <= x1 and y0 <= y <= y1
                  and not any(a <= x <= c and b <= y <= d for a, b, c, d in regions)}
        heading = {'REPUBLICA', 'COLOMBIA'}.issubset(nearby)
        card_type = {'CEDULA', 'CIUDADANIA'}.issubset(nearby)
        fields = nearby & {'NUIP', 'APELLIDOS', 'NOMBRES', 'NACIMIENTO', 'ESTATURA', 'EXPEDICION'}
        if (heading and card_type) or (card_type and len(fields) >= 3) or (
                len(fields & {'NACIMIENTO', 'ESTATURA', 'EXPEDICION'}) == 3):
            regions.append((max(0, floor(x0)-3), max(0, floor(y0)-3),
                            min(image.width, ceil(x1)+3), min(image.height, ceil(y1)+3)))
    return regions


def redact(image, replacer, *, native=('', ()), image_boxes=None):
    from math import ceil, floor
    from PIL import ImageDraw, ImageOps
    from etiquetas import image_label
    check_cancelled()
    image = ImageOps.exif_transpose(image).convert('RGB')
    cache_key = (image.size, hashlib.sha256(image.tobytes()).hexdigest())
    if cache_key not in replacer.ocr_cache:
        replacer.progress('Reconociendo texto (OCR): ' + replacer.location)
        replacer.ocr_cache[cache_key] = recognize(image)
    text, words = replacer.ocr_cache[cache_key]
    native_text, native_words = native
    ocr_word_count = len(words)
    if native_words:
        offset = len(native_text) + 2
        text = native_text + '\n\n' + text
        words = [*native_words, *((a + offset, b + offset, box, confidence)
                                  for a, b, box, confidence in words)]
    had_text = bool(words)
    cards = []
    if replacer.automatic:
        cards = identity_card_regions(image, text, words, image_boxes if image_boxes is not None
                                      else [(0, 0, image.width, image.height)])
    if cards:
        characters = list(text)
        outside = []
        for start, stop, box, confidence in words:
            x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
            if any(a <= x <= c and b <= y <= d for a, b, c, d in cards):
                characters[start:stop] = ' ' * (stop-start)
            else:
                outside.append((start, stop, box, confidence))
        text, words = ''.join(characters), outside
    if replacer.reviewing:
        preview = io.BytesIO()
        image.save(preview, format='PNG')
        replacer.evidence[replacer.location] = {'image': preview.getvalue(), 'words': words, 'text': text,
                                                'scanned': True, 'identity_cards': cards,
                                                'native_word_count': len(native_words),
                                                'ocr_word_count': ocr_word_count}
    drawing = ImageDraw.Draw(image)
    replacer.ocr_words, replacer.ocr_seen = words, []
    try:
        matches = list(replacer.spans(text))
    finally:
        replacer.ocr_words = None
    for start, stop, token in matches:
        check_cancelled()
        selected_words = [word for word in words if start < word[1] and stop > word[0]]
        if not selected_words:
            raise OCRError('No se pudo ubicar un dato seleccionado en el escaneo. No se generó una copia anonimizada.')
        box = (min(word[2][0] for word in selected_words), min(word[2][1] for word in selected_words),
               max(word[2][2] for word in selected_words), max(word[2][3] for word in selected_words))
        # OCR bounds can omit antialiased edges and accents from the scan.
        margin = max(2, max(int((word[2][3] - word[2][1]) / 10) for word in selected_words))
        x0, y0, x1, y1 = box
        box = (max(0, floor(x0) - margin), max(0, floor(y0) - margin),
               min(image.width, ceil(x1) + margin), min(image.height, ceil(y1) + margin))
        image_label(image, box, token)
        replacer.visual_codes.append((replacer.location, box, token))
        replacer.count += 1
    regions = replacer.regions.get(replacer.location, [])
    if replacer.approved is None and not (replacer.reviewing or replacer.learning):
        regions = [*regions, *({'box': box, 'caption': 'Cédula oculta'} for box in cards)]
    for region in regions:
        box = region['box'] if isinstance(region, dict) else region
        x0, y0, x1, y1 = box
        clipped = (max(0, min(image.width, x0)), max(0, min(image.height, y0)),
                   max(0, min(image.width, x1)), max(0, min(image.height, y1)))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            token = region.get('token') if isinstance(region, dict) else None
            if token:
                image_label(image, clipped, token)
                replacer.visual_codes.append((replacer.location, clipped, token))
            elif isinstance(region, dict) and region.get('caption'):
                image_label(image, clipped, region['caption'])
            else:
                drawing.rectangle((clipped[0], clipped[1], clipped[2] - 1, clipped[3] - 1), fill='#fff2a8')
        else:
            raise OCRError('No se pudo cubrir una zona seleccionada en el escaneo.')
        replacer.count += 1
    replacer.ocr_pages += 1
    if not had_text:
        message = 'OCR ejecutado sin texto reconocido en una imagen/página. Revise su legibilidad y contenido visual.'
    elif any(confidence < 60 for _, _, _, confidence in words):
        message = 'OCR con palabras de baja confianza: revise los datos y las áreas que no se hayan reconocido bien.'
    else:
        message = ('OCR local aplicado. Revise cada página: nombre, cédula, radicado, TD, NUI, '
                   'patio y huella pueden no ser reconocidos en manuscritos. Marque manualmente '
                   'cualquier dato o trazo restante.')
    if message not in replacer.warnings:
        replacer.warnings.append(message)
    return image
