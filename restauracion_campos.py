"""Carga datos originales de una recuperación en un fallo nuevo, localmente."""
import hashlib
import io
from pathlib import Path
import re
import secrets
import zipfile
import xml.etree.ElementTree as ET

from anonimizador import (AnonymizationError, MAX_BYTES, Replacer,
                          ReviewPlan, _decode, _replace_xml, _write_new, read_recovery)


class FieldReplacer(Replacer):
    def __init__(self, fields, **kwargs):
        super().__init__(automatic=False, **kwargs)
        self.restore_fields = fields
        self.pattern = re.compile(r'(?<!\w)(?:' + '|'.join(
            re.escape(token) for token in sorted(fields, key=len, reverse=True)) + r')(?!\w)')

    def spans(self, text):
        scope = (self.location, hashlib.sha256(text.encode()).hexdigest())
        encounter = self.encounters.get(scope, 0)
        self.encounters[scope] = encounter + 1
        if self.reviewing:
            self.evidence.setdefault(self.location, {'text': '', 'image': None, 'words': []})
            if not self.evidence[self.location].get('image'):
                self.evidence[self.location]['text'] += text + '\n'
        for match in self.pattern.finditer(text):
            token = match.group()
            identity = hashlib.sha256(repr((scope, encounter, match.start(), token)).encode()).hexdigest()
            value = self.restore_fields[token]
            if self.reviewing:
                self.findings[identity] = {'id': identity, 'kind': 'RESTAURAR',
                    'value': token, 'token': value, 'location': self.location,
                    'context': text[max(0, match.start()-85):match.end()+85],
                    'start': match.start(), 'stop': match.end()}
            if self.approved is None or identity in self.approved:
                yield match.start(), match.end(), value


def _transform(data, suffix, replacer):
    if suffix == '.pdf':
        return _restore_pdf(data, replacer)
    if suffix == '.docx':
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            if 'word/document.xml' not in source.namelist():
                raise AnonymizationError('El fallo no es un documento Word DOCX válido.')
            if sum(info.file_size for info in source.infolist()) > MAX_BYTES * 3:
                raise AnonymizationError('El contenido descomprimido supera 300 MB.')
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    payload = source.read(info)
                    if re.fullmatch(r'word/(?:document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml', info.filename):
                        with replacer.at(info.filename):
                            for _, namespace in ET.iterparse(io.BytesIO(payload), events=('start-ns',)):
                                prefix, uri = namespace
                                if not re.fullmatch(r'ns\d+', prefix):
                                    ET.register_namespace(prefix, uri)
                            root = ET.fromstring(payload)
                            _replace_xml(root, replacer)
                            payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                    target.writestr(info, payload)
        return output.getvalue()
    if suffix in {'.txt', '.md', '.log', '.rst'}:
        text, encoding = _decode(data)
        return replacer.replace(text).encode(encoding)
    raise AnonymizationError('Para cargar datos al fallo use Word (.docx), texto (.txt) o PDF con códigos como texto seleccionable. Los bloques negros no son campos editables.')


def _restore_pdf(data, replacer):
    import pymupdf
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass:
            raise AnonymizationError('La providencia PDF está protegida. Exporte una copia PDF sin contraseña y vuelva a intentarlo.')
        for page in doc:
            with replacer.at(f'Página {page.number+1}'):
                # Character boxes keep punctuation and neighbouring text outside
                # the selected code intact, even when both share a PDF word.
                parts, words, offset = [], [], 0
                for block in page.get_text('rawdict', sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
                    for line in block.get('lines', []):
                        if parts:
                            parts.append('\n')
                            offset += 1
                        for span in line.get('spans', []):
                            for character in span.get('chars', []):
                                value = character['c']
                                parts.append(value)
                                words.append((offset, offset+len(value), character['bbox']))
                                offset += len(value)
                text = ''.join(parts)
                if replacer.reviewing:
                    pix = page.get_pixmap(dpi=100, alpha=False)
                    boxes = []
                    for a, b, box in words:
                        rect = pymupdf.Rect(box) * page.rotation_matrix
                        boxes.append((a, b, tuple(v * 100 / 72 for v in rect), 100))
                    replacer.evidence[replacer.location] = {'text': text, 'image': pix.tobytes('png'), 'words': boxes}
                replacements = []
                for start, stop, value in replacer.spans(text):
                    boxes = [pymupdf.Rect(box) for a, b, box in words if start < b and stop > a]
                    if not boxes:
                        raise AnonymizationError('No se pudo ubicar un código en el fallo PDF.')
                    rect = pymupdf.Rect(boxes[0])
                    for box in boxes[1:]:
                        rect |= box
                    if '\n' in value or '\r' in value:
                        raise AnonymizationError('Un dato ocupa varios renglones. Ajuste el PDF de la providencia y vuelva a intentarlo.')
                    font = pymupdf.Font('helv')
                    if any(ord(character) > 255 for character in value):
                        raise AnonymizationError('Un dato contiene caracteres que no se pueden insertar en este PDF. Ajuste el PDF de la providencia y vuelva a intentarlo.')
                    size = min(rect.height / 1.5, rect.width / max(font.text_length(value, fontsize=1), 0.1))
                    if size < 6:
                        raise AnonymizationError('Un dato original no cabe de forma legible. Amplíe el espacio del código en el PDF y vuelva a intentarlo.')
                    replacements.append((rect, value, size))
                if replacer.reviewing:
                    continue
                if replacements and any(annot.type[0] == 12 for annot in page.annots() or []):
                    raise AnonymizationError('La providencia PDF tiene tachaduras pendientes. Exporte un PDF sin anotaciones de redacción pendientes.')
                for rect, value, size in replacements:
                    page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
                if replacements:
                    page.apply_redactions(images=0, graphics=0)
                    for rect, value, size in replacements:
                        page.insert_text((rect.x0, rect.y0 + size), value, fontsize=size, fontname='helv')
                        replacer.count += 1
        return doc.tobytes(garbage=4, deflate=True)


def analyze_field_restoration(path, recovery, progress=None):
    body, _ = read_recovery(recovery)
    fields = body.get('fields', {})
    if not fields:
        raise AnonymizationError('Esta recuperación no contiene códigos y campos guardados. Si es una copia antigua, restaure el original y vuelva a anonimizarlo con esta versión para crear los campos.')
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise AnonymizationError('Seleccione un fallo existente de hasta 100 MB.')
    original = path.read_bytes()
    replacer = FieldReplacer(fields, reviewing=True, progress=progress)
    _transform(original, path.suffix.lower(), replacer)
    if not replacer.findings:
        raise AnonymizationError('Los campos editables del fallo no corresponden a esta recuperación. Seleccione la recuperación de los insumos utilizados por el sustanciador. Los escaneos y bloques negros no se sustituyen como texto.')
    unknown = set(re.findall(r'(?<!\w)(?:ANON_[A-Z_]+_\d{4,}|A[A-F0-9]{7})(?!\w)',
                             '\n'.join(item['text'] for item in replacer.evidence.values()))) - fields.keys()
    warnings = [f"Recuperación del caso: {body['name']}.",
                'Solo se sustituyen los códigos autorizados. El texto nuevo del fallo se conserva.']
    if unknown:
        warnings.append(f'{len(unknown)} códigos no están en esta recuperación y conservarán su texto: ' + ', '.join(sorted(unknown)))
    return ReviewPlan(path, original, (), False, replacer.findings, replacer.evidence,
                      warnings,
                      0, {}, path.suffix, purpose='restore_fields', restoration_fields=dict(fields))


def apply_field_review(plan, approved, output_dir, progress=None):
    approved = set(approved)
    if not approved or not approved.issubset(plan.findings):
        raise AnonymizationError('Seleccione campos del fallo que desea restaurar.')
    if plan.path.read_bytes() != plan.original:
        raise AnonymizationError('El fallo cambió después del análisis. Vuelva a analizarlo.')
    replacer = FieldReplacer(plan.restoration_fields, approved=approved, progress=progress)
    restored = _transform(plan.original, plan.path.suffix.lower(), replacer)
    if replacer.count != len(approved):
        raise AnonymizationError('Los campos ya no corresponden a la revisión. Vuelva a analizar el fallo.')
    if plan.path.suffix.lower() == '.pdf':
        import pymupdf
        with pymupdf.open(stream=restored, filetype='pdf') as document:
            remaining = set(re.findall(r'(?<!\w)(?:ANON_[A-Z_]+_\d{4,}|A[A-F0-9]{7})(?!\w)',
                                       '\n'.join(page.get_text() for page in document)))
        if remaining:
            raise AnonymizationError('El PDF conserva códigos sin restaurar: ' + ', '.join(sorted(remaining)))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f'documento_restaurado_{secrets.token_hex(4)}{plan.path.suffix}'
    _write_new(target, restored)
    return {'output': str(target.resolve()), 'replacements': replacer.count}


def restore_judgment(path, recovery, output_dir, progress=None):
    """Restore every existing code in the judgment, without manual selection."""
    if Path(path).suffix.lower() not in {'.pdf', '.docx', '.txt', '.md', '.log', '.rst'}:
        raise AnonymizationError('Seleccione una providencia PDF, Word o de texto con códigos intactos.')
    plan = analyze_field_restoration(path, recovery, progress)
    codes = set(re.findall(r'(?<!\w)(?:ANON_[A-Z_]+_\d{4,}|A[A-F0-9]{7})(?!\w)',
                           '\n'.join(item['text'] for item in plan.evidence.values())))
    unknown = codes - plan.restoration_fields.keys()
    if unknown:
        raise AnonymizationError('No se generó el fallo: hay campos que no pertenecen a esta recuperación: '
                                 + ', '.join(sorted(unknown)) + '. Seleccione la recuperación del caso correspondiente.')
    if progress:
        progress(f'Restaurando automáticamente {len(plan.findings)} apariciones de campos en el fallo…')
    result = apply_field_review(plan, set(plan.findings), output_dir, progress)
    result['fields'] = len({finding['value'] for finding in plan.findings.values()})
    return result
