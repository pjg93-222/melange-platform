"""Convert the existing local candidate-only Qwen model; no downloads or APIs."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech import classify_member_longform_full_turns_local as local


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=local.DEFAULT_MODEL)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    # The converter is only allowed to consume already downloaded local weights.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    files = [args.model / name for name in ('config.json', 'generation_config.json', 'model.safetensors',
                                            'tokenizer.json', 'tokenizer_config.json', 'merges.txt', 'vocab.json')]
    inputs = [{'path': str(path.resolve()), 'sha256': SMR.sha256_file(path)} for path in files]
    if args.out.exists():
        raise ValueError('conversion requires a new directory; never overwrite a model')
    import torch
    from ctranslate2.converters import TransformersConverter
    torch.set_num_threads(2)
    converter = TransformersConverter(str(args.model.resolve()), load_as_float16=True,
                                      low_cpu_mem_usage=True, trust_remote_code=False)
    converter.convert(str(args.out.resolve()), quantization='int8')
    if any(SMR.sha256_file(Path(item['path'])) != item['sha256'] for item in inputs):
        raise ValueError('original model changed during conversion')
    outputs = [{'path': str(path.resolve()), 'sha256': SMR.sha256_file(path)}
               for path in sorted(args.out.iterdir()) if path.is_file()]
    SMR.write_json_atomic(args.out / 'conversion.receipt.json', {
        'schema': 'member-longform-local-candidate-model-conversion-v1', 'status': 'PASS_CONVERSION_ONLY',
        'model_id': local.MODEL_ID, 'model_revision': local.MODEL_REVISION,
        'original_model': str(args.model.resolve()), 'converted_model': str(args.out.resolve()),
        'quantization': 'int8', 'inputs': inputs, 'outputs': outputs,
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'downloads': False, 'external_api_requests': 0, 'speaker_or_acoustic_models_changed': False,
        'bit_identical_transformers_output_claimed': False})
    print('PASS_CONVERSION_ONLY', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
