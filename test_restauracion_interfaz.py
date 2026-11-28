from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anonimizador import _recovery
from app import App


class RestorationInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.app.mode.set('fallo')
        self.app.restore_kind.set('elaborado')
        self.app.update_restoration_kind()
        self.addCleanup(self.app.destroy)

    def test_restoration_shows_only_simple_controls(self):
        self.assertIn('los datos personales se procesan solo en el sistema local',
                      self.app.local_restore_notice.cget('text'))
        self.assertEqual(self.app.recovery_bar.winfo_manager(), '')
        self.assertEqual(self.app.output_grid.winfo_manager(), '')
        self.assertEqual(self.app.log.winfo_manager(), '')
        self.assertEqual(self.app.results_frame.winfo_manager(), '')
        self.assertTrue(self.app.open_result_button.instate(['disabled']))
        self.app.mode.set('anonimizar')
        self.assertEqual(self.app.results_frame.winfo_manager(), '')
        self.assertEqual(self.app.log.winfo_manager(), 'pack')

    def test_finished_restoration_enables_open_and_download(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'documento_restaurado.pdf'
            target.write_bytes(b'%PDF-original')
            self.app.events.put(('restored', str(target)))
            self.app.events.put(('done', 'Proceso terminado'))
            with patch('app.os.startfile') as opened:
                self.app.poll()
            self.assertEqual(self.app.selected_restored.get(), str(target))
            self.assertEqual(self.app.result_name.get(), target.name)
            self.assertTrue(self.app.open_result_button.instate(['!disabled']))
            self.assertTrue(self.app.download_result_button.instate(['!disabled']))
            self.assertIn('Revíselo antes', self.app.status.get())
            opened.assert_called_once_with(str(target.resolve()))
            destination = Path(directory) / 'copia.pdf'
            with patch('app.filedialog.asksaveasfilename', return_value=str(destination)):
                self.app.download_restored()
            self.assertEqual(destination.read_bytes(), target.read_bytes())

    def test_picker_starts_in_configured_folder_and_does_not_hide_extensions(self):
        self.app.folders['providencias'] = Path('C:/carpeta-elegida')
        with patch('app.filedialog.askopenfilename', return_value='') as dialog:
            self.app.load_downloaded_judgment()
        self.assertEqual(dialog.call_args.kwargs['initialdir'],
                         str(self.app.folders['providencias']))
        self.assertEqual(dialog.call_args.kwargs['filetypes'][-1], ('Todos los archivos', '*'))

    def test_selecting_anonymized_document_shows_both_paths_and_table(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fallo_anonimizado.txt'
            target.write_text('ANON_PERSONA_0001', encoding='utf-8')
            recovery = Path(str(target) + '.restauracion')
            recovery.write_bytes(_recovery(b'original', 'insumo.txt', {'ANON_PERSONA_0001': 'Maria Perez'}))
            with patch('app.filedialog.askopenfilename', return_value=str(target)):
                self.app.load_downloaded_judgment()
            self.assertEqual(self.app.judgment_name.get(), target.name)
            self.assertEqual(self.app.judgment.get(), str(target))
            self.assertEqual(self.app.recovery.get(), str(recovery))
            self.assertEqual(self.app.recovery_name.get(), recovery.name)
            self.assertIn(str(target), self.app.anonymized_source.get())
            rows = self.app.recovery_fields.get_children()
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(self.app.recovery_fields.item(rows[0], 'values')), ('ANON_PERSONA_0001', 'Maria Perez'))

    def test_downloaded_judgment_selection_does_not_start_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fallo_descargado.txt'
            target.write_text('AMPARAR a ANON_PERSONA_0001', encoding='utf-8')
            recovery = Path(str(target) + '.restauracion')
            recovery.write_bytes(_recovery(b'original', 'insumo.txt',
                                            {'ANON_PERSONA_0001': 'Maria Perez'}))
            with patch('app.filedialog.askopenfilename', return_value=str(target)) as dialog, \
                 patch.object(self.app, 'run') as run:
                self.app.load_downloaded_judgment()
            self.assertIn('documento anonimizado elaborado por el sustanciador', dialog.call_args.kwargs['title'])
            self.assertEqual(self.app.judgment.get(), str(target))
            self.assertEqual(self.app.recovery.get(), str(recovery))
            run.assert_not_called()
            self.assertEqual(self.app.run_button.cget('text'), 'Restaurar documento del sustanciador en local')

    def test_original_option_recovers_exact_bytes_without_using_generated_document(self):
        from types import SimpleNamespace
        import queue
        from app import App
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / 'entrada.png.restauracion'
            recovery.write_bytes(_recovery(b'\x89PNG\r\noriginal', 'entrada.png',
                                            {'A1234567': 'Maria Perez'}))
            self.app.restore_kind.set('original')
            self.app.update_restoration_kind()
            worker = SimpleNamespace(events=queue.Queue(), report_progress=lambda message: None)
            App.work(worker, 'original', [str(root / 'entrada.png')], str(root / 'salida'),
                     [], False, str(recovery))
            restored = [value for kind, value in list(worker.events.queue) if kind == 'restored']
            self.assertEqual(len(restored), 1)
            self.assertEqual(Path(restored[0]).read_bytes(), b'\x89PNG\r\noriginal')
            self.assertEqual(self.app.run_button.cget('text'), 'Restaurar copia exacta del original')

    def test_loading_invalid_table_does_not_leave_previous_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery = Path(directory) / 'caso.RESTAURACION'
            recovery.write_bytes(_recovery(b'original', 'insumo.txt', {'ANON_PERSONA_0001': 'Maria Perez'}))
            self.app.load_recovery(recovery)
            self.assertEqual(len(self.app.recovery_fields.get_children()), 1)
            with patch('app.messagebox.showerror') as error:
                self.app.load_recovery(Path(directory) / 'inexistente.restauracion')
            error.assert_called_once()
            self.assertEqual(self.app.recovery.get(), '')
            self.assertEqual(self.app.recovery_fields.get_children(), ())


if __name__ == '__main__':
    unittest.main()
