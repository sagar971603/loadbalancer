import unittest

from eportal_login_enhanced import _blocking_itr_errors


class ItrValidationCategoryTests(unittest.TestCase):
    def test_only_categories_b_and_d_are_ignored(self):
        errors = [
            {"errCtg": "B", "desc": "advisory"},
            {"category": "d", "desc": "informational"},
            {"cat": "A", "desc": "blocking"},
            "unknown error",
        ]

        self.assertEqual(_blocking_itr_errors(errors), errors[2:])


if __name__ == "__main__":
    unittest.main()
