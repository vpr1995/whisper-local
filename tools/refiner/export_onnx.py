# tools/refiner/export_onnx.py
# Exports a Gemma 3 270M HF folder (base or fine-tuned) to the ONNX layout that
# local_refiner.py loads: the onnxruntime-genai model builder, the same tool that made
# onnx-community/gemma-3-270m-it-ONNX. Usage: export_onnx.py <hf_folder> <out_folder> [int4|fp32]

import os
import shutil
import sys

import transformers
from onnxruntime_genai.models import builder

# ── Config fix for transformers 5 ────────────────────────────────────────────
# transformers 5 keeps Gemma 3 RoPE in a nested dict per layer type. The builder reads
# the old flat fields; without them it silently uses theta 10000 on the global layers
# (should be 1,000,000), which damages long text.

_original_from_pretrained = transformers.AutoConfig.from_pretrained


def _flat_rope_config(*args, **kwargs):
    config = _original_from_pretrained(*args, **kwargs)
    rope = getattr(config, 'rope_parameters', None)
    if isinstance(rope, dict) and 'full_attention' in rope:
        config.rope_theta = rope['full_attention']['rope_theta']
        config.rope_local_base_freq = rope['sliding_attention']['rope_theta']
        config.rope_parameters = dict(rope['full_attention'])
    return config


transformers.AutoConfig.from_pretrained = _flat_rope_config

# ── Build ────────────────────────────────────────────────────────────────────

# Runs the builder's own command line in this process (so the config fix above
# applies), then names the files like onnx-community does: the app's FILES list and
# the path stored inside the .onnx file both stay valid.
def export(hf_folder: str, out_folder: str, precision: str = 'int4'):
    import runpy
    import onnx
    build_folder = out_folder + '.build'
    sys.argv = ['builder', '-i', hf_folder, '-o', build_folder, '-p', precision,
                '-e', 'cpu', '-c', out_folder + '.cache',
                # Full-length KV on every layer, like onnx-community: the app keeps one
                # past length for all layers. fp32 math in the int4 matmuls: the default
                # int8 activations round differently for one token than for many, so
                # decode drifts from prefill.
                '--extra_options', 'windowed_kv_cache=false', 'accuracy_level=1']
    runpy.run_module('onnxruntime_genai.models.builder', run_name='__main__')
    model = onnx.load(os.path.join(build_folder, 'model.onnx'))
    os.makedirs(os.path.join(out_folder, 'onnx'), exist_ok=True)
    onnx.save(model, os.path.join(out_folder, 'onnx', 'model_q4.onnx'), save_as_external_data=True,
              all_tensors_to_one_file=True, location='model_q4.onnx_data')
    shutil.copy(os.path.join(hf_folder, 'tokenizer.json'), out_folder)
    shutil.rmtree(build_folder)
    shutil.rmtree(out_folder + '.cache', ignore_errors=True)


if __name__ == '__main__':
    export(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'int4')
