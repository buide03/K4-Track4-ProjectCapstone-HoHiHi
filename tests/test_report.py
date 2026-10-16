from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from collision_warning.report import write_ttc_chart


class ChartTests(unittest.TestCase):
    def test_missing_estimates_and_id_changes_break_the_curve(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ttc.svg"
            series = [(0, (1, 8)), (1, (1, 5)), (2, None),
                      (3, (1, 3)), (4, (1, 2)), (5, (2, 1.8)), (6, (2, 1.2))]
            write_ttc_chart(path, series, 3, 1.5)
            root = ET.parse(path).getroot()
            lines = root.findall(".//{http://www.w3.org/2000/svg}polyline")
            self.assertEqual(len(lines), 3)
            self.assertTrue(all(len(line.attrib["points"].split()) == 2 for line in lines))
            for line in lines:
                for point in line.attrib["points"].split():
                    _, y = map(float, point.split(","))
                    self.assertGreaterEqual(y, 60)
                    self.assertLessEqual(y, 360)

    def test_no_detections_still_produces_valid_chart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ttc.svg"
            write_ttc_chart(path, [(0, None)], 3, 1.5)
            self.assertEqual(len(ET.parse(path).getroot().findall(
                ".//{http://www.w3.org/2000/svg}polyline")), 0)


if __name__ == "__main__":
    unittest.main()
