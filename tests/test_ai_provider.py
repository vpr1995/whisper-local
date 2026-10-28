# Tests for the AI provider (Apple Intelligence or Ollama) and its polish safety checks.
# The platform module and Ollama are mocked: no test starts the real helper or opens a socket.

import importlib.util
import unittest
import unittest.mock as mock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from whisper_key import ai_provider  # noqa: E402
from whisper_key.utils import OnDeviceAIError  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

# Pins Apple Intelligence's availability, whatever machine the tests run on.
def apple_is(available, reason=''):
    return mock.patch.object(ai_provider, 'apple_availability', return_value=(available, reason))


# A fake platform module whose generate() returns `text` (or raises `error`).
def fake_apple_module(text='', error=None):
    module = mock.MagicMock()
    module.availability.return_value = (True, '')
    if error:
        module.generate.side_effect = error
    else:
        module.generate.return_value = text
    return module


def config_with(provider='auto', polish=True):
    return {'ai': {'provider': provider, 'polish': polish},
            'ollama': {'endpoint': 'http://127.0.0.1:0', 'model': 'm', 'timeout': 1}}


# ── Provider resolution ──────────────────────────────────────────────────────

class ProviderResolutionTests(unittest.TestCase):
    def test_auto_uses_apple_when_available_else_ollama(self):
        with apple_is(True):
            self.assertEqual(ai_provider.active_provider(config_with('auto')), 'apple_intelligence')
        with apple_is(False, 'apple_intelligence_off'):
            self.assertEqual(ai_provider.active_provider(config_with('auto')), 'ollama')

    def test_explicit_apple_falls_back_to_ollama_when_unavailable(self):
        with apple_is(True):
            self.assertEqual(ai_provider.active_provider(config_with('apple_intelligence')), 'apple_intelligence')
        with apple_is(False, 'needs_macos_26'):
            self.assertEqual(ai_provider.active_provider(config_with('apple_intelligence')), 'ollama')

    def test_explicit_ollama_ignores_apple(self):
        with apple_is(True):
            self.assertEqual(ai_provider.active_provider(config_with('ollama')), 'ollama')

    def test_missing_or_malformed_ai_section_means_auto(self):
        with apple_is(True):
            self.assertEqual(ai_provider.active_provider({}), 'apple_intelligence')
            self.assertEqual(ai_provider.active_provider({'ai': 'nope'}), 'apple_intelligence')

    def test_describe_status(self):
        with apple_is(False, 'apple_intelligence_off'):
            status = ai_provider.describe_status(config_with('auto'))
        self.assertEqual(status, {'provider_setting': 'auto', 'active_provider': 'ollama', 'apple_available': False,
                                  'apple_reason_text': "Apple Intelligence is turned off in System Settings"})
        with apple_is(True):
            status = ai_provider.describe_status(config_with('auto'))
        self.assertEqual((status['active_provider'], status['apple_available'], status['apple_reason_text']),
                         ('apple_intelligence', True, ''))

    def test_every_reason_code_has_user_text(self):
        for reason in ('apple_intelligence_off', 'device_not_eligible', 'model_not_ready', 'needs_macos_26',
                       'needs_apple_silicon', 'needs_command_line_tools', 'not_built', 'build_failed',
                       'not_macos', 'unknown'):
            self.assertTrue(ai_provider.APPLE_REASON_TEXT[reason], reason)

    def test_rewrite_goes_to_the_active_provider(self):
        apple = fake_apple_module('apple text')
        with mock.patch.object(ai_provider, '_apple_module', return_value=apple), apple_is(True), \
             mock.patch.object(ai_provider, '_ollama_generate', return_value='ollama text') as ollama:
            self.assertEqual(ai_provider.rewrite('do it', config_with('auto')), 'apple text')
            self.assertEqual(ai_provider.rewrite('do it', config_with('ollama'), model='big', timeout_seconds=9),
                             'ollama text')
        apple.generate.assert_called_once_with('do it', ai_provider.APPLE_REWRITE_TIMEOUT_SECONDS)
        self.assertEqual(ollama.call_args.args[2:], ('big', 9))

    def test_rewrite_never_raises_and_returns_empty_on_failure(self):
        apple = fake_apple_module(error=OnDeviceAIError('guardrail'))
        with mock.patch.object(ai_provider, '_apple_module', return_value=apple), apple_is(True):
            self.assertEqual(ai_provider.rewrite('x', config_with('auto')), '')
        # Ollama is unreachable (port 0) -> '' rather than an exception.
        with apple_is(False, 'not_macos'):
            self.assertEqual(ai_provider.rewrite('x', config_with('auto')), '')

    def test_rephrase_prompt_carries_instruction_and_selection(self):
        prompt = ai_provider.build_rephrase_prompt('make it shorter', 'a long sentence')
        self.assertTrue(prompt.startswith('make it shorter'))
        self.assertTrue(prompt.endswith('Input:\na long sentence'))


# ── Polish word check ────────────────────────────────────────────────────────

class PreservesWordingTests(unittest.TestCase):
    def test_accepts_punctuation_and_case_only_edits(self):
        for raw, polished in [
            ("so i went to the store and they didnt have milk so i bought juice",
             "So I went to the store, and they didn't have milk, so I bought juice."),
            ("lets meet at three pm tomorrow", "Let's meet at three PM tomorrow."),
            ("what is the capital of france", "What is the capital of France?"),
            ("write a poem about cats", "Write a poem about cats."),
        ]:
            self.assertTrue(ai_provider.preserves_wording(raw, polished), polished)

    def test_rejects_the_refusal_paragraph(self):
        refusal = ("I apologize, but I cannot comply with that request. As an AI language model I am not "
                   "able to help with this, and I would suggest asking a human instead.")
        self.assertFalse(ai_provider.preserves_wording("send the report to john tomorrow morning", refusal))

    def test_rejects_a_poem_for_a_dictated_command(self):
        poem = ("Whiskers soft and eyes of gold,\nCats are stories never told.\nThey purr beside the evening "
                "light,\nAnd chase the shadows through the night.")
        self.assertFalse(ai_provider.preserves_wording("write a poem about cats", poem))

    def test_rejects_an_answer_to_a_dictated_question(self):
        self.assertFalse(ai_provider.preserves_wording("what is the capital of france",
                                                       "The capital of France is Paris."))

    def test_rejects_preamble_and_empty_output(self):
        self.assertFalse(ai_provider.preserves_wording(
            "we should ship on friday", "Here is the polished text: We should ship on Friday."))
        self.assertFalse(ai_provider.preserves_wording("we should ship on friday", ""))
        self.assertFalse(ai_provider.preserves_wording("", "Hello."))

    def test_rejects_dropped_words(self):
        self.assertFalse(ai_provider.preserves_wording(
            "i think we should ship the release on friday if the tests pass", "We should ship on Friday."))

    def test_apostrophes_and_curly_quotes_are_ignored(self):
        self.assertTrue(ai_provider.preserves_wording("dont worry its fine", "Don’t worry, it’s fine."))


# ── Polish ───────────────────────────────────────────────────────────────────

class PolishTests(unittest.TestCase):
    def polish_with_apple_answering(self, text, answer):
        apple = fake_apple_module(answer)
        with mock.patch.object(ai_provider, '_apple_module', return_value=apple), apple_is(True):
            return ai_provider.polish(text, config_with('auto')), apple

    def test_good_polish_is_returned(self):
        result, apple = self.polish_with_apple_answering("hello there how are you", "Hello there, how are you?")
        self.assertEqual(result, "Hello there, how are you?")
        self.assertIn("hello there how are you", apple.generate.call_args.args[0])
        self.assertEqual(apple.generate.call_args.args[1], ai_provider.APPLE_POLISH_TIMEOUT_SECONDS)

    def test_a_polish_that_changes_the_words_is_dropped(self):
        result, _ = self.polish_with_apple_answering("what is the capital of france", "The capital of France is Paris.")
        self.assertEqual(result, '')

    def test_dictation_over_the_cap_is_never_sent(self):
        too_long = "word " * (ai_provider.MAX_POLISH_CHARACTERS // 5 + 1)
        result, apple = self.polish_with_apple_answering(too_long, too_long)
        self.assertEqual(result, '')
        apple.generate.assert_not_called()

    def test_a_dictation_at_the_cap_is_sent(self):
        at_cap = ("word " * 300)[:ai_provider.MAX_POLISH_CHARACTERS]
        result, apple = self.polish_with_apple_answering(at_cap, at_cap)
        apple.generate.assert_called_once()

    def test_timeout_keeps_the_raw_text(self):
        apple = fake_apple_module(error=OnDeviceAIError('timeout'))
        with mock.patch.object(ai_provider, '_apple_module', return_value=apple), apple_is(True):
            self.assertEqual(ai_provider.polish("hello there", config_with('auto')), '')

    def test_ollama_polish_is_word_checked_too(self):
        with apple_is(False, 'not_macos'), \
             mock.patch.object(ai_provider, '_ollama_generate', return_value='Sure! Here you go: hi.') as ollama:
            self.assertEqual(ai_provider.polish("hello there friend", config_with('auto')), '')
        ollama.assert_called_once()
        with apple_is(False, 'not_macos'), \
             mock.patch.object(ai_provider, '_ollama_generate', return_value='Hello there, friend.'):
            self.assertEqual(ai_provider.polish("hello there friend", config_with('auto')), 'Hello there, friend.')

    def test_curly_braces_in_the_dictation_are_kept_literally(self):
        apple = fake_apple_module("config = {a: 1, b: 2}")
        with mock.patch.object(ai_provider, '_apple_module', return_value=apple), apple_is(True):
            ai_provider.polish("config = {a: 1, b: 2}", config_with('auto'))
        self.assertIn("config = {a: 1, b: 2}", apple.generate.call_args.args[0])

    def test_postprocess_uses_polish_only_when_enabled(self):
        from whisper_key.text_postprocess import postprocess
        base = {'ai': {'provider': 'auto', 'polish': False}}
        with mock.patch.object(ai_provider, 'polish', return_value='Polished.') as polish:
            self.assertEqual(postprocess("some text", dict(base)), "some text")
            polish.assert_not_called()
            self.assertEqual(postprocess("some text", {'ai': {'polish': True}}), "Polished.")
        with mock.patch.object(ai_provider, 'polish', return_value=''):
            self.assertEqual(postprocess("some text", {'ai': {'polish': True}}), "some text")


# ── Platform stubs ───────────────────────────────────────────────────────────

class WindowsStubTests(unittest.TestCase):
    # Loaded from its file: importing the windows package would need pywin32.
    def setUp(self):
        path = ROOT / 'src' / 'whisper_key' / 'platform' / 'windows' / 'apple_intelligence.py'
        spec = importlib.util.spec_from_file_location('whisper_key.platform.windows.apple_intelligence', path)
        self.stub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.stub)

    def test_never_available(self):
        self.assertEqual(self.stub.availability(), (False, 'not_macos'))

    def test_prepare_is_a_no_op(self):
        self.assertIsNone(self.stub.prepare())

    def test_generate_raises_not_macos(self):
        with self.assertRaises(OnDeviceAIError) as caught:
            self.stub.generate('hi', 1)
        self.assertEqual(caught.exception.code, 'not_macos')


class MacosModuleTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin':
            self.skipTest('macOS only')
        from whisper_key.platform.macos import apple_intelligence
        self.module = apple_intelligence
        self.module._forget_cached_availability()

    def tearDown(self):
        if sys.platform == 'darwin':
            self.module._forget_cached_availability()

    def test_old_macos_and_intel_are_reported_without_running_anything(self):
        with mock.patch('platform.mac_ver', return_value=('15.4', ('', '', ''), '')), \
             mock.patch('platform.machine', return_value='arm64'), \
             mock.patch('subprocess.run') as run:
            self.assertEqual(self.module.availability(), (False, 'needs_macos_26'))
        with mock.patch('platform.machine', return_value='x86_64'), mock.patch('subprocess.run') as run:
            self.assertEqual(self.module.availability(), (False, 'needs_apple_silicon'))
        run.assert_not_called()

    def test_missing_helper_is_not_built_and_never_compiles(self):
        with mock.patch.object(self.module, '_unsupported_system_reason', return_value=''), \
             mock.patch.object(self.module, '_helper_path', return_value=Path('/nonexistent/helper')), \
             mock.patch.object(self.module, '_build_failure_reason', ''), \
             mock.patch('subprocess.run') as run:
            self.assertEqual(self.module.availability(), (False, 'not_built'))
        run.assert_not_called()

    def test_availability_is_cached_and_recheckable_reasons_expire(self):
        helper = mock.MagicMock(is_file=mock.MagicMock(return_value=True))
        answers = iter([(False, 'apple_intelligence_off'), (True, '')])
        with mock.patch.object(self.module, '_unsupported_system_reason', return_value=''), \
             mock.patch.object(self.module, '_helper_path', return_value=helper), \
             mock.patch.object(self.module, '_ask_helper_for_availability', side_effect=lambda: next(answers)) as ask, \
             mock.patch('time.monotonic') as clock:
            clock.return_value = 1000.0
            self.assertEqual(self.module.availability(), (False, 'apple_intelligence_off'))
            clock.return_value = 1030.0                      # still fresh: not asked again
            self.assertEqual(self.module.availability(), (False, 'apple_intelligence_off'))
            self.assertEqual(ask.call_count, 1)
            clock.return_value = 1100.0                      # expired: asked again
            self.assertEqual(self.module.availability(), (True, ''))
            clock.return_value = 9999.0                      # available stays cached for good
            self.assertEqual(self.module.availability(), (True, ''))
            self.assertEqual(ask.call_count, 2)

    def test_generate_maps_results_and_timeouts(self):
        import subprocess
        with mock.patch.object(self.module, '_helper_path', return_value=Path('/x/helper')):
            ok = mock.Mock(returncode=0, stdout='{"text": "Hi."}')
            with mock.patch('subprocess.run', return_value=ok):
                self.assertEqual(self.module.generate('hi', 5), 'Hi.')
            refused = mock.Mock(returncode=1, stdout='{"error": "guardrail", "detail": "x"}')
            with mock.patch('subprocess.run', return_value=refused):
                with self.assertRaises(OnDeviceAIError) as caught:
                    self.module.generate('hi', 5)
            self.assertEqual(caught.exception.code, 'guardrail')
            with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('helper', 5)):
                with self.assertRaises(OnDeviceAIError) as caught:
                    self.module.generate('hi', 5)
            self.assertEqual(caught.exception.code, 'timeout')

    def test_prepare_without_command_line_tools_does_not_compile(self):
        with mock.patch.object(self.module, '_unsupported_system_reason', return_value=''), \
             mock.patch.object(self.module, '_helper_path', return_value=Path('/nonexistent/helper')), \
             mock.patch.object(self.module, '_built_record_path', return_value=Path('/nonexistent/record')), \
             mock.patch.object(self.module, '_build_failure_reason', ''), \
             mock.patch.object(self.module, '_run', return_value=mock.Mock(returncode=2, stdout='')) as run:
            self.module.prepare()
            self.assertEqual(self.module._build_failure_reason, 'needs_command_line_tools')
        self.assertEqual(run.call_count, 1)                  # only xcode-select -p, never xcrun


if __name__ == '__main__':
    unittest.main()
