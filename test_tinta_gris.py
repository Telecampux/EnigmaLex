import unittest

from PIL import Image, ImageDraw

from ocr_local import handwriting_regions


class TintaGrisTests(unittest.TestCase):
    def test_nombre_gris_debajo_de_atentamente(self):
        image = Image.new('RGB', (800, 220), 'white')
        draw = ImageDraw.Draw(image)
        for offset in range(0, 450, 35):
            draw.arc((100 + offset, 90, 145 + offset, 155), 0, 300, fill='#999999', width=4)
        words = [(0, 11, (75, 0, 395, 44), 90),
                 (12, 17, (300, 90, 550, 155), 55)]
        regions = handwriting_regions(image, 'Atentamente\nPerez', words)
        self.assertTrue(any(label == 'nombre manuscrito tras Atentamente' and box[2] > 550
                            for box, label, _ in regions))

    def test_tinta_gris_sin_cierre_no_se_propone(self):
        image = Image.new('RGB', (800, 220), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 90, 600, 150), fill='#999999')
        self.assertEqual([], handwriting_regions(image, '', []))


if __name__ == '__main__':
    unittest.main()
