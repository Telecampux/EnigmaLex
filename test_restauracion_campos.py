import io
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from anonimizador import (AnonymizationError, _recovery, analyze, apply_review,
                          anonymize, read_recovery, restore)
from restauracion_campos import analyze_field_restoration, restore_judgment


class FieldRestorationTests(unittest.TestCase):
    def test_codes_from_different_cases_do_not_restore_wrong_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / 'caso_uno.txt'
            second = root / 'caso_dos.txt'
            first.write_text('María Pérez', encoding='utf-8')
            second.write_text('Luisa López', encoding='utf-8')
            result1 = anonymize(first, root / 'salida1', ['María Pérez'], False)
            result2 = anonymize(second, root / 'salida2', ['Luisa López'], False)
            fields1 = read_recovery(result1['recovery'])[0]['fields']
            fields2 = read_recovery(result2['recovery'])[0]['fields']
            self.assertFalse(set(fields1) & set(fields2))
            fallo = root / 'fallo.txt'
            fallo.write_text('AMPARAR a ' + next(iter(fields1)), encoding='utf-8')
            with self.assertRaises(AnonymizationError):
                restore_judgment(fallo, result2['recovery'], root / 'incorrecto')
            self.assertFalse((root / 'incorrecto').exists())
            restored = restore_judgment(fallo, result1['recovery'], root / 'correcto')
            self.assertEqual(Path(restored['output']).read_text(encoding='utf-8'), 'AMPARAR a María Pérez')

    def test_reviewed_codes_are_the_same_codes_saved_in_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'demanda.txt'
            source.write_text('María Pérez', encoding='utf-8')
            plan = analyze(source, ['María Pérez'], False)
            code = next(iter(plan.findings.values()))['token']
            result = apply_review(plan, set(plan.findings), root / 'salida')
            self.assertEqual(Path(result['output']).read_text(encoding='utf-8'), code)
            self.assertEqual(read_recovery(result['recovery'])[0]['fields'][code], 'María Pérez')

    def test_fallo_requires_loaded_judgment_and_ignores_other_selected_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        import threading
        from app import App
        variable = lambda value: Mock(get=Mock(return_value=value))
        app = SimpleNamespace(busy=False, mode=variable('fallo'), restore_kind=variable('elaborado'), judgment=variable(''),
            files=['demanda_original.docx'], recovery=variable('caso.restauracion'),
            output=variable('salida'), manual=variable(''), auto=variable(True),
            run_button=Mock(), activity=Mock(), status=Mock(), work=Mock(),
            cancel_event=threading.Event(), cancel_button=Mock(), clear_progress=Mock(),
            folders={'anonimizados': Path('salida'), 'providencias': Path('providencias')})
        with patch('app.validar_carpetas'), patch('app.messagebox.showerror') as error, patch('app.threading.Thread') as thread:
            App.run(app)
            self.assertIn('archivo que desea restaurar', error.call_args.args[1])
            thread.assert_not_called()
            app.judgment = variable('fallo_sustanciador.docx')
            App.run(app)
            args = thread.call_args.kwargs['args']
            self.assertEqual(args[1], ['fallo_sustanciador.docx'])
            self.assertEqual(args[-1], 'caso.restauracion')

    def test_automatic_judgment_restores_all_existing_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            fields = {'ANON_PERSONA_0001': 'María Pérez', 'ANON_DOCUMENTO_0002': '12345678'}
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', fields))
            target = root / 'fallo.txt'
            original = 'AMPARAR a ANON_PERSONA_0001, CC ANON_DOCUMENTO_0002. Notificar a ANON_PERSONA_0001.'
            target.write_text(original, encoding='utf-8')
            result = restore_judgment(target, recovery, root / 'salida')
            self.assertEqual(result['fields'], 2)
            self.assertEqual(result['replacements'], 3)
            self.assertEqual(Path(result['output']).read_text(encoding='utf-8'),
                             'AMPARAR a María Pérez, CC 12345678. Notificar a María Pérez.')
            self.assertEqual(target.read_text(encoding='utf-8'), original)

    def test_automatic_restoration_refuses_unresolved_fields_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_PERSONA_0001': 'María Pérez'}))
            target = root / 'fallo.txt'
            target.write_text('ANON_PERSONA_0001 y ANON_DOCUMENTO_0002', encoding='utf-8')
            with self.assertRaisesRegex(AnonymizationError, 'No se generó el fallo'):
                restore_judgment(target, recovery, root / 'salida')
            self.assertFalse((root / 'salida').exists())

    def test_app_restores_automatically_without_opening_review(self):
        import queue
        from types import SimpleNamespace
        from app import App
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_PERSONA_0001': 'María Pérez'}))
            target = root / 'fallo.docx'
            with zipfile.ZipFile(target, 'w') as z:
                z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>AMPARAR a ANON_PER</w:t></w:r><w:r><w:t>SONA_0001.</w:t></w:r></w:p></w:body></w:document>')
            worker = SimpleNamespace(events=queue.Queue(), report_progress=lambda message: None)
            App.work(worker, 'fallo', [str(target)], str(root / 'salida'), [], True, str(recovery))
            events = []
            while not worker.events.empty():
                events.append(worker.events.get_nowait())
            self.assertNotIn('review', [kind for kind, message in events])
            self.assertTrue(any('documento restaurado localmente' in message for kind, message in events if kind == 'log'))
            self.assertEqual(events[-1][0], 'done')
            outputs = list((root / 'salida').glob('documento_restaurado_*.docx'))
            self.assertEqual(len(outputs), 1)
            with zipfile.ZipFile(outputs[0]) as restored:
                self.assertIn('AMPARAR a María Pérez.',
                              ''.join(ET.fromstring(restored.read('word/document.xml')).itertext()))
            self.assertEqual(len([event for event in events if event[0] == 'restored']), 1)

    def test_recovery_keeps_only_authorized_fields_with_original_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'demanda.txt'
            source.write_text('María Pérez; ana@example.com', encoding='utf-8')
            plan = analyze(source, ['María Pérez'])
            selected = {f['id'] for f in plan.findings.values() if f['kind'] == 'DATO'}
            result = apply_review(plan, selected, root / 'salida')
            body, original = read_recovery(result['recovery'])
            self.assertEqual(body['version'], 2)
            self.assertEqual(list(body['fields'].values()), ['María Pérez'])
            self.assertEqual(original, source.read_bytes())
            self.assertEqual(Path(restore(result['recovery'], root)).read_bytes(), original)

    def test_new_judgment_restores_only_selected_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'demanda.txt'
            source.write_text('María Pérez', encoding='utf-8')
            result = anonymize(source, root / 'salida', ['María Pérez'], False)
            body, _ = read_recovery(result['recovery'])
            token = next(iter(body['fields']))
            target = root / 'fallo.txt'
            original = f'RESUELVE: amparar a {token}. Notificar a {token}. Decisión nueva.'
            target.write_text(original, encoding='utf-8')
            files_before = set(root.rglob('*'))
            plan = analyze_field_restoration(target, result['recovery'])
            self.assertEqual(set(root.rglob('*')), files_before)
            self.assertEqual(len(plan.findings), 2)
            output = apply_review(plan, {next(iter(plan.findings))}, root / 'fallos')
            restored = Path(output['output']).read_text(encoding='utf-8')
            self.assertEqual(restored, f'RESUELVE: amparar a María Pérez. Notificar a {token}. Decisión nueva.')
            self.assertEqual(target.read_text(encoding='utf-8'), original)

    def test_docx_runs_headers_media_and_repeated_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_DATO_0001': 'María & Pérez'}))
            target = root / 'fallo.docx'
            ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            paragraph = '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>ANON_DA</w:t></w:r><w:r><w:t>TO_0001</w:t></w:r></w:p>'
            with zipfile.ZipFile(target, 'w') as z:
                z.writestr('word/document.xml', f'<w:document xmlns:w="{ns}"><w:body>{paragraph}{paragraph}<w:p><w:r><w:t>DECISIÓN NUEVA</w:t></w:r></w:p></w:body></w:document>')
                z.writestr('word/header1.xml', f'<w:hdr xmlns:w="{ns}">{paragraph}</w:hdr>')
                z.writestr('word/media/logo.png', b'unchanged media')
                z.writestr('word/styles.xml', b'unchanged styles')
            plan = analyze_field_restoration(target, recovery)
            self.assertEqual(len(plan.findings), 3)
            selected = {next(iter(plan.findings))}
            result = apply_review(plan, selected, root / 'salida')
            with zipfile.ZipFile(result['output']) as z:
                doc = ET.fromstring(z.read('word/document.xml'))
                texts = ''.join(doc.itertext())
                self.assertIn('María & Pérez', texts)
                self.assertIn('ANON_DATO_0001', texts)
                self.assertIn('DECISIÓN NUEVA', texts)
                self.assertEqual(len(doc.findall(f'.//{{{ns}}}b')), 2)
                self.assertEqual(z.read('word/media/logo.png'), b'unchanged media')
                self.assertEqual(z.read('word/styles.xml'), b'unchanged styles')
                self.assertIn('ANON_DATO_0001', ''.join(ET.fromstring(z.read('word/header1.xml')).itertext()))

    def test_old_recovery_restores_original_but_does_not_guess_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'antigua.restauracion'
            recovery.write_bytes(_recovery(b'original exacto', 'demanda.txt'))
            target = root / 'fallo.txt'
            target.write_text('ANON_DATO_0001')
            self.assertEqual(Path(restore(recovery, root)).read_bytes(), b'original exacto')
            with self.assertRaisesRegex(AnonymizationError, 'no contiene códigos'):
                analyze_field_restoration(target, recovery)

    def test_unknown_codes_and_longer_identifiers_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_DATO_0001': 'María Pérez'}))
            target = root / 'fallo.txt'
            target.write_text('ANON_DATO_00010 ANON_DATO_0001 ANON_OTRO_0002 XANON_DATO_0001', encoding='utf-8')
            plan = analyze_field_restoration(target, recovery)
            self.assertEqual(len(plan.findings), 1)
            self.assertTrue(any('2 códigos' in warning for warning in plan.warnings))
            result = apply_review(plan, set(plan.findings), root / 'salida')
            self.assertEqual(Path(result['output']).read_text(encoding='utf-8'),
                             'ANON_DATO_00010 María Pérez ANON_OTRO_0002 XANON_DATO_0001')

    def test_pdf_preserves_new_ruling_and_restores_selected_code(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_DATO_0001': 'Maria Perez'}))
            target = root / 'fallo.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 50), 'RESUELVE: AMPARAR LOS DERECHOS')
                page.insert_text((40, 80), '(ANON_DATO_0001).')
                page.insert_text((40, 110), 'ANON_DATO_0001')
                page.set_rotation(90)
                doc.save(target)
            original = target.read_bytes()
            plan = analyze_field_restoration(target, recovery)
            self.assertEqual(len(plan.findings), 2)
            result = apply_review(plan, set(plan.findings), root / 'salida')
            with pymupdf.open(result['output']) as doc:
                text = doc[0].get_text()
                self.assertEqual(text.count('ANON_DATO_0001'), 0)
                self.assertEqual(text.count('Maria Perez'), 2)
                self.assertIn('AMPARAR LOS DERECHOS', text)
                self.assertIn('(', text)
                self.assertIn(').', text)
                self.assertEqual(doc[0].rotation, 90)
            self.assertEqual(target.read_bytes(), original)

    def test_refuses_changes_wrong_selections_and_unsupported_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'caso.restauracion'
            recovery.write_bytes(_recovery(b'original', 'demanda.txt', {'ANON_DATO_0001': 'María Pérez'}))
            target = root / 'fallo.txt'
            target.write_text('ANON_DATO_0001')
            plan = analyze_field_restoration(target, recovery)
            for selection in (set(), {'unknown'}):
                with self.assertRaises(AnonymizationError):
                    apply_review(plan, selection, root / 'salida')
            target.write_text('Otro fallo')
            with self.assertRaisesRegex(AnonymizationError, 'cambió'):
                apply_review(plan, set(plan.findings), root / 'salida')
            self.assertFalse((root / 'salida').exists())
            pdf = root / 'fallo.bin'
            pdf.write_bytes(b'ANON_DATO_0001')
            with self.assertRaisesRegex(AnonymizationError, 'Word'):
                analyze_field_restoration(pdf, recovery)


if __name__ == '__main__':
    unittest.main()
