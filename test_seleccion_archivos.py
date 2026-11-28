import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import App


class SeleccionArchivosTests(unittest.TestCase):
    def test_nueva_seleccion_reemplaza_la_anterior(self):
        with tempfile.TemporaryDirectory() as folder:
            state = SimpleNamespace(busy=False, mode=SimpleNamespace(get=lambda: 'anonimizar'),
                                    folders={'originales': Path(folder)}, files=['primero.pdf'])
            state.clear = lambda: state.files.clear()
            state.add_original = lambda file: state.files.append(file)
            with patch('app.filedialog.askopenfilenames', return_value=('segundo.pdf',)):
                App.choose(state)
            self.assertEqual(['segundo.pdf'], state.files)

    def test_cancelar_selector_conserva_la_seleccion(self):
        with tempfile.TemporaryDirectory() as folder:
            state = SimpleNamespace(busy=False, mode=SimpleNamespace(get=lambda: 'anonimizar'),
                                    folders={'originales': Path(folder)}, files=['primero.pdf'])
            state.clear = lambda: state.files.clear()
            with patch('app.filedialog.askopenfilenames', return_value=()):
                App.choose(state)
            self.assertEqual(['primero.pdf'], state.files)


if __name__ == '__main__':
    unittest.main()
