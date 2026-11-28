import io
import unittest
from types import SimpleNamespace

from PIL import Image, ImageDraw

from anonimizador import _propose_manuscript_identity_blocks


class ManuscriptIdentityBlocksTests(unittest.TestCase):
    def test_printed_labels_without_unread_ink_do_not_create_zones(self):
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
            self.assertEqual([], values)

    def test_unread_ink_next_to_label_creates_zone(self):
        image = Image.new('RGB', (1000, 1400), 'white')
        ImageDraw.Draw(image).line([(280, 260), (300, 270), (325, 258), (350, 270)], fill='black', width=3)
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        text = 'Nombre Persona\nPowered by'
        words = [(0, 6, (90, 250, 240, 280), 90),
                 (7, 14, (270, 250, 420, 280), 50)]
        replacer = SimpleNamespace(evidence={'Documento / Página 1': {
            'scanned': True, 'image': buffer.getvalue(), 'text': text, 'words': words}}, findings={})
        _propose_manuscript_identity_blocks(replacer)
        self.assertIn('Nombre manuscrito', [finding['value'] for finding in replacer.findings.values()])


if __name__ == '__main__':
    unittest.main()
