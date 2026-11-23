"""Recorrido local: escaneo revisado, fallo nuevo y restauración de campos."""
import io
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from anonimizador import Replacer, analyze, apply_review, read_recovery
from entidades import known_entity_spans
from ocr_local import redact
from restauracion_campos import restore_judgment


class ScannedJudgmentFlowTests(unittest.TestCase):
    def test_colombian_id_number_labels_and_repeated_values_are_removed_from_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'identificaciones.pdf'
            ids = ('12.345.678', '87654321', '1234567890')
            with pymupdf.open() as doc:
                page = doc.new_page()
                lines = ['C.C. No. ' + ids[0], 'Cédula de ciudadanía No. ' + ids[1],
                         'T.I. Nro. ' + ids[2], 'Referencia: 12345678',
                         'Documento emitido el 22/11/2026']
                for index, line in enumerate(lines):
                    page.insert_text((45, 45 + index * 25), line)
                doc.save(source)
            plan = analyze(source)
            result = apply_review(plan, plan.findings, root / 'anonimizado')
            with pymupdf.open(result['output']) as doc:
                exported = doc[0].get_text()
            for value in (*ids, '12345678'):
                self.assertNotIn(value, exported)
            self.assertIn('22/11/2026', exported)

    def test_identity_card_is_covered_as_one_image_without_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'anexo.pdf'
            picture = io.BytesIO()
            Image.new('RGB', (180, 110), '#3b9ed2').save(picture, format='PNG')
            with pymupdf.open() as doc:
                page = doc.new_page(width=300, height=220)
                page.insert_image(pymupdf.Rect(45, 55, 225, 165), stream=picture.getvalue())
                page.draw_rect(pymupdf.Rect(255, 50, 270, 65), fill=(0, 0.5, 0))
                doc.save(source)
            labels = 'República Colombia Cédula Ciudadanía\nNombre: Maria Perez'
            words = [(match.start(), match.end(), (250 + index * 20, 310, 270 + index * 20, 330), 90)
                     for index, match in enumerate(re.finditer(r'\S+', labels))]
            with patch('ocr_local.recognize', return_value=(labels, words)):
                plan = analyze(source)
                self.assertEqual(len(plan.findings), 1)
                card = next(iter(plan.findings.values()))
                self.assertEqual(card['caption'], 'Cédula oculta')
                self.assertIsNone(card.get('token'))
                result = apply_review(plan, plan.findings, root / 'anonimizado')
            self.assertFalse(read_recovery(result['recovery'])[0]['fields'])
            with pymupdf.open(result['output']) as doc:
                page = doc[0]
                self.assertFalse(page.get_text().strip())
                xref = page.get_images()[0][0]
                with Image.open(io.BytesIO(doc.extract_image(xref)['image'])) as exported:
                    colors = {color for _, color in exported.getcolors(maxcolors=exported.width * exported.height)}
                    self.assertNotIn((59, 158, 210), colors)
                    self.assertTrue(any(g in (127, 128) and r == b == 0 for r, g, b in colors))

    def test_unread_handwriting_is_covered_in_scanned_zip_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'expediente.zip'
            image = Image.new('RGB', (600, 280), 'white')
            pen = ImageDraw.Draw(image)
            pen.line([(96, 62), (110, 38), (126, 66), (150, 48), (175, 60)],
                     fill='#4a79bd', width=4)
            pen.line([(71, 165), (90, 141), (102, 160), (145, 146)],
                     fill='#383838', width=4)
            pen.line([(330, 112), (345, 94), (360, 118), (382, 99), (401, 109)],
                     fill='#4a79bd', width=4)
            pen.rectangle((480, 215, 510, 245), fill='#000000')
            payload = io.BytesIO()
            image.save(payload, format='PNG')
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('anexo.png', payload.getvalue())

            labels = ('Nombre:\nFirma:', [(0, 7, (30, 40, 86, 61), 95),
                                         (8, 14, (30, 176, 82, 198), 95)])
            focused = ('Maria Perez', [(0, 11, (1, 1, 10, 10), 92)])
            with patch('ocr_local.recognize', return_value=labels), \
                    patch('ocr_local._recognize_pass', return_value=focused) as retry:
                plan = analyze(source)
                zones = [item for item in plan.findings.values() if item['kind'] == 'ZONA']
                self.assertEqual(len(zones), 3)
                self.assertIn('Maria Perez', zones[0]['value'])
                self.assertIn('Firma', zones[1]['value'])
                self.assertIn('Tinta azul', zones[2]['value'])
                self.assertIn('Maria Perez', zones[2]['value'])
                self.assertEqual([call.args[1] for call in retry.call_args_list], [7, 13, 7, 13])
                result = apply_review(plan, plan.findings, root / 'anonimizado')

            with zipfile.ZipFile(result['output']) as archive:
                with Image.open(io.BytesIO(archive.read('archivo_0001.png'))) as exported:
                    pixels = {color for _, color in exported.getcolors(maxcolors=600 * 280)}
                    self.assertNotIn((74, 121, 189), pixels)
                    self.assertNotIn((56, 56, 56), pixels)
                    self.assertEqual(exported.getpixel((495, 230)), (0, 0, 0))

    def test_zip_covers_unlabelled_full_name_in_another_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'expediente.zip'
            name = 'Maria de la Cruz'
            with zipfile.ZipFile(source, 'w') as archive:
                for filename, lines in [('tutela.pdf', [f'Nombre: {name}']),
                                        ('anexo.pdf', ['Maria de', 'la Cruz', 'Texto publico'])]:
                    with pymupdf.open() as doc:
                        page = doc.new_page()
                        for index, line in enumerate(lines):
                            page.insert_text((40, 40 + 22 * index), line)
                        archive.writestr(filename, doc.tobytes())
            plan = analyze(source)
            annex = [item for item in plan.findings.values() if 'anexo.pdf' in item['location']]
            self.assertEqual([' '.join(item['value'].split()) for item in annex], [name])
            result = apply_review(plan, plan.findings, root / 'anonimizado')
            with zipfile.ZipFile(result['output']) as archive:
                with pymupdf.open(stream=archive.read('archivo_0002.pdf'), filetype='pdf') as doc:
                    text = doc[0].get_text()
                    self.assertIn(annex[0]['token'], text)
                    self.assertIn('Texto publico', text)
                    self.assertNotIn('Maria', text)
                    self.assertNotIn('Cruz', text)

    def test_known_name_spans_all_words_across_lines(self):
        text = 'Maria Elena\nPerez'
        self.assertEqual(list(known_entity_spans(text, {'PERSONA': ['Maria Elena Perez']})),
                         [(0, len(text), 'PERSONA', text)])

    def test_mixed_pdf_covers_full_name_when_ocr_only_reads_part(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'mixto.pdf'
            name = 'Maria Elena Perez'
            with pymupdf.open() as doc:
                page = doc.new_page(width=500, height=170)
                page.insert_text((40, 45), 'Nombre: ' + name, fontsize=17)
                page.insert_image(pymupdf.Rect(40, 80, 60, 100),
                                  pixmap=pymupdf.Pixmap(pymupdf.csRGB, 1, 1, b'\xff\xff\xff', False))
                boxes = page.search_for(name)
                partial = page.search_for('Maria Elena')[0] * (300 / 72)
                doc.save(source)
            ocr = ('Nombre: Maria Elena', [(8, 19, tuple(partial), 90)])
            with patch('ocr_local.recognize', return_value=ocr):
                plan = analyze(source)
                self.assertEqual([item['value'] for item in plan.findings.values()], [name])
                result = apply_review(plan, plan.findings, root / 'anonimizado')
            with pymupdf.open(result['output']) as doc:
                page = doc[0]
                self.assertEqual(page.get_text().strip(), next(iter(plan.findings.values()))['token'])
                last_name_edge = page.get_pixmap(clip=pymupdf.Rect(boxes[0].x1 - 1, boxes[0].y0,
                                                                 boxes[0].x1, boxes[0].y1))
                self.assertEqual(last_name_edge.pixel(0, last_name_edge.height // 2), (255, 242, 168))

    def test_cropped_pdf_removes_every_word_of_the_approved_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'recortado.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(width=500, height=300)
                page.insert_text((105, 90), 'Maria Perez')
                page.insert_text((105, 130), 'Texto publico')
                page.set_cropbox(pymupdf.Rect(50, 50, 450, 250))
                page.set_rotation(90)
                doc.save(source)

            plan = analyze(source, ['Maria Perez'], automatic=False)
            self.assertEqual(len(plan.findings), 1)
            result = apply_review(plan, plan.findings, root / 'anonimizado')
            with pymupdf.open(result['output']) as doc:
                page = doc[0]
                extracted = page.get_text()
                self.assertIn('Texto publico', extracted)
                self.assertIn(next(iter(plan.findings.values()))['token'], extracted)
                self.assertNotIn('Maria', extracted)
                self.assertNotIn('Perez', extracted)
                pixmap = page.get_pixmap()
                self.assertIn((255, 242, 168), set(zip(*[iter(pixmap.samples)] * 3)))

    def test_ocr_cover_includes_ink_just_outside_the_detected_box(self):
        image = Image.new('RGB', (160, 50), 'white')
        image.putpixel((19, 9), (0, 0, 0))
        image.putpixel((117, 30), (0, 0, 0))
        image.putpixel((140, 30), (0, 0, 0))
        with patch('ocr_local.recognize', return_value=('Identificador', [(0, 13, (20, 10, 117, 30), 100)])):
            result = redact(image, Replacer(['Identificador'], automatic=False))
        self.assertEqual(result.getpixel((19, 9)), (255, 242, 168))
        self.assertEqual(result.getpixel((117, 30)), (255, 242, 168))
        self.assertEqual(result.getpixel((140, 30)), (0, 0, 0))

    def test_scanned_pdf_codes_survive_in_new_judgment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'escaneo.pdf'
            picture = io.BytesIO()
            image = Image.new('RGB', (1200, 500), 'white')
            drawing = ImageDraw.Draw(image)
            font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 40)
            drawing.text((60, 80), 'INFORME PUBLICO', fill='black', font=font)
            drawing.text((60, 170), 'Nombre: Maria Perez', fill='black', font=font)
            drawing.text((60, 300), 'Este contenido debe permanecer visible.', fill='black', font=font)
            image.save(picture, format='PNG')
            with pymupdf.open() as doc:
                page = doc.new_page(width=900, height=450)
                page.insert_image(page.rect, stream=picture.getvalue())
                doc.save(source)

            plan = analyze(source, ['Maria Perez'])
            selected = {key for key, finding in plan.findings.items()
                        if finding['kind'] == 'DATO' and finding['value'] == 'Maria Perez'}
            self.assertTrue(selected)
            result = apply_review(plan, selected, root / 'anonimizado')
            fields = read_recovery(result['recovery'])[0]['fields']
            self.assertEqual(len(fields), 1)
            code = next(iter(fields))
            self.assertEqual(fields[code], 'Maria Perez')
            with pymupdf.open(result['output']) as doc:
                self.assertIn(code, doc[0].get_text())
                self.assertNotIn('Maria Perez', doc[0].get_text())

            # Simula el texto nuevo que produciría el sustanciador con el código completo.
            judgment = root / 'fallo.docx'
            with zipfile.ZipFile(judgment, 'w') as archive:
                archive.writestr('word/document.xml',
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f'<w:body><w:p><w:r><w:t>AMPARAR a {code}. Decisión nueva.</w:t></w:r></w:p></w:body></w:document>')
            restored = restore_judgment(judgment, result['recovery'], root / 'restaurado')
            with zipfile.ZipFile(restored['output']) as archive:
                text = archive.read('word/document.xml').decode('utf-8')
            self.assertIn('AMPARAR a Maria Perez. Decisión nueva.', text)
            self.assertNotIn(code, text)
            self.assertEqual(restored['fields'], 1)
            with zipfile.ZipFile(judgment) as archive:
                self.assertIn(code, archive.read('word/document.xml').decode('utf-8'))
