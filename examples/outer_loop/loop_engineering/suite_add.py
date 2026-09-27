import unittest

from add import add


class AddTests(unittest.TestCase):
    def test_numbers(self) -> None:
        self.assertEqual(add(1, 2), 3)

    def test_string_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            add("1", 2)


if __name__ == "__main__":
    unittest.main()
