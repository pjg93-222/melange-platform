# -*- coding: utf-8 -*-
r"""Line-wise, byte-exact patch cutting against the LIVE working tree.
Ruling 2026-09-19: leave the live files alone; every cutter works line-wise from ONE shared
helper.  Its census found 312 of 2,892 tracked text files MIXED in the working tree (census at
main 35521f9aa, 2026-09-19) while every blob is pure LF, so a string-replacement anchor spanning
a CRLF returns -1 exactly there while an EOL-normalised hash still reports "no drift".
  * Bytes end to end: read 'rb', written 'wb', never decoded, so a cp1252 console cannot mangle
    a non-ASCII line (DE-025).
  * Lines split on b'\n' ONLY, terminators kept: b''.join(split_lines(d)) == d.  str.splitlines
    also breaks on FF, VT, FS, GS, RS, NEL, U+2028, U+2029 and cut a patch git apply rejected.
  * Anchors match per line on content(), terminator stripped, so one anchor may span a CRLF line
    and a bare-LF line.  An edit anchor must hit EXACTLY once or AnchorError.
  * New lines take the MAJORITY terminator of the original file (tie, or no terminated line at
    all -> b'\r\n'); eol='anchor' or an explicit b'\r\n'/b'\n' overrides it.
  * Never normalises: untouched lines stay byte-identical, a mixed file stays mixed, and a file
    that ended without a terminator still does.
  * Prove before you ship: prove() applies the patch in a fresh mkdtemp() outside every
    repository and compares every byte; check_against() is `git apply --check`, read-only.
  * Apply with `git -c core.autocrlf=false apply` (apply_argv()/apply_to()) to the WORKING TREE,
    never --cached: blobs are LF.  Measured on git 2.54.0, without that flag a hunk whose old
    lines are all bare LF applies by renormalising the WHOLE file to CRLF, silently, rc 0.
  * Store a cut patch outside the repo, or under a `-text` attribute (as
    audits/laundry_list_closeout_20260918/.gitattributes does): autocrlf rewrites a committed
    patch's own CRLF lines and it stops applying.  Import-safe: no I/O or subprocess at import.
CLI: python fec/tools/live_patch.py eol <file>... | drift <file_a> <file_b>
"""
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

CRLF = b'\r\n'
LF = b'\n'
NO_NEWLINE_MARKER = b'\\ No newline at end of file\n'
# git reads a patch it produced itself; these are the only two shapes it writes.
_VALID_EOL = (CRLF, LF)
_BOMS = ((b'\xef\xbb\xbf', 'utf8'), (b'\xff\xfe', 'utf16le'), (b'\xfe\xff', 'utf16be'))
# A stray GIT_DIR in the environment would drag a "plain directory" back into a repository.
_GIT_ENV_DROP = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')
# Every apply/check this module runs carries these.  core.autocrlf=true lives in the SYSTEM
# gitconfig on this box, so it is in force even in a temp directory outside every repository:
# without the override a patch whose old lines are all bare LF applies against CRLF targets by
# renormalisation and rewrites the whole file to CRLF.  With it, application is byte-exact and
# an LF-only patch aimed at CRLF targets FAILS the check, which is the honest answer.
GIT_BYTE_EXACT = ['-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false']


# ------------------------------------------------------------------ the line model
def split_lines(data):
    """Split bytes into lines on b'\\n' ONLY, keeping terminators.

    b''.join(split_lines(data)) == data for every input; b'' -> [].  A lone CR, a form feed or
    a U+2028 never starts a new line, so only the LAST element can lack a trailing b'\\n'.
    """
    if not data:
        return []
    parts = data.split(LF)
    lines = [part + LF for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def terminator(line):
    """b'\\r\\n', b'\\n', or b'' for a last line that carries no terminator."""
    if line.endswith(CRLF):
        return CRLF
    if line.endswith(LF):
        return LF
    return b''


def content(line):
    """The line without its terminator.  For b'a\\r\\r\\n' that is b'a\\r': the extra CR is content."""
    end = terminator(line)
    return line[:len(line) - len(end)] if end else line


# ------------------------------------------------------------------ the EOL census
def eol_stats(data):
    """Count the terminators in `data` and classify the file.

    n_crlf counts b'\\r\\n'; n_bare_lf and n_lone_cr are the LFs and CRs left over.  klass is
    EMPTY, NO_EOL (no CR and no LF at all), MIXED (CRLF and bare LF both present), CR_ANOMALY
    (a lone CR and not MIXED), PURE_CRLF or PURE_LF.  ends_with_eol follows the line model: a
    file whose last byte is a lone CR does NOT end with an EOL.
    """
    bom = 'none'
    for marker, name in _BOMS:
        if data.startswith(marker):
            bom = name
            break
    n_crlf = data.count(CRLF)
    n_bare_lf = data.count(LF) - n_crlf
    n_lone_cr = data.count(b'\r') - n_crlf
    if not data:
        klass = 'EMPTY'
    elif not n_crlf and not n_bare_lf and not n_lone_cr:
        klass = 'NO_EOL'
    elif n_crlf and n_bare_lf:
        klass = 'MIXED'
    elif n_lone_cr:
        klass = 'CR_ANOMALY'
    elif n_crlf:
        klass = 'PURE_CRLF'
    else:
        klass = 'PURE_LF'
    return {'size': len(data), 'bom': bom, 'n_crlf': n_crlf, 'n_bare_lf': n_bare_lf,
            'n_lone_cr': n_lone_cr, 'n_crcrlf': data.count(b'\r\r\n'),
            'ends_with_eol': data.endswith(LF), 'klass': klass}


def raw_sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalised_sha256(data):
    """sha256 after b'\\r\\n' -> b'\\n' ONLY.  This is the hash that hides a terminator mismatch."""
    return hashlib.sha256(data.replace(CRLF, LF)).hexdigest()


def drift_report(data_a, data_b):
    """Compare two byte strings so an EOL-only difference can never read as "no drift"."""
    raw_a, raw_b = raw_sha256(data_a), raw_sha256(data_b)
    norm_a, norm_b = normalised_sha256(data_a), normalised_sha256(data_b)
    if data_a == data_b:
        verdict = 'IDENTICAL_BYTES'
    elif norm_a == norm_b:
        verdict = 'EOL_ONLY'
    else:
        verdict = 'CONTENT'
    return {'stats_a': eol_stats(data_a), 'stats_b': eol_stats(data_b),
            'raw_a': raw_a, 'raw_b': raw_b,
            'normalised_a': norm_a, 'normalised_b': norm_b, 'verdict': verdict}


def format_stats(stats):
    """One census line: size=... crlf=... lf=... lone_cr=... class=..."""
    return 'size=%d crlf=%d lf=%d lone_cr=%d class=%s' % (
        stats['size'], stats['n_crlf'], stats['n_bare_lf'], stats['n_lone_cr'], stats['klass'])


# ------------------------------------------------------------------ small helpers
def _as_bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode('utf-8')
    raise TypeError('expected str or bytes, got %s' % type(value).__name__)


def _anchor_lines(anchor):
    if isinstance(anchor, (list, tuple)):
        if not anchor:
            raise ValueError('an anchor must name at least one line')
        return [_as_bytes(item) for item in anchor]
    return [_as_bytes(anchor)]


def _payload_lines(new_lines, end):
    if isinstance(new_lines, (str, bytes)):
        raise TypeError('new_lines must be a list of lines, not a single line')
    payload = []
    for item in new_lines:
        line = _as_bytes(item)
        if LF in line or b'\r' in line:
            raise ValueError('a new line must carry no terminator of its own: %r' % (line,))
        payload.append(line + end)
    if not payload:
        raise ValueError('no new lines given')
    return payload


def _label_text(label):
    if isinstance(label, bytes):
        try:
            label = label.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('a patch label must be ASCII: %r' % (label,))
    if not isinstance(label, str):
        raise TypeError('a patch label must be str or bytes, got %s' % type(label).__name__)
    return label


def _check_label(label):
    """Refuse any label git would quote in a `diff --git` header, and any path that escapes.

    A label is joined onto a root by prove() and apply_to(), so it must stay RELATIVE and stay
    INSIDE: '..', a leading '/' and a drive letter are all refused, because os.path.join(root,
    'C:', 'x') is 'C:x' on Windows and would leave the tree entirely.  git refuses these paths
    in a patch too, but prove() writes the preimage before git ever sees it.
    """
    text = _label_text(label)
    if not text:
        raise ValueError('a patch label must not be empty')
    if '\\' in text:
        raise ValueError('a patch label must use forward slashes: %r' % (text,))
    if '"' in text:
        raise ValueError('a patch label must not contain a quote: %r' % (text,))
    for char in text:
        code = ord(char)
        if code < 0x20 or code == 0x7f:
            raise ValueError('a patch label must not contain a control character: %r' % (text,))
        if code > 0x7e:
            raise ValueError('a patch label must be ASCII: %r' % (text,))
    if text != text.strip(' '):
        raise ValueError('a patch label must not be space-padded: %r' % (text,))
    if os.path.isabs(text) or ':' in text:
        raise ValueError('a patch label must be repo-relative: %r' % (text,))
    for part in text.split('/'):
        if part in ('', '.', '..'):
            raise ValueError('a patch label must not contain %r: %r' % (part, text))
    return text


def _target_path(root, label):
    return os.path.join(root, *_check_label(label).split('/'))


class AnchorError(ValueError):
    """An edit anchor that did not hit exactly once.  Names the file, the anchor and the hits."""

    def __init__(self, label, anchor, hits):
        self.label = label
        self.anchor = anchor
        self.hits = list(hits)
        shown = repr(anchor)
        if len(shown) > 80:
            shown = shown[:77] + '...'
        where = ('lines ' + ', '.join(str(hit + 1) for hit in self.hits)) if self.hits else '0 hits'
        ValueError.__init__(self, '%s: anchor %s matched %d time(s) (%s)'
                            % (label, shown, len(self.hits), where))


# ------------------------------------------------------------------ the cutter
class LiveFile(object):
    """One file's bytes plus the line-wise edits cut against them.

    `label` is the repo-relative, forward-slash path the patch header carries.  Nothing is
    written here: ask for `after`, hand it to unified_diff()/PatchSet, and prove it.
    """

    def __init__(self, label, data):
        # UTF-16 stores an LF as two bytes, so this byte-LF line model would cut it in half and
        # corrupt the file.  The census found 11 UTF-16 Task Scheduler XML files in this tree;
        # they are not cuttable here, and saying so beats producing a plausible wrong patch.
        bom = eol_stats(data)['bom']
        if bom in ('utf16le', 'utf16be'):
            raise ValueError('%s is %s: this cutter is byte-LF and cannot cut UTF-16'
                             % (label, bom))
        self.label = label
        self.before = data
        self._lines = split_lines(data)
        # Only the original decides whether the result may end without a terminator.
        self._no_final_eol = bool(data) and not data.endswith(LF)

    @classmethod
    def read(cls, root, rel):
        """Read root/rel in binary; the label is `rel` with forward slashes."""
        with open(os.path.join(root, rel), 'rb') as handle:
            return cls(rel.replace('\\', '/'), handle.read())

    # ---- inspection
    @property
    def lines(self):
        return list(self._lines)

    @property
    def after(self):
        return b''.join(self._lines)

    @property
    def changed(self):
        return self.after != self.before

    def majority_eol(self):
        """The terminator that ends more lines in the ORIGINAL file; tie -> CRLF."""
        stats = eol_stats(self.before)
        return LF if stats['n_bare_lf'] > stats['n_crlf'] else CRLF

    def find(self, anchor, contains=False):
        """0-based indexes where `anchor` matches, compared on content() with the EOL stripped.

        `anchor` is one line's content or a list/tuple of consecutive lines' contents; with
        contains=True every element is a substring test instead of an equality test.  Because
        the terminator is stripped first, a multi-line anchor happily spans a CRLF line and a
        bare-LF line -- the case a string-replacement cutter cannot see.
        """
        wanted = _anchor_lines(anchor)
        span = len(wanted)
        hits = []
        for start in range(0, len(self._lines) - span + 1):
            for offset, want in enumerate(wanted):
                have = content(self._lines[start + offset])
                if (want not in have) if contains else (have != want):
                    break
            else:
                hits.append(start)
        return hits

    # ---- edits
    def insert_after(self, anchor, new_lines, contains=False, eol='majority'):
        start, span = self._one(anchor, contains)
        end = self._resolve_eol(eol, start + span - 1)
        payload = _payload_lines(new_lines, end)
        at = start + span
        self._lines = self._lines[:at] + payload + self._lines[at:]
        self._restore_tail(end)
        return at

    def insert_before(self, anchor, new_lines, contains=False, eol='majority'):
        start, _span = self._one(anchor, contains)
        end = self._resolve_eol(eol, start)
        payload = _payload_lines(new_lines, end)
        self._lines = self._lines[:start] + payload + self._lines[start:]
        self._restore_tail(end)
        return start

    def replace(self, anchor, new_lines, contains=False, eol='majority'):
        """Replace exactly the anchor's line(s)."""
        start, span = self._one(anchor, contains)
        end = self._resolve_eol(eol, start)
        payload = _payload_lines(new_lines, end)
        self._lines = self._lines[:start] + payload + self._lines[start + span:]
        self._restore_tail(end)
        return start

    def delete(self, anchor, contains=False):
        """Delete exactly the anchor's line(s)."""
        start, span = self._one(anchor, contains)
        self._lines = self._lines[:start] + self._lines[start + span:]
        self._restore_tail(self.majority_eol())
        return start

    # ---- internals
    def _one(self, anchor, contains):
        wanted = _anchor_lines(anchor)
        hits = self.find(wanted, contains=contains)
        if len(hits) != 1:
            raise AnchorError(self.label, anchor, hits)
        return hits[0], len(wanted)

    def _resolve_eol(self, eol, anchor_index):
        if eol == 'majority':
            return self.majority_eol()
        if eol == 'anchor':
            return terminator(self._lines[anchor_index]) or self.majority_eol()
        value = _as_bytes(eol)
        if value not in _VALID_EOL:
            raise ValueError("eol must be 'majority', 'anchor', '\\r\\n' or '\\n', got %r" % (eol,))
        return value

    def _restore_tail(self, end):
        """Keep the original file's final-EOL state, whatever the edit did to the last line."""
        if not self._lines:
            return
        if self._no_final_eol:
            for index in range(len(self._lines) - 1):
                if not terminator(self._lines[index]):
                    self._lines[index] = self._lines[index] + end
            last = self._lines[-1]
            tail = terminator(last)
            if tail:
                stripped = last[:len(last) - len(tail)]
                if stripped:
                    self._lines[-1] = stripped
                else:
                    self._lines.pop()
        elif not terminator(self._lines[-1]):
            self._lines[-1] = self._lines[-1] + end


# ------------------------------------------------------------------ patch text
def _body_line(line):
    """Emit one diff body line, with git's marker when its payload has no final newline."""
    if line.endswith(LF):
        return line
    return line + LF + NO_NEWLINE_MARKER


def unified_diff(label, before, after, context=3):
    """A `git apply`-ready patch for one existing file.  b'' when the bytes are identical.

    Body lines carry the FILE's own terminators, never a normalised one; header and hunk lines
    always end b'\\n', which is what git writes and what git reads.
    """
    text = _check_label(label)
    if before == after:
        return b''
    name = text.encode('ascii')
    out = [b'diff --git a/' + name + b' b/' + name + LF,
           b'--- a/' + name + LF,
           b'+++ b/' + name + LF]
    raw = list(difflib.diff_bytes(difflib.unified_diff, split_lines(before), split_lines(after),
                                  b'a/' + name, b'b/' + name, b'', b'', context, b''))
    # raw[0] and raw[1] are difflib's own ---/+++ lines, already emitted above.
    for line in raw[2:]:
        out.append(line + LF if line.startswith(b'@@') else _body_line(line))
    return b''.join(out)


def new_file_diff(label, data):
    """A `git apply`-ready patch that creates one file."""
    text = _check_label(label)
    if not data:
        raise ValueError('refusing to cut a new-file diff for empty data: %s' % text)
    name = text.encode('ascii')
    lines = split_lines(data)
    out = [b'diff --git a/' + name + b' b/' + name + LF,
           b'new file mode 100644' + LF,
           b'--- /dev/null' + LF,
           b'+++ b/' + name + LF,
           ('@@ -0,0 +1,%d @@' % len(lines)).encode('ascii') + LF]
    for line in lines:
        out.append(_body_line(b'+' + line))
    return b''.join(out)


_DIFF_GIT = b'diff --git a/'


def patch_labels(patch_bytes):
    """The labels a patch names, in order, from its `diff --git a/<label> b/<label>` headers.

    Every header this module writes carries the SAME label twice, so the split point is known
    from the length and is verified by reconstruction -- which a plain search for b' b/' is not,
    because a label may legally contain a space and so could contain ' b/' itself.  A header
    that does not reconstruct (a rename, or a patch from elsewhere) falls back to the rightmost
    b' b/'; one that yields nothing at all is skipped rather than guessed at.
    """
    labels = []
    for line in split_lines(patch_bytes):
        text = content(line)
        if not text.startswith(_DIFF_GIT):
            continue
        rest = text[len(_DIFF_GIT):]
        half = (len(rest) - 3) // 2
        if half > 0 and rest == rest[:half] + b' b/' + rest[:half]:
            label = rest[:half]
        else:
            label, separator, _tail = rest.rpartition(b' b/')
            if not separator or not label:
                continue
        labels.append(label.decode('ascii', 'replace'))
    return labels


class PatchSet(object):
    """Several files' diffs in insertion order.  An unchanged file contributes nothing."""

    def __init__(self):
        self._entries = []

    def add(self, live_file):
        self._entries.append(('edit', live_file))
        return self

    def add_new_file(self, label, data):
        self._entries.append(('new', (label, data)))
        return self

    def to_bytes(self):
        chunks = []
        for kind, payload in self._entries:
            if kind == 'edit':
                chunks.append(unified_diff(payload.label, payload.before, payload.after))
            else:
                chunks.append(new_file_diff(payload[0], payload[1]))
        return b''.join(chunks)

    def manifest(self):
        """What each label must hash to before and after, so an apply can refuse a moved target.

        A file this set did not change contributes nothing to the patch and nothing here.
        """
        out = {}
        for kind, payload in self._entries:
            if kind == 'edit':
                if not payload.changed:
                    continue
                label, before, after = payload.label, payload.before, payload.after
            else:
                label, before, after = payload[0], None, payload[1]
            out[_check_label(label)] = {
                'before_sha256': None if before is None else raw_sha256(before),
                'after_sha256': raw_sha256(after),
                'before_eol': None if before is None else eol_stats(before),
                'after_eol': eol_stats(after)}
        return out

    def write(self, path):
        """Write the patch 'wb' plus <path>.manifest.json beside it.  Refuses an empty patch."""
        body = self.to_bytes()
        if not body:
            raise ValueError('refusing to write an empty patch: no file in this set changed')
        with open(path, 'wb') as handle:
            handle.write(body)
        text = json.dumps(self.manifest(), sort_keys=True, indent=1)
        with open(manifest_path(path), 'wb') as handle:
            handle.write(text.encode('utf-8') + LF)
        return path


def manifest_path(patch_path):
    """Where PatchSet.write() leaves a patch's manifest."""
    return patch_path + '.manifest.json'


def load_manifest(patch_path):
    """Read the manifest sidecar PatchSet.write() left beside `patch_path`."""
    with open(manifest_path(patch_path), 'rb') as handle:
        return json.loads(handle.read().decode('utf-8'))


# ------------------------------------------------------------------ git
def _git_env():
    env = dict(os.environ)
    env['GIT_OPTIONAL_LOCKS'] = '0'
    for name in _GIT_ENV_DROP:
        env.pop(name, None)
    return env


def _run_git(args, cwd=None, timeout=120):
    """Popen with both streams on real files, per this box's drain-after-timeout hang.

    Returns (rc, stdout_bytes, stderr_bytes); rc is None when the call had to be killed.
    """
    spool = tempfile.mkdtemp(prefix='live_patch_git_')
    out_path = os.path.join(spool, 'stdout')
    err_path = os.path.join(spool, 'stderr')
    try:
        with open(out_path, 'wb') as out_handle:
            with open(err_path, 'wb') as err_handle:
                process = subprocess.Popen(['git'] + list(args), cwd=cwd, env=_git_env(),
                                           stdin=subprocess.DEVNULL,
                                           stdout=out_handle, stderr=err_handle)
                try:
                    rc = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    rc = None
        with open(out_path, 'rb') as handle:
            stdout = handle.read()
        with open(err_path, 'rb') as handle:
            stderr = handle.read()
    finally:
        shutil.rmtree(spool, ignore_errors=True)
    return rc, stdout, stderr


def apply_argv(root, patch_path):
    """The one correct apply command, for a cutter to print.  Nothing is run here.

    Both paths are made absolute: `git -C <root>` resolves a RELATIVE patch path against root,
    not against the caller's cwd, so a bare name would silently name the wrong file.
    """
    return (['git', '-C', os.path.abspath(root)] + list(GIT_BYTE_EXACT)
            + ['apply', os.path.abspath(patch_path)])


def _assert_outside_repository(path):
    probe = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(probe, '.git')):
            raise RuntimeError('refusing to prove inside a repository: %s carries .git' % probe)
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    rc, stdout, _stderr = _run_git(['rev-parse', '--show-toplevel'], cwd=path)
    if rc == 0:
        raise RuntimeError('refusing to prove inside a repository: git reports %r for %s'
                           % (stdout.strip(), path))


def prove(patch_bytes, files, timeout=120):
    """Apply `patch_bytes` in a throwaway directory and compare EVERY byte with what was cut.

    `files` maps each label to (before_bytes, expected_after_bytes); a new file's `before` is
    None and nothing is written for it.  The directory comes from tempfile.mkdtemp() and is
    asserted to be outside every repository, so this never touches LIVE, an index or a blob.
    The apply runs with GIT_BYTE_EXACT, the same flags apply_to()/apply_argv() use, so what it
    proves is what the operator's own apply will do.
    Returns {ok, check_rc, apply_rc, stderr, mismatches}; apply_rc is None when --check refused.
    """
    if not patch_bytes:
        raise ValueError('refusing to prove an empty patch')
    root = tempfile.mkdtemp(prefix='live_patch_prove_')
    try:
        work = os.path.join(root, 'work')
        os.makedirs(work)
        _assert_outside_repository(work)
        for label in files:
            before = files[label][0]
            if before is None:
                continue
            target = _target_path(work, label)
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(target, 'wb') as handle:
                handle.write(before)
        patch_path = os.path.join(root, 'cut.patch')
        with open(patch_path, 'wb') as handle:
            handle.write(patch_bytes)
        check_rc, _out, check_err = _run_git(
            GIT_BYTE_EXACT + ['apply', '--check', patch_path], cwd=work, timeout=timeout)
        apply_rc, apply_err = None, b''
        mismatches = []
        if check_rc == 0:
            apply_rc, _out2, apply_err = _run_git(
                GIT_BYTE_EXACT + ['apply', patch_path], cwd=work, timeout=timeout)
        if apply_rc == 0:
            for label in sorted(files):
                target = _target_path(work, label)
                actual = None
                if os.path.isfile(target):
                    with open(target, 'rb') as handle:
                        actual = handle.read()
                if actual != files[label][1]:
                    mismatches.append(label)
        stderr = (check_err + apply_err).decode('utf-8', 'replace')
        return {'ok': check_rc == 0 and apply_rc == 0 and not mismatches,
                'check_rc': check_rc, 'apply_rc': apply_rc,
                'stderr': stderr, 'mismatches': mismatches}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_against(root, patch_path, timeout=120):
    """`git -C <root> apply --check <patch>`: read-only, writes nothing, applies nothing.

    Runs with GIT_BYTE_EXACT so the check is strict: without it an LF-only patch passes against
    CRLF targets by renormalisation and would then rewrite the whole file on apply.  A cutter's
    patch describes the WORKING TREE's bytes, so --cached is EXPECTED to fail -- under
    core.autocrlf=true every blob is pure LF while the checkout is not.  Never check the index.
    Both paths are made absolute first: `git -C <root>` resolves a RELATIVE patch path against
    root, not against the caller's cwd.  Returns {rc, stderr, timed_out}; rc is None on timeout.
    """
    rc, _out, stderr = _run_git(['-C', os.path.abspath(root)] + GIT_BYTE_EXACT
                                + ['apply', '--check', os.path.abspath(patch_path)],
                                timeout=timeout)
    return {'rc': rc, 'stderr': stderr.decode('utf-8', 'replace'), 'timed_out': rc is None}


def _write_atomic(target, data):
    """Replace `target`'s bytes through a temp file in its own directory and os.replace().

    A plain open('wb') truncates first, so a crash mid-write loses the file; this never leaves a
    half-written target.  os.replace can raise PermissionError on this box while OneDrive or an
    AV scanner briefly holds the handle, so it is retried five times before giving up.
    """
    directory = os.path.dirname(os.path.abspath(target))
    handle, temp = tempfile.mkstemp(prefix='.live_patch_', dir=directory)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temp, target)
                return target
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _restore(root, labels, originals):
    for label in labels:
        target = _target_path(root, label)
        original = originals[label]
        if original is None:
            if os.path.isfile(target):
                os.remove(target)
        else:
            _write_atomic(target, original)


def _refusal(reason, mismatches=()):
    return {'ok': False, 'reason': reason, 'check_rc': None, 'apply_rc': None,
            'stderr': '', 'mismatches': list(mismatches)}


def _numstat_paths(listing):
    """Every path `git apply --numstat -z` reports (both sides of a rename), as str labels."""
    parts = listing.split(b'\0')
    paths = set()
    i = 0
    while i < len(parts):
        record = parts[i]
        fields = record.split(b'\t', 2)
        i += 1
        if len(fields) < 3:
            if record:                          # fail closed: an unread path would dodge the scope check
                raise ValueError('unparseable git apply --numstat record: %r' % record)
            continue
        if fields[2]:
            paths.add(fields[2].decode('utf-8', 'replace'))
        else:                                   # rename/copy: old and new path follow as own records
            pair = parts[i:i + 2]
            if len(pair) < 2 or not all(pair):
                raise ValueError('truncated rename/copy record in git apply --numstat output')
            paths.update(p.decode('utf-8', 'replace') for p in pair)
            i += 2
    return paths


def apply_to(root, patch_path, manifest=None, timeout=120):
    """Apply a cut patch to `root`'s WORKING TREE, refusing anything that moved since the cut.

    The only function here that writes into a tree, and only when called.  Three gates run
    before a byte moves: the patch's own labels must be exactly the manifest's keys (a short
    manifest would otherwise let the apply rewrite a file nobody hashed), every target's current
    bytes must hash to `before_sha256`, and a new file's path must not exist yet.  Afterwards
    every target must hash to `after_sha256` or the bytes this function read are put back
    atomically and a newly created file is removed.  `manifest` defaults to the sidecar beside
    the patch.  Both paths are made absolute, because `git -C <root>` resolves a relative patch
    path against root.  Never --cached / --index: blobs are pure LF under core.autocrlf=true, so
    a live-bytes patch cannot match them.
    Returns {ok, reason, check_rc, apply_rc, stderr, mismatches}.
    """
    root = os.path.abspath(root)
    patch_path = os.path.abspath(patch_path)
    if not os.path.isfile(patch_path):
        return _refusal('patch file not found')
    if manifest is None:
        if not os.path.isfile(manifest_path(patch_path)):
            return _refusal('no manifest beside the patch')
        manifest = load_manifest(patch_path)
    with open(patch_path, 'rb') as handle:
        named = set(patch_labels(handle.read()))
    keyed = set(manifest)
    if named != keyed:
        return _refusal('patch and manifest name different files',
                        sorted(named.symmetric_difference(keyed)))
    rc, listing, err = _run_git(
        ['-C', root] + GIT_BYTE_EXACT + ['apply', '--numstat', '-z', patch_path], timeout=timeout)
    if rc != 0:
        return _refusal('git could not list the files the patch touches: '
                        + err.decode('utf-8', 'replace').strip())
    try:
        touched = _numstat_paths(listing)
    except ValueError as exc:
        return _refusal(str(exc))
    if touched - keyed:
        return _refusal('patch touches files outside the manifest', sorted(touched - keyed))
    labels = sorted(manifest)
    originals = {}
    stale = []
    for label in labels:
        entry = manifest[label]
        target = _target_path(root, label)
        current = None
        if os.path.isfile(target):
            with open(target, 'rb') as handle:
                current = handle.read()
        originals[label] = current
        if entry['before_sha256'] is None:
            if current is not None:
                stale.append(label)
        elif current is None or raw_sha256(current) != entry['before_sha256']:
            stale.append(label)
    if stale:
        return _refusal('target moved since the patch was cut', stale)
    check_rc, _out, check_err = _run_git(
        ['-C', root] + GIT_BYTE_EXACT + ['apply', '--check', patch_path], timeout=timeout)
    if check_rc != 0:
        return {'ok': False, 'reason': 'git apply --check refused the patch',
                'check_rc': check_rc, 'apply_rc': None,
                'stderr': check_err.decode('utf-8', 'replace'), 'mismatches': []}
    apply_rc, _out2, apply_err = _run_git(
        ['-C', root] + GIT_BYTE_EXACT + ['apply', patch_path], timeout=timeout)
    stderr = (check_err + apply_err).decode('utf-8', 'replace')
    mismatches = []
    for label in labels:
        target = _target_path(root, label)
        actual = None
        if os.path.isfile(target):
            with open(target, 'rb') as handle:
                actual = handle.read()
        if actual is None or raw_sha256(actual) != manifest[label]['after_sha256']:
            mismatches.append(label)
    if apply_rc != 0 or mismatches:
        _restore(root, labels, originals)
        return {'ok': False,
                'reason': ('git apply failed' if apply_rc != 0
                           else 'result bytes do not match the manifest'),
                'check_rc': check_rc, 'apply_rc': apply_rc,
                'stderr': stderr, 'mismatches': mismatches}
    return {'ok': True, 'reason': '', 'check_rc': check_rc, 'apply_rc': apply_rc,
            'stderr': stderr, 'mismatches': []}


# ------------------------------------------------------------------ CLI
_USAGE = ('usage: live_patch.py eol <file> [...]\n'
          '       live_patch.py drift <file_a> <file_b>\n'
          'drift exits 0 IDENTICAL_BYTES, 1 EOL_ONLY, 2 CONTENT.')


def _read(path):
    with open(path, 'rb') as handle:
        return handle.read()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    if not argv:
        print(_USAGE)
        return 2
    command, rest = argv[0], argv[1:]
    if command == 'eol' and rest:
        for path in rest:
            print('%s  %s' % (path, format_stats(eol_stats(_read(path)))))
        return 0
    if command == 'drift' and len(rest) == 2:
        report = drift_report(_read(rest[0]), _read(rest[1]))
        print('a %s  %s' % (rest[0], format_stats(report['stats_a'])))
        print('b %s  %s' % (rest[1], format_stats(report['stats_b'])))
        print('normalised_a %s' % report['normalised_a'])
        print('normalised_b %s' % report['normalised_b'])
        print('verdict %s' % report['verdict'])
        return {'IDENTICAL_BYTES': 0, 'EOL_ONLY': 1, 'CONTENT': 2}[report['verdict']]
    print(_USAGE)
    return 2


if __name__ == '__main__':
    sys.exit(main())
