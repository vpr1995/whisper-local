# tests/test_local_refiner.py
# The Built-in AI provider without the real model: a fake onnxruntime session
# answers from a script of token ids and a fake tokenizer maps words to ids.
# Covers stop tokens, the output cap, prefix-cache reuse and the provider routing.

import unittest
import unittest.mock as mock
from types import SimpleNamespace

import numpy as np

from whisper_key import ai_provider, local_refiner
from whisper_key.local_refiner import LocalRefiner

VOCAB = 300


class FakeTokenizer:
    def __init__(self):
        self.words = {}

    def encode(self, text, add_special_tokens=False):
        ids = [self.words.setdefault(word, 10 + len(self.words)) for word in text.split()]
        return mock.Mock(ids=ids)

    def decode(self, ids):
        names = {i: w for w, i in self.words.items()}
        return " ".join(names.get(i, f"<{i}>") for i in ids)


# Each run predicts the next id of `script`; counts the tokens it was fed.
class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.fed = []

    def get_inputs(self):
        names = ['input_ids', 'attention_mask'] + [f'past_key_values.{l}.{kind}' for l in range(2) for kind in ('key', 'value')]
        return [SimpleNamespace(name=name) for name in names]

    def run(self, _outputs, feeds):
        ids = feeds['input_ids'][0]
        self.fed.append(len(ids))
        logits = np.zeros((1, len(ids), VOCAB), dtype=np.float32)
        if len(self.fed) > 1:               # the first run is the cached prompt start
            logits[0, -1, self.script.pop(0) if self.script else 1] = 1.0
        past = feeds['past_key_values.0.key'].shape[2] + len(ids)
        kv = [np.zeros((1, 1, past, 256), dtype=np.float32)] * 4
        return [logits] + kv


def loaded_refiner(script):
    refiner = LocalRefiner()
    refiner._session = FakeSession(script)
    refiner._tokenizer = FakeTokenizer()
    refiner._kv_names = [i.name for i in refiner._session.get_inputs() if i.name.startswith('past_key_values.')]
    return refiner


class GenerateTests(unittest.TestCase):
    def test_stops_at_end_of_turn(self):
        refiner = loaded_refiner([])
        tokenizer = refiner._tokenizer
        hello, world = tokenizer.encode("hello world").ids
        refiner._session.script = [hello, world, 106, hello]
        self.assertEqual(refiner.generate("Clean: hello world", "Clean:"), "hello world")

    def test_a_looping_model_stops_at_the_cap(self):
        refiner = loaded_refiner([])
        loop = refiner._tokenizer.encode("again").ids[0]
        refiner._session.script = [loop] * 500
        output = refiner.generate("Clean: a b", "Clean:")
        body_tokens = len(refiner._tokenizer.encode("a b" + local_refiner.TURN_END).ids)
        self.assertEqual(len(output.split()), int(1.5 * body_tokens + 32))

    def test_the_prompt_start_is_computed_once(self):
        refiner = loaded_refiner([1, 1])
        refiner.generate("Clean: one", "Clean: ")
        refiner.generate("Clean: two", "Clean: ")
        # Runs: prefix, body of call 1, body of call 2. No second prefix run.
        self.assertEqual(len(refiner._session.fed), 3)

    def test_prewarm_and_generate_share_one_prompt_start(self):
        # The polish head ends in a space; both must cache it under the same key.
        refiner = loaded_refiner([1])
        refiner.prewarm("Dictation: ")
        refiner.generate("Dictation: hello", "Dictation: ")
        self.assertEqual(len(refiner._session.fed), 2)   # prefix once, then the body

    def test_not_downloaded_starts_the_download_and_gives_nothing(self):
        refiner = LocalRefiner()
        with mock.patch.object(local_refiner, 'downloaded_folder', return_value=None), \
             mock.patch.object(refiner, 'download_in_background') as download:
            self.assertEqual(refiner.generate("Clean: x", "Clean:"), "")
        download.assert_called_once()


class ProviderRoutingTests(unittest.TestCase):
    def test_builtin_is_used_only_when_chosen(self):
        with mock.patch.object(ai_provider, 'apple_availability', return_value=(False, 'not_macos')):
            self.assertEqual(ai_provider.active_provider({'ai': {'provider': 'builtin'}}), 'builtin')
            self.assertEqual(ai_provider.active_provider({'ai': {'provider': 'auto'}}), 'ollama')

    def test_rephrase_with_builtin_uses_what_auto_would(self):
        refiner = mock.Mock()
        with mock.patch.object(local_refiner, 'get_refiner', return_value=refiner), \
             mock.patch.object(ai_provider, 'apple_availability', return_value=(False, 'not_macos')), \
             mock.patch.object(ai_provider, '_ollama_generate', return_value='rewritten') as ollama:
            out = ai_provider.rewrite("make it formal", {'ai': {'provider': 'builtin'}})
        self.assertEqual(out, 'rewritten')
        ollama.assert_called_once()
        refiner.generate.assert_not_called()

    def test_polish_sends_the_prompt_start_as_the_cached_part(self):
        refiner = mock.Mock()
        refiner.generate.return_value = "Send the file."
        with mock.patch.object(local_refiner, 'get_refiner', return_value=refiner):
            out = ai_provider.polish("send the the file", {'ai': {'provider': 'builtin', 'polish': True}})
        self.assertEqual(out, "Send the file.")
        prompt, stable_prefix = refiner.generate.call_args[0]
        self.assertTrue(prompt.startswith(stable_prefix))
        self.assertIn("send the the file", prompt)
        self.assertNotIn("send the the file", stable_prefix)


if __name__ == '__main__':
    unittest.main()
