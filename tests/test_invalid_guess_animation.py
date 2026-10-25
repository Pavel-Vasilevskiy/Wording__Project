import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InvalidGuessAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (PROJECT_ROOT / "static" / "script.js").read_text(encoding="utf-8")
        cls.styles = (PROJECT_ROOT / "static" / "style.css").read_text(encoding="utf-8")
        cls.template = (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    def test_dictionary_error_restarts_shake_on_current_guess_row(self):
        self.assertIn('id="current-guess"', self.template)
        self.assertIn("data.error === 'Not in word list'", self.script)
        self.assertIn("currentGuessDiv.classList.remove('shake')", self.script)
        self.assertIn("currentGuessDiv.classList.add('shake')", self.script)
        self.assertIn("void currentGuessDiv.offsetWidth", self.script)
        self.assertIn("animationend", self.script)
        self.assertIn("e.animationName === 'guess-row-shake'", self.script)

    def test_shake_is_short_and_respects_reduced_motion(self):
        self.assertIn("animation: guess-row-shake 400ms ease-in-out", self.styles)
        self.assertIn("@keyframes guess-row-shake", self.styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)
        self.assertIn("animation: none", self.styles)

    def test_errors_keep_input_and_only_dictionary_error_shakes(self):
        error_branch = self.script.split("if (data.error) {", 1)[1].split("// Update game board", 1)[0]
        self.assertIn("if (data.error === 'Not in word list')", error_branch)
        self.assertNotIn("currentGuess = ''", error_branch)
        self.assertNotIn("guessInput.value = ''", error_branch)

        network_error_branch = self.script.split(".catch(error => {", 1)[1].split(".finally(() => {", 1)[0]
        self.assertNotIn("shake", network_error_branch)
        self.assertIn("An error occurred. Please try again.", network_error_branch)


if __name__ == "__main__":
    unittest.main()
