"""Explicit, evidence-bound amendments to an immutable historical completion.

These are current-state revision records, never replacement ingestion receipts.
No automatic adoption of drift. A reviewer supplies each changed path, its
original hash, exact accepted bytes, reason, and immutable supporting evidence.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

SCHEMA = "completed_source_revision_v1"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha(json.dumps({k:v for k,v in value.items() if k != 'payload_sha256'},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def artifact_matches(path, expected):
    try:
        from .compiler_worker_v2 import receipt_artifact_matches
    except ImportError:
        from compiler_worker_v2 import receipt_artifact_matches
    return receipt_artifact_matches(path, expected)


def inside(root, value):
    root = Path(root).resolve()
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('revision path must be workspace-relative')
    p = root / relative
    if not p.resolve().is_relative_to(root):
        raise ValueError('revision path escapes workspace')
    for parent in (p, *p.parents):
        if parent == root: break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('revision path contains a link')
    return p


def original_files(completion):
    files = {r['path']:r['sha256'] for r in completion['artifact_manifest']}
    for field in ('knowledge_package', 'source_slice'):
        files[completion[field+'_path']] = completion[field+'_sha256']
    return files


def current_source_review(root, record, completion, changes):
    """A fresh independent content review, explicitly not historical replay."""
    reference = record.get('current_source_review')
    if not reference: return False
    p = inside(root, reference['path'])
    if sha(p.read_bytes()) != reference['sha256']: raise ValueError('source review changed')
    review = json.loads(p.read_text(encoding='utf-8'))
    if (review.get('schema_version') != 'current_source_review_v1'
            or review.get('payload_sha256') != digest(review)
            or review.get('scope') != 'current_content_not_historical_reconstruction'
            or review.get('source_identity') != completion['source_identity']
            or review.get('status') != 'accepted' or not review.get('reviewer')
            or review.get('unresolved_current_findings') != []
            or not review.get('historical_limitations')):
        raise ValueError('incomplete independent current-source review')
    reviewed = {row['path']:row['sha256'] for row in review['reviewed_files']}
    for row in changes:
        if reviewed.get(row['current_path']) != row['sha256']:
            raise ValueError('current change was not independently reviewed')
    package = inside(root, completion['knowledge_package_path']).parent
    kp = json.loads(inside(root, completion['knowledge_package_path']).read_text(encoding='utf-8'))
    try:
        from .compiler_worker_v2 import accepted_body_integrity_issues
        from .obsidian_vault_adapter import extract_canonical_body_markdown
    except ImportError:
        from compiler_worker_v2 import accepted_body_integrity_issues
        from obsidian_vault_adapter import extract_canonical_body_markdown
    if not (package/'input_bundle.json').is_file() or accepted_body_integrity_issues(kp, package):
        raise ValueError('current source review lacks intact accepted input')
    source = kp['source']
    if source.get('source_identity') != completion['source_identity']:
        raise ValueError('current source identity changed')
    note = inside(root, completion['adapter']['source_note'])
    body = extract_canonical_body_markdown(note.read_text(encoding='utf-8-sig'))
    if sha(body.encode()) != source['canonical_body_sha256']:
        raise ValueError('current source review changed or lost original body')
    return True


def validate(root, record, completion):
    if (record.get('schema_version') != SCHEMA or record.get('payload_sha256') != digest(record)
            or record.get('completion_payload_sha256') != completion.get('payload_sha256')
            or record.get('source_id') != completion.get('source_id')
            or record.get('source_identity') != completion.get('source_identity')
            or not record.get('authorization') or not record.get('review_summary')):
        raise ValueError('invalid completed-source revision binding')
    originals = original_files(completion)
    adapter = completion.get('adapter') or {}
    note_paths = {adapter.get('source_note'), adapter.get('application_note'), *adapter.get('knowledge_points', [])} - {None, ''}
    # Ordinary amendments may only change saved interpretation/metadata notes.
    # Historical image/attribution repairs have a separate, receipt-proven route.
    legacy = record.get('legacy_receipt')
    legacy_files = {}
    if legacy:
        proof = inside(root, legacy['path'])
        if sha(proof.read_bytes()) != legacy['sha256']:
            raise ValueError('historical correction receipt changed')
        historical = json.loads(proof.read_text(encoding='utf-8'))
        if (historical.get('payload_sha256') != digest(historical)
                or historical.get('source_id') != completion['source_id']
                or historical.get('source_identity') != completion['source_identity']):
            raise ValueError('historical correction identity/hash mismatch')
        if historical.get('schema_version') == 'compiler_worker_v2_completion_receipt_v1' and (historical.get('adapter') or {}).get('mode') == 'verified_fresh_infographic_replacement':
            legacy_files = {r['path']:r['sha256'] for r in historical['artifact_manifest']}
        elif historical.get('schema_version') == 'compiler_worker_v2_source_attribution_correction_v1' and historical.get('status') == 'corrected':
            asset = str(historical['asset_path']).replace('\\', '/')
            legacy_files = {p:h for p,h in originals.items() if p.endswith('/'+asset.split('/')[-1])}
            legacy_files = {p:historical['asset_sha256'] for p in legacy_files}
        else:
            raise ValueError('unsupported historical correction receipt')
    protected_expected = {p:h for p,h in originals.items()
                          if p not in note_paths and not p.endswith('.html')
                          and p not in {completion['knowledge_package_path'],completion['source_slice_path']}
                          and '/assets/' not in p}
    protected_supplied = {p['path']:p['sha256'] for p in record.get('protected_inputs', [])}
    if not protected_expected.items() <= protected_supplied.items():
        raise ValueError('revision must preserve every original input/capture/ledger')
    package = inside(root, completion['knowledge_package_path']).parent
    for name in ('input_bundle.json', 'canonical_source.md', 'source_capture.md'):
        p = inside(root, (package/name).relative_to(root).as_posix())
        expected = protected_supplied.get(p.relative_to(root).as_posix())
        matches = (sha(p.read_bytes()) == expected if name == 'source_capture.md'
                   else artifact_matches(p, expected)) if p.exists() else True
        if not matches:
            raise ValueError('revision lacks protected accepted input: '+name)
    changes = record.get('changes')
    if not isinstance(changes, list) or not changes:
        raise ValueError('empty completed-source revision')
    independently_revalidated = current_source_review(root, record, completion, changes)
    seen = set()
    for item in changes:
        path = item['original_path']
        if path in seen or originals.get(path) != item.get('original_sha256'):
            raise ValueError('revision does not bind an original artifact')
        seen.add(path)
        if path.endswith('.html') or 'imagegen_attempts' in path or not item.get('reason'):
            raise ValueError('use the existing projection/ledger owner for this artifact')
        current = inside(root, item['current_path'])
        if path in note_paths:
            if item['current_path'] != path or current.suffix.lower() != '.md':
                raise ValueError('saved-note revisions cannot relocate files')
        elif path in {completion['knowledge_package_path'],completion['source_slice_path']}:
            if item['current_path'] != path:
                raise ValueError('package/slice revisions cannot relocate files')
            # An attribution record is not permission to rebind an arbitrary KP.
            # A missing historical snapshot remains blocked, not manufactured.
            if not independently_revalidated and (not legacy or historical.get('schema_version') != 'compiler_worker_v2_source_attribution_correction_v1'
                    or path != completion['source_slice_path'] or item['current_path'] != path
                    or item['sha256'] != historical.get('source_slice_sha256')):
                raise ValueError('package/slice revision lacks exact historical source proof')
        elif '/assets/' in path:
            if legacy_files.get(item['current_path']) != item['sha256']:
                raise ValueError('image revision lacks its original historical image receipt')
        else:
            raise ValueError('immutable original input is not amendable')
        if not current.is_file() or not artifact_matches(current, item['sha256']):
            raise ValueError('accepted revision content drift: ' + item['current_path'])
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('revision requires explicit supporting evidence')
        for proof in evidence:
            p = inside(root, proof['path'])
            if not p.is_file() or sha(p.read_bytes()) != proof['sha256'] or not proof.get('supports'):
                raise ValueError('revision supporting evidence changed')
    # Even ordinary source-note metadata amendments cannot alter original prose.
    kp = json.loads(inside(root, completion['knowledge_package_path']).read_text(encoding='utf-8'))
    if (kp.get('source') or {}).get('retention_mode') == 'full_body_required':
        try:
            from .obsidian_vault_adapter import extract_canonical_body_markdown
        except ImportError:
            from obsidian_vault_adapter import extract_canonical_body_markdown
        body = extract_canonical_body_markdown(inside(root, adapter['source_note']).read_text(encoding='utf-8-sig'))
        if sha(body.encode()) != kp['source']['canonical_body_sha256']:
            raise ValueError('revision changed original body in saved source note')
    for protected in record.get('protected_inputs', []):
        p = inside(root, protected['path'])
        if not p.is_file() or not artifact_matches(p, protected['sha256']):
            raise ValueError('revision protected original input changed')
    return {item['original_path']:item for item in changes}


def read_current(root, package, completion):
    pointer = inside(root, (package / 'completion_revision_current.json').relative_to(root).as_posix())
    if not pointer.exists(): return {}
    p = json.loads(pointer.read_text(encoding='utf-8'))
    if p.get('payload_sha256') != digest(p):
        raise ValueError('completed-source revision pointer changed')
    path = inside(root, p['revision_path'])
    if not path.resolve().is_relative_to(package.resolve() / 'completion_revisions'):
        raise ValueError('revision pointer targets another package')
    if sha(path.read_bytes()) != p['revision_file_sha256']:
        raise ValueError('completed-source revision receipt changed')
    record = json.loads(path.read_text(encoding='utf-8'))
    return validate(root, record, completion)


def accept(root, package, completion, record):
    root, package = Path(root).resolve(), Path(package).absolute()
    package = inside(root, package.relative_to(root).as_posix())
    lock = inside(root, (package / 'completion_revision.lock').relative_to(root).as_posix())
    with lock.open('x', encoding='utf-8') as owned_lock:
        owned_lock.write('exclusive completed-source revision activation\n')
        owned_lock.flush()
        try:
            return _accept_locked(root, package, completion, record)
        finally:
            owned_lock.close()
            lock.unlink()


def _accept_locked(root, package, completion, record):
    """Install an explicitly reviewed record with compare-and-swap protection.

    The record includes the entire current accepted change set, original receipt
    hash and previous pointer hash. No content or original receipt is written.
    """
    root, package = Path(root).resolve(), Path(package).resolve()
    if not package.is_relative_to(root): raise ValueError('package outside workspace')
    original = inside(root, record.get('completion_path', (package/'completion_receipt.json').relative_to(root).as_posix()))
    if original.parent != package: raise ValueError('completion is outside revision package')
    original_bytes = original.read_bytes()
    if json.loads(original_bytes) != completion or completion.get('payload_sha256') != digest(completion):
        raise ValueError('original completion changed')
    validate(root, record, completion)
    pointer = package / 'completion_revision_current.json'
    prior = sha(pointer.read_bytes()) if pointer.exists() else ''
    if record.get('previous_pointer_sha256', '') != prior:
        raise ValueError('stale revision plan')
    folder = inside(root, (package / 'completion_revisions').relative_to(root).as_posix())
    folder.mkdir(exist_ok=True)
    target = inside(root, (folder / (record['payload_sha256'] + '.json')).relative_to(root).as_posix())
    data = (json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    if target.exists() and target.read_bytes() != data: raise ValueError('immutable revision conflict')
    if not target.exists():
        with target.open('xb') as f: f.write(data)
    validate(root, record, completion)
    if original.read_bytes() != original_bytes or (sha(pointer.read_bytes()) if pointer.exists() else '') != prior:
        raise ValueError('revision state changed before activation')
    value = {'revision_path': target.relative_to(root).as_posix(), 'revision_file_sha256':sha(data)}
    value['payload_sha256'] = digest(value)
    pointer = inside(root, pointer.relative_to(root).as_posix())
    temp = inside(root, pointer.with_suffix('.tmp').relative_to(root).as_posix())
    with temp.open('x',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2)
    os.replace(temp,pointer)
    read_current(root,package,completion)
    return target
