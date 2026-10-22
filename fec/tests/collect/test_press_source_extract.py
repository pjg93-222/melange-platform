import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'fec'),str(ROOT/'outputs/press_recovery_20260920/vendor')]
from collect.press_source_extract import extract_source_article

def saved(test,digest):
    # Saved official pages live in the gitignored run folder, so a fresh clone skips these cases.
    path=ROOT/'outputs/press_recovery_20260920/responses'/(digest+'.html')
    if not path.is_file(): test.skipTest('saved response not in this tree: '+path.name)
    return path.read_bytes()

class OfficialBodies(unittest.TestCase):
    def test_saved_huffman_and_takano_articles(self):
        fixtures=[('huffman.house.gov','2dadf810642369b0b22fc9e3037b36364e6311b9a5e9544e67065530969b3c20','2014-06-19'),
                  ('takano.house.gov','42da587bc052e41f0e762eddc0b15ec18529c373c3616d84a7f8e210528cd885','2017-02-17')]
        for host,digest,day in fixtures:
            body=saved(self,digest)
            result=extract_source_article(body,'https://'+host+'/press-release')
            self.assertEqual(result['date_detail'],day);self.assertGreater(len(result['body'].split()),100)
            self.assertEqual(result['extractor'],'source:#press')

    def test_elementor_body_and_visible_publication_date(self):
        digest='328a9ed70577d4a48d48a9518e9425e57409061423a2cf40c5b6d170ab1d72ef'
        body=saved(self,digest)
        result=extract_source_article(body,'https://www.banks.senate.gov/press-releases/test')
        self.assertEqual(result['date_detail'],'2025-09-03')
        self.assertIn('Criminal Reforms',result['body'])

    def test_error_page_and_unrelated_heading_are_not_articles(self):
        body=' '.join(['Long navigation text with many repeated items']*30)
        for title in ['Page Not Found','Office Locations']:
            self.assertIsNone(extract_source_article(f'<h1>{title}</h1><div id="press">{body}</div>',
                'https://huffman.house.gov/press-releases/new-coastal-legislation','New coastal legislation'))

if __name__=='__main__':unittest.main()
