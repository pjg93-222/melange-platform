"""Synthetic retained-file/PCM safety tests; no project audio, model or network."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import wave
ROOT = Path(__file__).resolve().parents[3]


_DENY = False  # audit hooks cannot be removed; the guard is armed only while code under test runs


def deny_external(event, args):
    if _DENY and event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'}:
        raise AssertionError('External activity forbidden')


sys.addaudithook(deny_external)
MODULES = {}
HASHES = {}
for name in ['quarantine_logical_duplicates', 'quarantine_zero_rms', 'repair_canonical_wj_pcm_contract']:
    path = ROOT/'fec/speech'/(name+'.py')
    spec=importlib.util.spec_from_file_location('retained_fixture_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    MODULES[name]=module;HASHES[name]=hashlib.sha256(path.read_bytes()).hexdigest()


class RetainedAudio(unittest.TestCase):
    def root(self):
        return Path(tempfile.mkdtemp(prefix='fec-retained-audio-regression-')).resolve()

    def tearDown(self):
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((ROOT/'fec/speech'/(name+'.py')).read_bytes()).hexdigest(),digest)
        # Keep small fixture trees; no recursive deletion of a computed target.

    def manifest(self, path, rows):
        raw=''.join(json.dumps(row)+'\n' for row in rows).encode()
        path.write_bytes(raw)
        return raw

    def quarantine(self, name, manifest, destination, count):
        args=['--manifest',str(manifest),'--quarantine',str(destination)]
        if name=='quarantine_zero_rms':args+=['--expected',str(count)]
        global _DENY
        _DENY = True
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return MODULES[name].main(args)
        finally:
            _DENY = False

    def test_retained_source_and_alias_refuse_before_backup_or_move(self):
        for name in ('quarantine_logical_duplicates','quarantine_zero_rms'):
            for alias in (False,True):
                with self.subTest(name=name,alias=alias):
                    root=self.root();member=root/'members';member.mkdir();wav=member/'clip.wav';wav.write_bytes(b'keep this')
                    other=member/'..'/'members'/'clip.wav' if alias else wav
                    row={'out_wav':str(wav),'outlet':'HouseLive','sid':'synthetic','seg':1,'member':'TEST','start':0,'end':1,'match':.9,'rms':1}
                    manifest=member/'clips.jsonl';before=self.manifest(manifest,[row,{**row,'out_wav':str(other),'match':.8,'rms':0}])
                    with self.assertRaisesRegex(RuntimeError,'retained'):
                        self.quarantine(name,manifest,root/'quarantine',1)
                    self.assertEqual(wav.read_bytes(),b'keep this');self.assertEqual(manifest.read_bytes(),before)
                    self.assertFalse(list(member.glob('*.pre-*.jsonl')))

    def test_repeated_logical_loser_source_refuses_before_backup(self):
        root=self.root();member=root/'members';member.mkdir();winner=member/'winner.wav';loser=member/'loser.wav'
        winner.write_bytes(b'winner');loser.write_bytes(b'loser')
        row={'out_wav':str(winner),'outlet':'HouseLive','sid':'synthetic','seg':1,'member':'TEST','start':0,'end':1,'match':.9}
        manifest=member/'clips.jsonl';before=self.manifest(manifest,[row,{**row,'out_wav':str(loser),'match':.8},{**row,'out_wav':str(loser),'match':.7}])
        with self.assertRaisesRegex(RuntimeError,'duplicate'):
            self.quarantine('quarantine_logical_duplicates',manifest,root/'quarantine',2)
        self.assertEqual(winner.read_bytes(),b'winner');self.assertEqual(loser.read_bytes(),b'loser')
        self.assertEqual(manifest.read_bytes(),before);self.assertFalse(list(member.glob('*.pre-*.jsonl')))

    def test_distinct_files_keep_existing_selection_and_move_behavior(self):
        for name in ('quarantine_logical_duplicates','quarantine_zero_rms'):
            with self.subTest(name=name):
                root=self.root();member=root/'members';member.mkdir();winner=member/'winner.wav';loser=member/'loser.wav'
                winner.write_bytes(b'winner');loser.write_bytes(b'loser')
                row={'out_wav':str(winner),'outlet':'HouseLive','sid':'synthetic','seg':1,'member':'TEST','start':0,'end':1,'match':.9,'rms':1}
                manifest=member/'clips.jsonl';self.manifest(manifest,[row,{**row,'out_wav':str(loser),'match':.8,'rms':0}])
                self.assertEqual(self.quarantine(name,manifest,root/'quarantine',1),0)
                self.assertEqual(winner.read_bytes(),b'winner');self.assertFalse(loser.exists())
                self.assertEqual((root/'quarantine'/'loser.wav').read_bytes(),b'loser')
                self.assertEqual([json.loads(x) for x in manifest.read_text().splitlines()],[row])

    def test_guard_is_armed_only_during_guarded_calls(self):
        global _DENY
        self.assertFalse(_DENY)
        _DENY = True
        try:
            with self.assertRaises(AssertionError):os.system('exit 0')
        finally:
            _DENY = False
        self.assertEqual(os.system('exit 0'),0)

    def wav(self,path,frames,channels=1,rate=16000):
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(channels);stream.setsampwidth(2);stream.setframerate(rate)
            stream.writeframes(b'\x01\x00'*frames*channels)
        return path.read_bytes()

    def pcm_row(self,path,frames=8000,start=1):
        return {'outlet':'CSPAN','show':'Washington Journal','out_wav':str(path),'frames':frames,'start':start}

    def repair(self,manifest,receipt):
        global _DENY
        _DENY = True
        try:
            return self._repair(manifest,receipt)
        finally:
            _DENY = False

    def _repair(self,manifest,receipt):
        with mock.patch.object(sys,'argv',['fixture','--manifest',str(manifest),'--receipt',str(receipt)]), contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            return MODULES['repair_canonical_wj_pcm_contract'].main()

    def test_missing_zero_negative_noninteger_frame_contract_refuses(self):
        for frames in (None,0,-1,False,1.5,'invalid'):
            with self.subTest(frames=frames):
                root=self.root();wav=root/'clip.wav';original=self.wav(wav,8000);row=self.pcm_row(wav,frames)
                if frames is None:del row['frames']
                manifest=root/'clips.jsonl';before=self.manifest(manifest,[row]);receipt=root/'receipt.json'
                with self.assertRaisesRegex(RuntimeError,'frame'):
                    self.repair(manifest,receipt)
                self.assertEqual(wav.read_bytes(),original);self.assertEqual(manifest.read_bytes(),before);self.assertFalse(receipt.exists())

    def test_later_invalid_frame_row_preserves_earlier_trim_candidate(self):
        root=self.root();first=root/'first.wav';second=root/'second.wav'
        first_before=self.wav(first,8001);second_before=self.wav(second,8000)
        manifest=root/'clips.jsonl';before=self.manifest(manifest,[self.pcm_row(first),self.pcm_row(second,0)])
        with self.assertRaisesRegex(RuntimeError,'frame'):
            self.repair(manifest,root/'receipt.json')
        self.assertEqual(first.read_bytes(),first_before);self.assertEqual(second.read_bytes(),second_before);self.assertEqual(manifest.read_bytes(),before)

    def test_later_bad_wav_contract_preserves_earlier_trim_candidate(self):
        root=self.root();first=root/'first.wav';second=root/'second.wav'
        first_before=self.wav(first,8001);second_before=self.wav(second,8001,channels=2)
        manifest=root/'clips.jsonl';before=self.manifest(manifest,[self.pcm_row(first),self.pcm_row(second)])
        with self.assertRaisesRegex(RuntimeError,'contract'):
            self.repair(manifest,root/'receipt.json')
        self.assertEqual(first.read_bytes(),first_before);self.assertEqual(second.read_bytes(),second_before);self.assertEqual(manifest.read_bytes(),before)

    def test_invalid_start_refuses_before_trim(self):
        for start in (None,float('nan'),float('inf')):
            with self.subTest(start=start):
                root=self.root();wav=root/'clip.wav';original=self.wav(wav,8001)
                manifest=root/'clips.jsonl';before=self.manifest(manifest,[self.pcm_row(wav,start=start)])
                with self.assertRaisesRegex(RuntimeError,'start'):
                    self.repair(manifest,root/'receipt.json')
                self.assertEqual(wav.read_bytes(),original);self.assertEqual(manifest.read_bytes(),before)

    def test_valid_prefix_trim_and_integral_string_contract(self):
        for expected in (8000,'8000'):
            with self.subTest(expected=expected):
                root=self.root();wav=root/'clip.wav';self.wav(wav,8001)
                manifest=root/'clips.jsonl';self.manifest(manifest,[self.pcm_row(wav,expected)])
                receipt=root/'receipt.json';self.assertEqual(self.repair(manifest,receipt),0)
                with wave.open(str(wav),'rb') as stream:
                    self.assertEqual(stream.getnframes(),8000);self.assertEqual(stream.readframes(8000),b'\x01\x00'*8000)
                row=json.loads(manifest.read_text());self.assertEqual(row['dur'],.5);self.assertEqual(row['end'],1.5)
                self.assertEqual(row['sha256'],hashlib.sha256(wav.read_bytes()).hexdigest());self.assertEqual(json.loads(receipt.read_text())['status'],'PASS')

    def test_rerun_rebinds_already_trimmed_wav_with_stale_hash(self):
        root=self.root();wav=root/'clip.wav';self.wav(wav,8000)
        # pre-trim size: legacy 78-byte ffmpeg header + 8001 frames, now trimmed to 8000
        row=self.pcm_row(wav,8000);row['sha256']='0'*64;row['bytes']=78+2*8001
        manifest=root/'clips.jsonl';self.manifest(manifest,[row]);original=wav.read_bytes()
        self.assertEqual(self.repair(manifest,root/'receipt.json'),0)
        self.assertEqual(wav.read_bytes(),original)
        out=json.loads(manifest.read_text());self.assertEqual(out['sha256'],hashlib.sha256(original).hexdigest());self.assertEqual(out['bytes'],len(original))

    def test_exact_length_hash_mismatch_without_trim_evidence_refuses(self):
        # same length, different content (corrupt/replaced/edited) must never be rebound
        cases=(('same_size',lambda n:n),('missing',None),('grew',lambda n:n-2),('over_one_second',lambda n:n+2*16000+200),('bool',True))
        for label,stored in cases:
            with self.subTest(case=label):
                root=self.root();wav=root/'clip.wav';original=self.wav(wav,8000)
                row=self.pcm_row(wav,8000);row['sha256']='0'*64
                if callable(stored):row['bytes']=stored(len(original))
                elif stored is not None:row['bytes']=stored
                manifest=root/'clips.jsonl';before=self.manifest(manifest,[row]);receipt=root/'receipt.json'
                with self.assertRaisesRegex(RuntimeError,'not a prior trim'):
                    self.repair(manifest,receipt)
                self.assertEqual(wav.read_bytes(),original);self.assertEqual(manifest.read_bytes(),before);self.assertFalse(receipt.exists())

    def test_exact_length_hash_mismatch_in_wrong_format_refuses_before_any_trim(self):
        root=self.root();first=root/'first.wav';second=root/'second.wav'
        first_before=self.wav(first,8001);second_before=self.wav(second,8000,rate=8000)
        row=self.pcm_row(second,8000);row['sha256']='0'*64;row['bytes']=len(second_before)+2
        manifest=root/'clips.jsonl';before=self.manifest(manifest,[self.pcm_row(first),row])
        with self.assertRaisesRegex(RuntimeError,'not a prior trim'):
            self.repair(manifest,root/'receipt.json')
        self.assertEqual(first.read_bytes(),first_before);self.assertEqual(second.read_bytes(),second_before);self.assertEqual(manifest.read_bytes(),before)

    def test_whitespace_only_manifest_line_is_skipped(self):
        root=self.root();wav=root/'clip.wav';self.wav(wav,8000)
        manifest=root/'clips.jsonl';self.manifest(manifest,[self.pcm_row(wav,8000)])
        manifest.write_bytes(manifest.read_bytes()+b'   '+bytes([10]))
        self.assertEqual(self.repair(manifest,root/'receipt.json'),0)

    def test_exact_count_is_noop_and_existing_safety_limits_remain(self):
        for actual,expected,error in ((8000,8000,None),(7999,8000,'pad'),(24001,8000,'ceiling')):
            with self.subTest(actual=actual,expected=expected):
                root=self.root();wav=root/'clip.wav';original=self.wav(wav,actual)
                manifest=root/'clips.jsonl';self.manifest(manifest,[self.pcm_row(wav,expected)])
                if error:
                    with self.assertRaisesRegex(RuntimeError,error):self.repair(manifest,root/'receipt.json')
                else:self.assertEqual(self.repair(manifest,root/'receipt.json'),0)
                self.assertEqual(wav.read_bytes(),original)


if __name__=='__main__':
    unittest.main(verbosity=2)
