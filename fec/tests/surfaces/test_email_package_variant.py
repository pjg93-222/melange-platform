# -*- coding: utf-8 -*-
"""Contract suite for the all-in-one email attachment built by fec/surfaces/make_email_package.py.

THE REGRESSION THIS PINS (2026-09-05).  The combined dashboard grew a fourth surface (the staffer
network, ~4.12 MB of JSON that costs ~640 KB compressed) and the all-in-one zip went from
14,167,350 to 14,808,817 bytes against a 14,680,064 budget.  build_zip refused the package and the
chain halted before the audit.  BUDGET_BYTES is an email ATTACHMENT limit sized for base64
overhead, so raising it is the owner's call; the resolution was to email the THREE-surface variant
(Moneyball_Email.html) while the four-surface Moneyball.html stays the file-open deliverable.

So the properties that matter are: the zip is built from Moneyball_Email.html and NOT from
Moneyball.html even when both sit in the tree; a recipient still opens a file called
Moneyball.html; the manifest hash-binds the SOURCE (which is what fec/audit/audit_full.py
re-checks); a missing source refuses by name; and the budget check still fires -- with a negative
control proving the same call passes under the real budget.

Everything runs in a temp tree.  This suite never reads or writes the live tree.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_email_package_variant.py
"""
import hashlib
import os
import random
import re
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, FEC)
from surfaces import make_email_package as MEP  # noqa: E402

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def raises(fn, needle):
    """Run fn and return (raised, message).  A RuntimeError whose text contains `needle` passes."""
    try:
        fn()
    except RuntimeError as error:
        return (needle in str(error)), str(error)
    except Exception as error:                       # a different exception is NOT the contract
        return False, '%s: %s' % (type(error).__name__, error)
    return False, '<no exception raised>'


def seed_tree(root, email_body, monolith_body='<html>FOUR SURFACE - MUST NOT BE EMAILED</html>'):
    """A tree holding every source the packager needs, plus a decoy Moneyball.html."""
    for name in MEP.FAMILY_FILES:
        (os.path.join(root, name))
        with open(os.path.join(root, name), 'w', encoding='utf-8') as handle:
            handle.write('<html>%s</html>' % name)
    with open(os.path.join(root, 'Moneyball_Email.html'), 'w', encoding='utf-8') as handle:
        handle.write(email_body)
    # The four-surface document lives in the tree too and must be ignored by the packager.
    with open(os.path.join(root, 'Moneyball.html'), 'w', encoding='utf-8') as handle:
        handle.write(monolith_body)


def main():
    # This suite exercises the existing packaging contract with legacy fixtures;
    # generation validation is covered independently by test_honors_publication.
    os.environ['HONORS_MODE'] = 'legacy'
    print('[a] the packaging table')
    pairs = MEP.entries(MEP.MONOLITH_FILES)
    ok('the all-in-one is one (source, arcname) pair', len(pairs) == 1, pairs)
    ok('the source is the three-surface email variant', pairs[0][0] == 'Moneyball_Email.html', pairs)
    ok('the arcname recipients open is unchanged', pairs[0][1] == 'Moneyball.html', pairs)
    ok('the family files are still plain names (source == arcname)',
       all(source == arcname for source, arcname in MEP.entries(MEP.FAMILY_FILES)))
    ok('there are still eight family surfaces', len(MEP.FAMILY_FILES) == 8, len(MEP.FAMILY_FILES))
    ok('the attachment budget is unchanged at 14 MiB',
       MEP.BUDGET_BYTES == 14 * 1024 * 1024, MEP.BUDGET_BYTES)

    root = tempfile.mkdtemp(prefix='melange_email_pkg_')
    try:
        email_body = '<html>THREE SURFACE EMAIL VARIANT</html>'
        seed_tree(root, email_body)
        out_dir = os.path.join(root, 'outputs', 'email_package')
        manifest = MEP.build(root, out_dir)

        print('[b] the all-in-one zip carries the email variant under the familiar name')
        all_in_one = os.path.join(out_dir, 'Moneyball_Email_Package_AllInOne.zip')
        with zipfile.ZipFile(all_in_one) as archive:
            names = archive.namelist()
            zipped = archive.read('Moneyball.html')
        ok('the zip holds exactly one member', names == ['Moneyball.html'], names)
        ok('that member is named Moneyball.html', 'Moneyball.html' in names, names)
        ok('the source name does not appear inside the zip', 'Moneyball_Email.html' not in names, names)
        with open(os.path.join(root, 'Moneyball_Email.html'), 'rb') as handle:
            source_bytes = handle.read()
        ok('its bytes are Moneyball_Email.html byte for byte', zipped == source_bytes,
           '%r vs %r' % (zipped[:60], source_bytes[:60]))
        with open(os.path.join(root, 'Moneyball.html'), 'rb') as handle:
            decoy = handle.read()
        ok('the four-surface Moneyball.html in the tree was NOT the thing zipped', zipped != decoy)

        print('[c] the receipt hash-binds the source, and names the delivered arcname')
        monolith_inputs = manifest['monolith_inputs']
        ok('there is exactly one all-in-one input', len(monolith_inputs) == 1, monolith_inputs)
        item = monolith_inputs[0]
        ok('the receipt records source Moneyball_Email.html', item['name'] == 'Moneyball_Email.html', item)
        ok('the receipt records arcname Moneyball.html', item['arcname'] == 'Moneyball.html', item)
        ok('the recorded sha256 is the SOURCE file, not the decoy',
           item['sha256'] == hashlib.sha256(source_bytes).hexdigest()
           and item['sha256'] != hashlib.sha256(decoy).hexdigest())
        ok('the recorded byte count is the source file', item['bytes'] == len(source_bytes), item)
        # This is the join audit_full.py makes: os.path.join(ROOT, item['name']) must resolve.
        ok("the audit's join root/name resolves to a real file",
           os.path.isfile(os.path.join(root, item['name'])))
        ok('the package manifest lists the arcname as its contents',
           manifest['packages']['outputs/email_package/Moneyball_Email_Package_AllInOne.zip']['files']
           == ['Moneyball.html'])
        ok('the data/money alias is the same package',
           manifest['packages']['data/money/Moneyball_email.zip']['sha256']
           == manifest['packages']['outputs/email_package/Moneyball_Email_Package_AllInOne.zip']['sha256'])
        ok('the family half is untouched: eight hash-bound inputs', len(manifest['inputs']) == 8)
        ok('the schema and status are unchanged',
           manifest['schema'] == 'email-package-v2' and manifest['status'] == 'CURRENT_HASH_BOUND')
        ok('no .tmp scratch survived the build',
           not [n for n in os.listdir(out_dir) if n.endswith('.tmp')], os.listdir(out_dir))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print('[d] a missing email variant refuses, and says which file')
    root = tempfile.mkdtemp(prefix='melange_email_pkg_')
    try:
        seed_tree(root, '<html>x</html>')
        os.remove(os.path.join(root, 'Moneyball_Email.html'))
        hit, message = raises(lambda: MEP.build(root, os.path.join(root, 'outputs', 'email_package')),
                              'email package input is missing')
        ok('the build refuses when Moneyball_Email.html is absent', hit, message)
        ok('the refusal names Moneyball_Email.html', 'Moneyball_Email.html' in message, message)
        ok('the four-surface Moneyball.html present in the tree is NOT silently substituted',
           os.path.isfile(os.path.join(root, 'Moneyball.html')) and hit)
        ok('the refused all-in-one zip was not left behind',
           not os.path.isfile(os.path.join(root, 'outputs', 'email_package',
                                           'Moneyball_Email_Package_AllInOne.zip')))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print('[e] the budget check still fires -- with a negative control')
    root = tempfile.mkdtemp(prefix='melange_email_pkg_')
    try:
        # 409,600 chars of seeded random hex.  Hex is 4 bits per character so deflate packs it
        # roughly 7:1 -- it lands near 59 KB zipped, which is what makes this a real test: the
        # budget assertion below is about the COMPRESSED size, tens of times over the patched
        # budget, not about a few bytes of zip header.
        rng = random.Random(20260905)
        big = ''.join('%08x' % rng.getrandbits(32) for _ in range(12800))   # 409,600 chars
        seed_tree(root, big)
        out_dir = os.path.join(root, 'outputs', 'email_package')
        destination = os.path.join(out_dir, 'Moneyball_Email_Package_AllInOne.zip')
        tiny_budget = 1024

        # NEGATIVE CONTROL: under the real budget this exact call must SUCCEED.  Without it,
        # "it raised" would prove nothing about the budget.
        MEP.build_zip(_P(destination), _P(root), MEP.MONOLITH_FILES)
        real_size = os.path.getsize(destination)
        with open(destination, 'rb') as handle:
            real_hash = hashlib.sha256(handle.read()).hexdigest()
        ok('control: the same package builds cleanly under the real 14 MiB budget',
           os.path.isfile(destination) and real_size <= MEP.BUDGET_BYTES,
           '%d bytes' % real_size)
        ok('control: and it is far over the budget the next step patches in',
           real_size > 20 * tiny_budget, '%d bytes vs %d' % (real_size, tiny_budget))

        original = MEP.BUDGET_BYTES
        try:
            MEP.BUDGET_BYTES = tiny_budget
            hit, message = raises(
                lambda: MEP.build_zip(_P(destination), _P(root), MEP.MONOLITH_FILES),
                'email package exceeds 14 MiB')
        finally:
            MEP.BUDGET_BYTES = original
        ok('an over-budget all-in-one raises the budget error', hit, message)
        # The lossless fallback may reduce the normal ZIP before the final cap
        # refusal. Report that final size, while retaining the original package.
        reported = re.search(r'\((\d+) bytes\)$', message)
        ok('the error reports the final compressed size above the cap',
           reported is not None and tiny_budget < int(reported.group(1)) <= real_size,
           message)
        ok('the budget constant was restored', MEP.BUDGET_BYTES == 14 * 1024 * 1024)
        with open(destination, 'rb') as handle:
            retained_hash = hashlib.sha256(handle.read()).hexdigest()
        ok('the previously good zip was not replaced by an over-budget one',
           os.path.getsize(destination) == real_size and retained_hash == real_hash)
        ok('the over-budget scratch file was cleaned up',
           not [n for n in os.listdir(out_dir) if n.endswith('.tmp')], os.listdir(out_dir))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


def _P(path):
    """build_zip takes pathlib paths; keep the os.path style of this suite at the boundary."""
    from pathlib import Path
    return Path(path)


if __name__ == '__main__':
    sys.exit(main())
