import ast
import pathlib
import unittest


class PanLinkActionTests(unittest.TestCase):
    def test_api_uses_the_client_pan_link_signatures(self):
        tree = ast.parse(pathlib.Path(__file__).with_name("main.py").read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

        generate = next(
            call
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "to_thread"
            and call.args
            and isinstance(call.args[0], ast.Attribute)
            and call.args[0].attr == "generate_pan_link_otp"
        )
        self.assertIn("name", {keyword.arg for keyword in generate.keywords})

        verify = [
            call.args[0].attr
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "to_thread"
            and call.args
            and isinstance(call.args[0], ast.Attribute)
            and "pan_link_otp" in call.args[0].attr
        ]
        self.assertIn("vaildate_adhar_pan_link_otp", verify)
        self.assertNotIn("validate_pan_link_otp", verify)


if __name__ == "__main__":
    unittest.main()
