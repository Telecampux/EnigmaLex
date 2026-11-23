import io
import unittest
from types import SimpleNamespace

from PIL import Image

from anonimizador import _propose_manuscript_identity_blocks


class ManuscriptIdentityBlocksTests(unittest.TestCase):
    def test_different_page_layouts_get_content_based_zones(self):
        image = Image.new('RGB', (1000, 1400), 'white')
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        words = ['Accionanie', 'Persona', 'Radicado', '123456', 'NONBRE',
                 'Persona', 'CC', '987654', 'PATIO', '5']
        for shift in (0, 160):
            positions = [(90, 250), (270, 250), (90, 680), (270, 680),
                         (120, 950), (290, 950), (120, 1010), (290, 1010),
                         (120, 1120), (290, 1120)]
            text = '\n'.join(words) + '\nPowered by'
            spans = []
            offset = 0
            for word, (x, y) in zip(words, positions):
                spans.append((offset, offset + len(word), (x, y + shift, x + 150, y + shift + 30), 50))
                offset += len(word) + 1
            replacer = SimpleNamespace(evidence={'Documento / Página 1': {
                'scanned': True, 'image': buffer.getvalue(), 'text': text, 'words': spans}}, findings={})
            _propose_manuscript_identity_blocks(replacer)
            values = [finding['value'] for finding in replacer.findings.values()]
            self.assertIn('Nombre manuscrito', values)
            self.assertTrue(any('radicado' in value for value in values))
            self.assertIn('Huella digital', values)


if __name__ == '__main__':
    unittest.main()
