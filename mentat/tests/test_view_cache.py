from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from view_cache import ViewCache, file_signature


class ViewCacheTests(unittest.TestCase):
    def test_singleflight_and_mutation_isolation(self):
        cache=ViewCache()
        calls=[]
        gate=threading.Barrier(4)
        def build():
            calls.append(1)
            time.sleep(.03)
            return {'rows':[{'amount':-1}]}
        def read(_):
            gate.wait()
            return cache.get('one',build)
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(read,range(4)))
        self.assertEqual(len(calls),1)
        results[0]['rows'][0]['amount']=123
        self.assertEqual(cache.get('one',build)['rows'][0]['amount'],-1)
        self.assertEqual(cache.info()['hits'],4)

    def test_failed_builds_retry_and_eviction_is_bounded(self):
        cache=ViewCache(entries=2,max_bytes=30)
        with self.assertRaises(ValueError):cache.get('bad',lambda:float('nan'))
        self.assertEqual(cache.get('bad',lambda:{'ok':True}),{'ok':True})
        for index in range(10):cache.get(index,lambda:{'a':1})
        self.assertLessEqual(cache.info()['entries'],2)
        self.assertLessEqual(cache.info()['bytes'],30)
        cache.get('huge',lambda:{'data':'x'*100})
        self.assertNotIn('huge',cache.values)
        self.assertEqual(cache.info()['in_progress'],0)

    def test_signature_changes_for_addition_deletion_and_same_size_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'input.json'
            missing=file_signature([path])
            path.write_text('aaa')
            first=file_signature([path])
            time.sleep(.02)
            path.write_text('bbb')
            self.assertNotEqual(first,file_signature([path]))
            path.unlink()
            self.assertEqual(missing,file_signature([path]))


if __name__=='__main__':unittest.main()
