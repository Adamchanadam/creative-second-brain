"""Explicit, evidence-bound amendments to an immutable historical completion.

These are current-state revision records, never replacement ingestion receipts.
No automatic adoption of drift. A reviewer supplies each changed path, its
original hash, exact accepted bytes, reason, and immutable supporting evidence.
"""
from __future__ import annotations
import hashlib
import json
import os
import copy
import re
from pathlib import Path

try:
    from .path_budget import PathBudgetError, assert_generated_path
except ImportError:
    from path_budget import PathBudgetError, assert_generated_path  # type: ignore

SCHEMA = "completed_source_revision_v1"
CONTENT_REVISION_PROJECTION_VERSION = "completed_content_revision_projection_v4"
SOURCE_ATTACHMENT_REVISION_KIND = "source_attachment"
EVIDENCE_FOLDER_ID_CHARS = 12
_EVIDENCE_REQUEST_SHA256 = re.compile(r"[0-9a-f]{64}")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _rendered_registry_rows(data, key):
    """Rows from a candidate registry render, keyed like its live registry reader."""
    rows = {}
    for line_no, line in enumerate(data.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        value = str(row.get(key) or "")
        if not value or value in rows:
            raise ValueError(f"candidate registry has invalid {key} at line {line_no}")
        rows[value] = {str(name): str(item) for name, item in row.items()}
    return rows


def digest(value):
    return sha(json.dumps({k:v for k,v in value.items() if k != 'payload_sha256'},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def _evidence_folder(package: Path, kind: str, request_sha256: str) -> Path:
    """Reuse an exact legacy transaction; otherwise select the short new locator."""

    if kind not in {"content", "recovery"} or not _EVIDENCE_REQUEST_SHA256.fullmatch(request_sha256):
        raise ValueError("invalid content revision evidence identity")
    evidence_root = package / "completion_revision_evidence"
    legacy = evidence_root / f"{kind}-{request_sha256}"
    return legacy if legacy.exists() else evidence_root / f"{kind}-{request_sha256[:EVIDENCE_FOLDER_ID_CHARS]}"


def _is_legacy_evidence_folder(folder: Path) -> bool:
    return bool(re.fullmatch(r"(?:content|recovery)-[0-9a-f]{64}", folder.name))


def _staged_evidence_paths(folder: Path, index: int, target: Path) -> tuple[Path, Path]:
    """Bound staging names to their index, role and target suffix only."""

    suffix = target.suffix
    return folder / f"{index:03d}-after{suffix}", folder / f"{index:03d}-before{suffix}"


def _review_file_for_digest(folder: Path, review_sha256: str) -> Path:
    """Keep legacy transactions byte-for-byte named while new ones use a locator."""

    if not _EVIDENCE_REQUEST_SHA256.fullmatch(review_sha256):
        raise ValueError("invalid content review identity")
    locator = review_sha256 if _is_legacy_evidence_folder(folder) else review_sha256[:EVIDENCE_FOLDER_ID_CHARS]
    return folder / f"review-{locator}.json"


def _review_file_path(folder: Path, review: dict) -> Path:
    return _review_file_for_digest(folder, digest(review))


def _assert_evidence_path_budget(folder: Path, staged: list[Path]) -> None:
    """Reject an over-budget new transaction before it creates its first file."""

    try:
        assert_generated_path(folder, label="content revision evidence directory")
        for path in [
            folder / "request.json",
            folder / "transaction.json",
            folder / ".transaction.json.tmp",
            _review_file_for_digest(folder, "0" * 64),
            *staged,
        ]:
            assert_generated_path(path, label="content revision evidence")
    except PathBudgetError as exc:
        raise ValueError(str(exc)) from exc


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


def knowledge_package_for_validation(root, completion, virtual=None):
    """Decode the staged package when preflight is checking candidate bytes."""
    path = completion['knowledge_package_path']
    data = (virtual or {}).get(path)
    if data is None:
        data = inside(root, path).read_bytes()
    return json.loads(data.decode('utf-8'))


def original_body_for_current_source_review(root, package, source, source_note, virtual=None):
    """Read the immutable-original representation chosen by accepted retention mode."""
    bundle_path = package / 'input_bundle.json'
    if bundle_path.is_file():
        try:
            bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('current source review has unreadable accepted input bundle') from exc
        mode = bundle.get('source_retention_mode')
    else:
        mode = None
    mode = mode or source.get('source_retention_mode') or source.get('retention_mode')
    try:
        from .obsidian_vault_adapter import read_retained_original, retained_original_location
    except ImportError:
        from obsidian_vault_adapter import read_retained_original, retained_original_location
    try:
        location = retained_original_location(mode)
    except ValueError as exc:
        raise ValueError('current source review has unsupported source retention mode: ' + str(mode)) from exc
    if location == 'source_note_body':
        note = inside(root, source_note)
        note_bytes = (virtual or {}).get(source_note)
        note_text = note_bytes.decode('utf-8-sig') if note_bytes is not None else note.read_text(encoding='utf-8-sig')
        try:
            return read_retained_original(mode, note_text=note_text)
        except ValueError:
            # Preserve the existing fail-closed path: current_source_review
            # compares an absent body with the accepted digest below.
            return ''
    try:
        return read_retained_original(mode, package=package)
    except ValueError as exc:
        raise ValueError('current source review lacks canonical_source.md for retained original body') from exc


def current_source_review(root, record, completion, changes, virtual=None):
    """A fresh independent content review, explicitly not historical replay."""
    reference = record.get('current_source_review')
    if not reference: return False
    p = inside(root, reference['path'])
    data = (virtual or {}).get(reference['path'])  # ``virtual``: {workspace path: bytes} for files that are staged but not yet written
    if data is None: data = p.read_bytes()
    if sha(data) != reference['sha256']: raise ValueError('source review changed')
    review = json.loads(data.decode('utf-8'))
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
        # A later attachment changes only the source-note frontmatter.  Its
        # image-specific evidence is checked by validate_source_attachments;
        # it must not invalidate an earlier independent review of the actual
        # content revision that this record carries forward.
        if (
            record.get("revision_kind") == SOURCE_ATTACHMENT_REVISION_KIND
            and row.get("original_path") == (completion.get("adapter") or {}).get("source_note")
            and row.get("reason") == "Attach user-provided visual evidence to the completed source."
        ):
            continue
        if reviewed.get(row['current_path']) != row['sha256']:
            raise ValueError('current change was not independently reviewed')
    package = inside(root, completion['knowledge_package_path']).parent
    kp = knowledge_package_for_validation(root, completion, virtual)
    try:
        from .compiler_worker_v2 import accepted_body_integrity_issues
    except ImportError:
        from compiler_worker_v2 import accepted_body_integrity_issues
    source = kp['source']
    if source.get('source_identity') != completion['source_identity']:
        raise ValueError('current source identity changed')
    body = original_body_for_current_source_review(
        root, package, source, completion['adapter']['source_note'], virtual,
    )
    if not (package/'input_bundle.json').is_file() or accepted_body_integrity_issues(kp, package):
        raise ValueError('current source review lacks intact accepted input')
    if sha(body.encode()) != source['canonical_body_sha256']:
        raise ValueError('current source review changed or lost original body')
    return True


def validate(root, record, completion, *, allowed_current_hashes=None, virtual=None):
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
    independently_revalidated = current_source_review(root, record, completion, changes, virtual)
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
        allowed = (allowed_current_hashes or {}).get(item['current_path'], {item['sha256']})
        current_bytes = (virtual or {}).get(item['current_path'])
        matches = (sha(current_bytes) in allowed if current_bytes is not None
                   else current.is_file() and any(artifact_matches(current, expected) for expected in allowed))
        if not matches:
            raise ValueError('accepted revision content drift: ' + item['current_path'])
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('revision requires explicit supporting evidence')
        for proof in evidence:
            p = inside(root, proof['path'])
            data = (virtual or {}).get(proof['path'])
            if data is None and p.is_file(): data = p.read_bytes()
            if data is None or sha(data) != proof['sha256'] or not proof.get('supports'):
                raise ValueError('revision supporting evidence changed')
    validate_source_attachments(root, record, completion, virtual=virtual)
    # Even ordinary source-note metadata amendments cannot alter original prose.
    kp = knowledge_package_for_validation(root, completion, virtual)
    try:
        from .obsidian_vault_adapter import retained_original_location
    except ImportError:
        from obsidian_vault_adapter import retained_original_location
    if retained_original_location((kp.get('source') or {}).get('retention_mode')) == 'source_note_body':
        try:
            from .obsidian_vault_adapter import read_retained_original
        except ImportError:
            from obsidian_vault_adapter import read_retained_original
        note_bytes = (virtual or {}).get(adapter['source_note'])
        note_text = (note_bytes.decode('utf-8-sig') if note_bytes is not None
                     else inside(root, adapter['source_note']).read_text(encoding='utf-8-sig'))
        try:
            body = read_retained_original('full_body_required', note_text=note_text)
        except ValueError:
            body = ''
        if sha(body.encode()) != kp['source']['canonical_body_sha256']:
            raise ValueError('revision changed original body in saved source note')
    for protected in record.get('protected_inputs', []):
        p = inside(root, protected['path'])
        if not p.is_file() or not artifact_matches(p, protected['sha256']):
            raise ValueError('revision protected original input changed')
    return {item['original_path']:item for item in changes}


def validate_source_attachments(root, record, completion, *, virtual=None):
    """Validate the distinct, append-only attachment revision kind."""
    kind = record.get("revision_kind")
    rows = record.get("source_attachments")
    if rows is None:
        return
    # A content revision may carry already accepted attachment evidence forward
    # unchanged.  Only a source_attachment revision may introduce a new row;
    # either form is held to the same immutable image/evidence readback below.
    if kind != SOURCE_ATTACHMENT_REVISION_KIND and not record.get("current_source_review"):
        raise ValueError("source attachments require an accepted attachment or content revision")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source attachment revision has no attachments")
    adapter = completion.get("adapter") or {}
    source_note = str(adapter.get("source_note") or "")
    if not source_note:
        raise ValueError("source attachment completion lacks a source note")
    changes = {str(item.get("original_path") or ""): item for item in record.get("changes", []) if isinstance(item, dict)}
    if source_note not in changes:
        raise ValueError("source attachment revision does not register its source-note change")
    try:
        from .obsidian_vault_adapter import frontmatter_fields_from_text
        from .image_source_contract import inspect_image
    except ImportError:
        from obsidian_vault_adapter import frontmatter_fields_from_text
        from image_source_contract import inspect_image
    source_bytes = (virtual or {}).get(source_note)
    if source_bytes is None:
        source_bytes = inside(root, source_note).read_bytes()
    fields = frontmatter_fields_from_text(source_bytes.decode("utf-8-sig").replace("\r\n", "\n"))
    declared = fields.get("source_attachments")
    if not isinstance(declared, list):
        raise ValueError("source attachment source note has no readable source_attachments list")
    seen_hashes, expected_paths = set(), []
    # The vault may be nested: take its workspace-relative path from the registry
    # (<vault>/_registry/sources.jsonl) or, for receipts without one, the source note.
    registry = str(adapter.get("registry") or "")
    if registry.endswith("/_registry/sources.jsonl"):
        vault_name = Path(registry).parent.parent.as_posix()
    elif "/sources/" in source_note:
        vault_name = source_note.rpartition("/sources/")[0]
    else:
        raise ValueError("source attachment completion does not locate its vault")
    prefix = f"{vault_name}/assets/{str(completion.get('source_id') or '').lower()}/attachments/"
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("source attachment revision entry is invalid")
        asset_path = str(row.get("asset_path") or "")
        image_sha256 = str(row.get("sha256") or "")
        record_path = str(row.get("record_path") or "")
        record_sha256 = str(row.get("record_sha256") or "")
        if (not asset_path.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{12}\.(?:png|jpe?g|webp)", Path(asset_path).name)
                or not re.fullmatch(r"[0-9a-f]{64}", image_sha256) or image_sha256 in seen_hashes):
            raise ValueError("source attachment asset binding is invalid")
        seen_hashes.add(image_sha256)
        asset = inside(root, asset_path)
        if not asset.is_file() or sha(asset.read_bytes()) != image_sha256:
            raise ValueError("source attachment image bytes changed")
        inspection = inspect_image(asset)
        dimensions = row.get("dimensions")
        if (inspection.sha256 != image_sha256 or row.get("mime_type") != inspection.mime_type
                or not isinstance(dimensions, dict) or dimensions.get("width") != inspection.width
                or dimensions.get("height") != inspection.height):
            raise ValueError("source attachment image identity or dimensions changed")
        evidence = inside(root, record_path)
        if not evidence.is_file() or sha(evidence.read_bytes()) != record_sha256:
            raise ValueError("source attachment immutable record changed")
        try:
            evidence_value = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("source attachment immutable record is unreadable") from exc
        expected_attachment = {
            "asset_path": asset_path, "sha256": image_sha256,
            "mime_type": inspection.mime_type, "dimensions": dimensions,
            "title": row.get("title"), "summary": row.get("summary"),
            "kp_mapping": row.get("kp_mapping"),
        }
        if (evidence_value.get("payload_sha256") != digest(evidence_value)
                or evidence_value.get("source_id") != completion.get("source_id")
                or evidence_value.get("source_identity") != completion.get("source_identity")
                or evidence_value.get("attachment") != expected_attachment):
            raise ValueError("source attachment immutable record does not bind the accepted attachment")
        # Package evidence is rooted at the workspace, while source-note media
        # uses the vault-relative convention of source_assets/infographics.
        expected_paths.append(Path(asset_path).relative_to(vault_name).as_posix())
    # When this record owns the current note bytes its list must match exactly.
    # Otherwise validate() has already admitted the bytes as a later accepted
    # overlay (an infographic child), whose own record checks the appended rest.
    owns_note = sha(source_bytes) == changes[source_note].get("sha256")
    if (declared != expected_paths) if owns_note else (declared[:len(expected_paths)] != expected_paths):
        raise ValueError("source attachment source-note list does not match the accepted attachment order")


def read_current(root, package, completion, *, include_record=False, verify=True, allowed_current_hashes=None):
    """The accepted changes of the current revision ({} when there is none).  ``verify=False`` reads only the pointer and the record
    (their own hashes) and skips ``validate``: for the note writer, whose notes differ on purpose from the bytes they were accepted
    with; ``accept`` validates the whole new record before it takes effect."""
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
    if not verify:
        return record if include_record else {item['original_path']: item for item in record.get('changes', [])}
    changes = validate(root, record, completion, allowed_current_hashes=allowed_current_hashes)
    return record if include_record else changes


def _content_candidate_binding(root, package, completion, journal):
    record = read_current(root, package, completion, include_record=True)
    expected_record = (package/'completion_revisions'/(record.get('payload_sha256', '')+'.json')).relative_to(root).as_posix()
    if (journal.get('record_path') != expected_record or journal.get('completion_payload_sha256') != completion['payload_sha256']
            or journal.get('source_identity') != completion['source_identity']):
        raise ValueError('candidate evidence is not bound to the current content revision')
    application = inside(root, completion['adapter']['application_note'])
    return {'revision_path': expected_record, 'revision_payload_sha256': record['payload_sha256'],
            'completion_payload_sha256': completion['payload_sha256'], 'source_identity': completion['source_identity'],
            'application_note': application.relative_to(root).as_posix(), 'application_note_sha256': sha(application.read_bytes())}


def _checked_content_candidates(path, binding, source_id):
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if (receipt.get('payload_sha256') != digest(receipt) or receipt.get('current_revision') != binding
            or receipt.get('receipt_type') != 'new_content_candidates'
            or receipt.get('selection', {}).get('source_ids') != [source_id]
            or len(receipt.get('records', [])) != 1 or receipt['records'][0].get('source_id') != source_id):
        raise ValueError('current revision candidate evidence changed')
    return receipt


def _ensure_content_revision_candidates(root, package, completion, journal_path, journal):
    """Derive source-local evidence once; never replace historical candidates."""
    binding = _content_candidate_binding(root, package, completion, journal)
    path = inside(root, (journal_path.parent/'new_content_candidates.json').relative_to(root).as_posix())
    created = not path.exists()
    if created:
        try:
            from .obsidian_semantic_maintenance import build_new_content_candidates_receipt
        except ImportError:
            from obsidian_semantic_maintenance import build_new_content_candidates_receipt
        worker, _, _ = _modules()
        receipt = build_new_content_candidates_receipt(vault=worker.vault_path(root), recent_limit=1,
            source_ids=[completion['source_id']], application_paths=[inside(root, binding['application_note'])])
        receipt['vault'] = worker.vault_path(root).relative_to(root).as_posix()
        receipt['current_revision'] = binding
        receipt['payload_sha256'] = digest(receipt)
        with path.open('xb') as stream: stream.write(_json_bytes(receipt))
    receipt = _checked_content_candidates(path, binding, completion['source_id'])
    if _content_candidate_binding(root, package, completion, journal) != binding:
        raise ValueError('content revision changed during candidate projection')
    reference = {'path': path.relative_to(root).as_posix(), 'sha256': sha(path.read_bytes()),
                 'status': receipt['status'], 'formal_links_created': False}
    changed = journal.get('idea_candidates') != reference
    if journal.get('idea_candidates') and changed:
        raise ValueError('immutable current revision candidate reference changed')
    journal['idea_candidates'] = reference
    return created or changed


def read_current_idea_candidates(root, package, completion):
    """Completed readback is read-only and selects the current revision evidence."""
    record = read_current(root, package, completion, include_record=True)
    if not record or 'projection_evidence' not in record:
        return completion.get('idea_candidates')
    journal_path = inside(root, (inside(root, record['current_source_review']['path']).parent/'transaction.json').relative_to(root).as_posix())
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    if (journal.get('schema_version') != 'completed_content_revision_transaction_v1'
            or journal.get('payload_sha256') != digest(journal) or journal.get('state') != 'completed'):
        raise ValueError('current content revision transaction is not completed')
    reference = journal.get('idea_candidates')
    if not reference:
        raise ValueError('current revision candidate evidence missing; resume the same apply-content-revision')
    path = inside(root, reference['path'])
    if path != journal_path.parent/'new_content_candidates.json' or sha(path.read_bytes()) != reference['sha256']:
        raise ValueError('current revision candidate reference changed')
    receipt = _checked_content_candidates(path, _content_candidate_binding(root, package, completion, journal), completion['source_id'])
    if reference.get('status') != receipt['status'] or reference.get('formal_links_created') is not False:
        raise ValueError('current revision candidate status changed')
    return reference


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


def prepare_source_attachment_revision(
    root,
    package,
    completion,
    *,
    source_note_path,
    attachment,
    attachment_record,
    authorization,
    review_summary,
    prior_record=None,
):
    """Build a write-free accepted-revision candidate for one source attachment."""
    root, package = Path(root).resolve(), Path(package).resolve()
    source_note_path = inside(root, source_note_path)
    originals = original_files(completion)
    source_note = str((completion.get("adapter") or {}).get("source_note") or "")
    if not source_note or source_note_path.relative_to(root).as_posix() != source_note:
        raise ValueError("source attachment target is not this completion's source note")
    if source_note not in originals:
        raise ValueError("source attachment source note is absent from the completion receipt")
    prior = prior_record or {}
    if prior and not isinstance(prior, dict):
        raise ValueError("source attachment predecessor revision is invalid")
    attachments = prior.get("source_attachments", []) if prior else []
    if attachments and not isinstance(attachments, list):
        raise ValueError("source attachment predecessor list is invalid")
    item, evidence = dict(attachment), dict(attachment_record)
    asset_path = str(item.get("asset_path") or "")
    image_sha256 = str(item.get("sha256") or "")
    evidence_path = str(evidence.get("path") or "")
    evidence_sha256 = str(evidence.get("sha256") or "")
    if not asset_path or not image_sha256 or not evidence_path or not evidence_sha256:
        raise ValueError("source attachment revision lacks immutable attachment binding")
    if any(str(row.get("sha256") or "") == image_sha256 for row in attachments if isinstance(row, dict)):
        raise ValueError("source attachment hash is already accepted")
    changes = {
        str(change.get("original_path") or ""): dict(change)
        for change in prior.get("changes", []) if isinstance(change, dict) and change.get("original_path")
    }
    source_bytes = source_note_path.read_bytes()
    changes[source_note] = {
        "original_path": source_note,
        "original_sha256": originals[source_note],
        "current_path": source_note,
        "sha256": sha(source_bytes),
        "reason": "Attach user-provided visual evidence to the completed source.",
        "evidence": [{
            "path": evidence_path,
            "sha256": evidence_sha256,
            "supports": "The immutable attachment record binds this source-note list change to the reviewed image.",
        }],
    }
    adapter = completion.get("adapter") or {}
    note_paths = {adapter.get("source_note"), adapter.get("application_note"), *(adapter.get("knowledge_points") or [])} - {None, ""}
    protected = {
        str(entry.get("path") or ""): str(entry.get("sha256") or "")
        for entry in prior.get("protected_inputs", []) if isinstance(entry, dict) and entry.get("path") and entry.get("sha256")
    }
    for path, expected in originals.items():
        if (path not in changes and path not in note_paths and not path.endswith(".html") and "/assets/" not in path
                and path not in {completion["knowledge_package_path"], completion["source_slice_path"]}):
            protected.setdefault(path, expected)
    # Older ordinary completion receipts did not always list package inputs in
    # artifact_manifest.  Their presence still makes them protected accepted
    # inputs, exactly as it does for a content revision.
    for name in ("input_bundle.json", "canonical_source.md", "source_capture.md"):
        path = package / name
        if path.is_file():
            protected.setdefault(path.relative_to(root).as_posix(), sha(path.read_bytes()))
    for path, expected in ((asset_path, image_sha256), (evidence_path, evidence_sha256)):
        protected[path] = expected
    pointer = package / "completion_revision_current.json"
    record = {
        "schema_version": SCHEMA,
        "revision_kind": SOURCE_ATTACHMENT_REVISION_KIND,
        "completion_path": (package / "completion_receipt.json").relative_to(root).as_posix(),
        "completion_payload_sha256": completion["payload_sha256"],
        "source_id": completion["source_id"],
        "source_identity": completion["source_identity"],
        "authorization": authorization,
        "review_summary": review_summary,
        "previous_pointer_sha256": sha(pointer.read_bytes()) if pointer.exists() else "",
        "protected_inputs": [{"path": path, "sha256": value} for path, value in sorted(protected.items())],
        "changes": list(changes.values()),
        "source_attachments": [*attachments, item],
    }
    # Keep the predecessor's evidence bindings.  A source attachment does not
    # re-open or weaken an already accepted content/historical correction.
    for key in ("current_source_review", "legacy_receipt", "projection_evidence", "retirement_candidates"):
        if key in prior:
            record[key] = copy.deepcopy(prior[key])
    record["payload_sha256"] = digest(record)
    return record


def revoke_source_attachment_revision(root, package, record_path, pointer_before):
    """Undo only this transaction's still-current attachment activation."""
    root, package = Path(root).resolve(), Path(package).resolve()
    pointer = inside(root, (package / "completion_revision_current.json").relative_to(root).as_posix())
    record_path = inside(root, record_path)
    if not record_path.resolve().is_relative_to((package / "completion_revisions").resolve()):
        raise ValueError("source attachment revision is outside its package")
    if not pointer.is_file():
        raise ValueError("source attachment revision pointer is missing during rollback")
    active = json.loads(pointer.read_text(encoding="utf-8"))
    if active.get("revision_path") != record_path.relative_to(root).as_posix():
        raise ValueError("source attachment revision is no longer current during rollback")
    if pointer_before is None:
        pointer.unlink()
    else:
        temporary = pointer.with_suffix(".rollback.tmp")
        with temporary.open("xb") as stream:
            stream.write(pointer_before)
        os.replace(temporary, pointer)
    if record_path.is_file():
        record_path.unlink()
    folder = record_path.parent
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


def write_source_attachment_note(root, source_note_path, after_bytes):
    """Atomically apply the source-note bytes preflighted for one attachment.

    This remains in the completed-source revision owner: the rail supplies only
    an already-validated byte sequence and immediately registers it through
    ``accept``.  It is deliberately not a general note writer.
    """
    root = Path(root).resolve()
    source_note = inside(root, source_note_path)
    if source_note.suffix.lower() != ".md" or not source_note.is_file() or not isinstance(after_bytes, bytes):
        raise ValueError("source attachment note write is invalid")
    temporary = source_note.with_name("." + source_note.name + ".source-attachment.tmp")
    if temporary.exists():
        raise ValueError("source attachment note temporary already exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(after_bytes)
        os.replace(temporary, source_note)
    finally:
        if temporary.exists():
            temporary.unlink()


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


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _save_json(path, value):
    value['payload_sha256'] = digest(value)
    temporary = path.with_name('.' + path.name + '.tmp')
    data = _json_bytes(value)
    if temporary.exists():
        if temporary.read_bytes() != data: raise ValueError('unowned transaction temporary file')
    else:
        with temporary.open('xb') as stream: stream.write(data)
    os.replace(temporary, path)


def _modules():
    try:
        from . import compiler_worker_v2 as worker, obsidian_vault_adapter as adapter
        from .ingest_plan_contract import build_kp
    except ImportError:
        import compiler_worker_v2 as worker, obsidian_vault_adapter as adapter
        from ingest_plan_contract import build_kp
    return worker, adapter, build_kp


PRIMARY_WORK_FIELDS = frozenset({
    'primary_work_status',
    'primary_work_title',
    'primary_work_evidence',
})
CONTENT_REVISION_SEMANTIC_FIELDS = (
    'analysis',
    'knowledge',
    'relations',
    'prior_knowledge',
    'application',
    'coverage_units',
)
PRIMARY_WORK_TABLE_PREFIXES = (
    '| 主要作品判定 |',
    '| 主要作品標題 |',
    '| 主要作品證據 |',
)
_CREATED_LINE = re.compile(r'^created:\s*(?P<value>"[^"\r\n]*"|[^\r\n]+)\s*$')
_UPDATED_LINE = re.compile(r'^updated:\s*"?(?:\d{4}-\d{2}-\d{2})"?\s*$')


def _changed_keys(before, after):
    marker = object()
    return {
        key for key in set(before) | set(after)
        if before.get(key, marker) != after.get(key, marker)
    }


def _is_metadata_only_primary_work_amendment(before_kp, candidate):
    """Whether a completed-source revision can safely skip unrelated projections."""
    if any(before_kp.get(key) != candidate.get(key) for key in CONTENT_REVISION_SEMANTIC_FIELDS):
        return False
    if any(
        before_kp.get(key) != candidate.get(key)
        for key in set(before_kp) | set(candidate)
        if key != 'source'
    ):
        return False
    before_source = before_kp.get('source')
    candidate_source = candidate.get('source')
    if not isinstance(before_source, dict) or not isinstance(candidate_source, dict):
        return False
    changed = _changed_keys(before_source, candidate_source)
    return bool(changed) and changed <= PRIMARY_WORK_FIELDS


def _without_primary_work_rows(lines):
    return [
        line for line in lines
        if not line.startswith(PRIMARY_WORK_TABLE_PREFIXES)
    ]


def _primary_work_line_indexes(lines, *, artifact):
    """Return one exact row per permitted primary-work mapping, or fail closed."""
    indexes = {}
    for index, line in enumerate(lines):
        if not line.startswith('| 主要作品'):
            continue
        prefix = next((value for value in PRIMARY_WORK_TABLE_PREFIXES if line.startswith(value)), None)
        if prefix is None:
            raise ValueError('metadata-only primary revision has an unknown primary-work row in ' + artifact)
        if prefix in indexes:
            raise ValueError('metadata-only primary revision has duplicate primary-work rows in ' + artifact)
        indexes[prefix] = index
    if PRIMARY_WORK_TABLE_PREFIXES[0] not in indexes:
        raise ValueError('metadata-only primary revision lacks primary-work status in ' + artifact)
    return indexes


def _normalized_artifact_lines(data):
    """Decode a text artifact while retaining its newline/BOM contract for output."""
    has_bom = data.startswith(b'\xef\xbb\xbf')
    text = data.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    normalized = text.replace('\r\n', '\n')
    return has_bom, newline, normalized, normalized.splitlines()


def _render_preserving_format(*, has_bom, newline, candidate_text, candidate_lines):
    return ((b'\xef\xbb\xbf' if has_bom else b'')
            + (newline.join(candidate_lines) + (newline if candidate_text.endswith('\n') else '')).encode('utf-8'))


def _source_body_with_attachment_paths(body, paths):
    """Keep accepted source-attachment paths when rendering a content revision."""
    if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
        raise ValueError('accepted source attachment paths are invalid')
    lines = body.replace('\r\n', '\n').splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('content revision source note lacks frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise ValueError('content revision source note has unterminated frontmatter') from exc
    start = next((index for index in range(1, end) if lines[index].split(':', 1)[0].strip() == 'source_attachments'
                  and ':' in lines[index]), None)
    rendered = ['source_attachments:'] + ['  - ' + json.dumps(path, ensure_ascii=False) for path in paths]
    if start is None:
        lines[end:end] = rendered
    else:
        finish = start + 1
        while finish < end and (lines[finish].startswith(' ') or not lines[finish].strip()):
            finish += 1
        lines[start:finish] = rendered
    return '\n'.join(lines) + ('\n' if body.endswith(('\n', '\r\n')) else '')


def _metadata_only_source_slice_bytes(before, candidate):
    """Fail closed unless the rendered slice changes only primary-work rows."""
    has_bom, newline, before_text, before_lines = _normalized_artifact_lines(before)
    candidate_text = candidate.replace('\r\n', '\n')
    candidate_lines = candidate_text.splitlines()
    before_rows = _primary_work_line_indexes(before_lines, artifact='source slice')
    candidate_rows = _primary_work_line_indexes(candidate_lines, artifact='source slice')
    if set(before_rows) - set(PRIMARY_WORK_TABLE_PREFIXES) or set(candidate_rows) - set(PRIMARY_WORK_TABLE_PREFIXES):
        raise ValueError('metadata-only primary revision has invalid source slice mappings')
    if _without_primary_work_rows(before_lines) != _without_primary_work_rows(candidate_lines):
        raise ValueError('metadata-only primary revision would change source slice content')
    return _render_preserving_format(has_bom=has_bom, newline=newline,
                                     candidate_text=candidate_text, candidate_lines=candidate_lines)


def _metadata_only_source_note_bytes(before, candidate):
    """Retain existing source-note creation/manual content for a metadata-only amendment."""
    has_bom, newline, before_text, before_lines = _normalized_artifact_lines(before)
    candidate_text = candidate.replace('\r\n', '\n')
    candidate_lines = candidate_text.splitlines()
    if not before_lines or before_lines[0] != '---' or not candidate_lines or candidate_lines[0] != '---':
        raise ValueError('metadata-only primary revision lacks source-note frontmatter')
    try:
        before_end = before_lines.index('---', 1)
        candidate_end = candidate_lines.index('---', 1)
    except ValueError as error:
        raise ValueError('metadata-only primary revision has unterminated source-note frontmatter') from error
    before_created = [index for index in range(1, before_end) if _CREATED_LINE.fullmatch(before_lines[index])]
    before_updated = [index for index in range(1, before_end) if _UPDATED_LINE.fullmatch(before_lines[index])]
    candidate_created = [index for index in range(1, candidate_end) if _CREATED_LINE.fullmatch(candidate_lines[index])]
    candidate_updated = [index for index in range(1, candidate_end) if _UPDATED_LINE.fullmatch(candidate_lines[index])]
    if not (len(before_created) == len(before_updated) == len(candidate_created) == len(candidate_updated) == 1):
        raise ValueError('metadata-only primary revision cannot preserve source note timestamps')
    before_rows = _primary_work_line_indexes(before_lines, artifact='source note')
    candidate_rows = _primary_work_line_indexes(candidate_lines, artifact='source note')

    def stable(lines, *, created, updated, rows):
        return [
            line for index, line in enumerate(lines)
            if index not in {*created, *updated, *rows.values()}
        ]

    if stable(before_lines, created=before_created, updated=before_updated, rows=before_rows) != stable(
            candidate_lines, created=candidate_created, updated=candidate_updated, rows=candidate_rows):
        raise ValueError('metadata-only primary revision would overwrite source note content')
    candidate_lines[candidate_created[0]] = before_lines[before_created[0]]
    return _render_preserving_format(has_bom=has_bom, newline=newline,
                                     candidate_text=candidate_text, candidate_lines=candidate_lines)


def prepare_content_revision(root, kp_path, plan_path, *, authorization, reason):
    """Stage deterministic content only; never write live notes or re-open a job."""
    worker, adapter, build_kp = _modules()
    root = Path(root).resolve()
    kp_path = inside(root, Path(kp_path).relative_to(root).as_posix())
    package = kp_path.parent
    if len(authorization.strip()) < 6 or len(reason.strip()) < 6:
        raise ValueError('content revision needs explicit authorization and a concrete reason')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    # The projection contract is part of a candidate's identity: never replace a
    # previously rejected staging folder with a differently-scoped projection.
    request = {'semantic_plan': plan, 'authorization': authorization, 'reason': reason,
               'projection_contract_version': CONTENT_REVISION_PROJECTION_VERSION}
    request_sha256 = digest(request)
    folder = inside(root, _evidence_folder(package, 'content', request_sha256).relative_to(root).as_posix())
    journal_path = folder / 'transaction.json'
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        if journal.get('payload_sha256') != digest(journal) or journal.get('request_sha256') != request_sha256:
            raise ValueError('content revision staging conflict')
        return _prepared_result(root, journal_path, journal)
    before_kp = worker.read_json(kp_path)
    completion = worker.completed_receipt_readback(root, package, kp_path, before_kp)
    if not completion:
        raise ValueError('content revision requires a verified completed source')
    completion_path = worker.bound_completion_path(root, package, kp_path, before_kp)
    original_completion = worker.read_json(completion_path)
    prior_record = read_current(root, package, original_completion, include_record=True)
    accepted_changes = {item['original_path']: item for item in prior_record.get('changes', [])}
    inherited_attachments = copy.deepcopy(prior_record.get('source_attachments'))
    if inherited_attachments is not None and not isinstance(inherited_attachments, list):
        raise ValueError('accepted source attachment record is invalid')
    replacement = build_kp(plan, package, root=root)
    old_ids = worker.knowledge_package_point_ids(before_kp)
    new_ids = worker.knowledge_package_point_ids(replacement)
    if plan.get('schema_version') == 'ingest_semantic_plan_compact_v1' and len(old_ids) == len(new_ids):
        remap = dict(zip(new_ids, old_ids))
        for item in replacement['knowledge']: item['kp_id'] = remap[item['kp_id']]
        for relation in replacement['relations']:
            relation['source_kp'] = remap[relation['source_kp']]
            relation['target_kp'] = remap[relation['target_kp']]
        for link in replacement.get('prior_knowledge', {}).get('cross_links', []):
            link['source_kp'] = remap[link['source_kp']]
    if worker.knowledge_package_point_ids(replacement) != old_ids:
        raise ValueError('content revision must retain original KP identities and order')
    candidate = copy.deepcopy(before_kp)
    if replacement.get('source_catalog_version'):
        candidate['source_catalog_version'] = replacement['source_catalog_version']
    for key in CONTENT_REVISION_SEMANTIC_FIELDS:
        if key in replacement:
            candidate[key] = replacement[key]
        else:
            candidate.pop(key, None)  # prior_knowledge is optional in older plans.
    before_source = candidate.get('source') if isinstance(candidate.get('source'), dict) else {}
    replacement_source = replacement.get('source') if isinstance(replacement.get('source'), dict) else {}
    source_metadata = plan.get('source_metadata') if isinstance(plan.get('source_metadata'), dict) else {}
    primary_fields = {'primary_work_status', 'primary_work_title', 'primary_work_evidence'}
    changed_non_primary = [
        key for key in source_metadata if key not in primary_fields
        and replacement_source.get(key) != before_source.get(key)
    ]
    if changed_non_primary:
        raise ValueError('content revision cannot change source metadata: ' + ', '.join(sorted(changed_non_primary)))
    requested_primary = primary_fields & set(source_metadata)
    if requested_primary:
        if 'primary_work_status' not in requested_primary:
            raise ValueError('content revision primary-work amendment requires primary_work_status')
        status = str(replacement_source.get('primary_work_status') or '').strip()
        if status == 'primary':
            if requested_primary != primary_fields:
                raise ValueError('content revision primary-work amendment requires title and canonical relationship evidence')
            for key in primary_fields:
                before_source[key] = replacement_source.get(key)
        elif status == 'none':
            before_source['primary_work_status'] = 'none'
            before_source.pop('primary_work_title', None)
            before_source.pop('primary_work_evidence', None)
        else:
            raise ValueError('content revision primary-work amendment has unsupported status')
    # Source identity, accepted original evidence, picture/case branches, job
    # binding and timing remain immutable through this entry.
    canonical = (package / 'canonical_source.md').read_text(encoding='utf-8').rstrip('\n')
    for error in worker.validate_primary_work_metadata(before_source, canonical):
        raise ValueError('content revision primary-work metadata is invalid: ' + error)
    if candidate == before_kp:
        raise ValueError('content revision has no accepted content change')
    errors = worker.validate_kp(candidate, package)
    if errors: raise ValueError('content revision preflight failed: ' + '; '.join(errors))
    slice_path = inside(root, original_completion['source_slice_path'])
    slice_text = worker.render_source_slice(candidate, package).rstrip() + '\n'
    vault = worker.vault_path(root)
    data = adapter.parse_slice(slice_path, max_kp=len(old_ids), markdown=slice_text)
    projection = adapter.build_plan(root, vault, slice_path, data)
    projection['content_revision'] = True
    projection['source_body'] = adapter.preserve_existing_created(projection['source_note'], projection['source_body'])
    if inherited_attachments:
        live_paths = adapter.frontmatter_fields(projection['source_note']).get('source_attachments')
        projection['source_body'] = _source_body_with_attachment_paths(projection['source_body'], live_paths)
    projection['application_body'] = adapter.preserve_existing_created(projection['application_note'], projection['application_body'])
    projection['knowledge_point_bodies'] = [
        adapter.preserve_existing_created(path, body)
        for path, body in zip(projection['knowledge_point_notes'], projection['knowledge_point_bodies'])
    ]
    preflight = adapter.preflight_report(projection)
    if preflight.get('status') != 'passed':
        raise ValueError('content revision writer preflight failed: ' + json.dumps(preflight.get('issues'), ensure_ascii=False))
    bound_adapter = original_completion['adapter']
    if (projection['source_note'].relative_to(root).as_posix() != bound_adapter['source_note']
            or projection['application_note'].relative_to(root).as_posix() != bound_adapter['application_note']
            or [p.relative_to(root).as_posix() for p in projection['knowledge_point_notes']] != bound_adapter['knowledge_points']):
        raise ValueError('content revision cannot relocate original notes')
    metadata_only_primary_work = _is_metadata_only_primary_work_amendment(before_kp, candidate)
    if metadata_only_primary_work:
        files = {
            kp_path: _json_bytes(candidate),
            slice_path: _metadata_only_source_slice_bytes(slice_path.read_bytes(), slice_text),
            projection['source_note']: _metadata_only_source_note_bytes(
                projection['source_note'].read_bytes(), str(projection['source_body']),
            ),
        }
        retirement = []
    else:
        files = {kp_path: _json_bytes(candidate), slice_path: slice_text.encode('utf-8'),
                 projection['source_note']: projection['source_body'].encode('utf-8'),
                 projection['application_note']: projection['application_body'].encode('utf-8')}
        files.update({p: body.encode('utf-8') for p, body in zip(projection['knowledge_point_notes'], projection['knowledge_point_bodies'])})
        registry_renders = {}
        for name, renderer in (('registry_file', adapter.write_source_registry),
                               ('knowledge_point_registry_file', adapter.write_knowledge_point_registry),
                               ('relation_registry_file', adapter.write_relation_registry)):
            registry_renders[name] = renderer(projection, render_only=True)
        candidate_facts = adapter.concept_projection_facts_from_rows(
            _rendered_registry_rows(registry_renders['knowledge_point_registry_file'], 'kp_uid'),
            _rendered_registry_rows(registry_renders['relation_registry_file'], 'relation_id'),
        ) or {}
        fields = {p: adapter.frontmatter_fields_from_text(body) for p, body in zip(projection['knowledge_point_notes'], projection['knowledge_point_bodies'])}
        candidate_kp_concepts = {}
        for field in fields.values():
            concept = adapter.reusable_concept_decision(field)
            kp_uid = str(field.get('kp_uid') or '')
            if kp_uid and concept:
                candidate_kp_concepts[kp_uid] = (adapter.reusable_concept_note_path(vault, concept), concept)
        concepts = adapter.write_concept_notes(
            vault, source_ids={data.source_id}, candidate_fields=fields, render_only=True, facts=candidate_facts,
        )
        files.update(concepts)
        for name, rendered in registry_renders.items():
            files[projection[name]] = rendered
        retirement = adapter.scoped_concept_revision(
            vault, data.source_id, set(old_ids), concepts, facts=candidate_facts,
            candidate_kp_concepts=candidate_kp_concepts,
        )
        for item in retirement: item['path'] = item['path'].relative_to(root).as_posix()
        files.update(concepts)
        graph_filter = adapter.reviewed_active_concept_filter(vault)
        if graph_filter is not None:
            files[vault/'.obsidian'/'graph.json'] = graph_filter
    staged_paths = []
    for index, (path, after) in enumerate(files.items()):
        before = path.read_bytes() if path.exists() else b''
        if before != after:
            staged_paths.extend(_staged_evidence_paths(folder, index, path))
    _assert_evidence_path_budget(folder, staged_paths)
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder/'request.json'
    request_path.write_bytes(_json_bytes(request))
    items = []
    for index, (path, after) in enumerate(files.items()):
        before = path.read_bytes() if path.exists() else b''
        if before == after: continue
        staged, snapshot = _staged_evidence_paths(folder, index, path)
        staged.write_bytes(after)
        snapshot.write_bytes(before)
        items.append({'path': path.relative_to(root).as_posix(),
                      'candidate_path': staged.relative_to(root).as_posix(),
                      'before_path': snapshot.relative_to(root).as_posix(),
                      'before_sha256': sha(before), 'after_sha256': sha(after),
                      'owner': 'compiler' if path in {kp_path, slice_path} else 'vault_adapter'})
    protected = []
    originals = original_files(original_completion)
    change_paths = {item['path'] for item in items}
    note_paths = {bound_adapter['source_note'], bound_adapter['application_note'], *bound_adapter['knowledge_points']}
    for path, expected in originals.items():
        if path in change_paths or path in note_paths or path.endswith('.html'): continue
        current = inside(root, path)
        protected.append({'path': path, 'sha256': expected})
    # Bind every package input, including evidence not present in old manifests.
    for name in ('input_bundle.json', 'canonical_source.md', 'source_capture.md', 'source_capture_metadata.json',
                 'image_analysis.json', 'imagegen_attempts.json'):
        path = package/name
        if path.is_file() and path.relative_to(root).as_posix() not in {item['path'] for item in protected}:
            protected.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path.read_bytes())})
    pointer = package/'completion_revision_current.json'
    journal = {'schema_version': 'completed_content_revision_transaction_v1', 'state': 'prepared',
               'request_sha256': request_sha256, 'request_path': request_path.relative_to(root).as_posix(),
               'source_id': data.source_id, 'source_identity': original_completion['source_identity'],
               'kp_path': kp_path.relative_to(root).as_posix(), 'slice_path': slice_path.relative_to(root).as_posix(),
               'completion_path': completion_path.relative_to(root).as_posix(),
               'completion_sha256': sha(completion_path.read_bytes()),
               'completion_payload_sha256': original_completion['payload_sha256'],
               'job_path': worker.job_file(root, before_kp['job_id']).relative_to(root).as_posix(),
               'job_sha256': sha(worker.job_file(root, before_kp['job_id']).read_bytes()),
               'previous_pointer_sha256': sha(pointer.read_bytes()) if pointer.exists() else '',
               'authorization': authorization, 'reason': reason, 'items': items,
               'projection_scope': 'metadata_only_primary_work' if metadata_only_primary_work else 'content_revision',
                'protected_inputs': protected, 'prior_changes': accepted_changes,
                'source_attachments': inherited_attachments,
               'retirement_candidates': retirement}
    _save_json(journal_path, journal)
    return _prepared_result(root, journal_path, journal)


def _prepared_result(root, path, journal):
    result = _prepared_result_base(root, path, journal)
    if journal.get('recovery_differences'):  # an infographic recovery: what the live files hold against what is restored, for the reviewer
        result['review_template']['recovery_differences'] = journal['recovery_differences']
    return result


def _prepared_result_base(root, path, journal):
    return {'status': 'review_required' if journal['state'] == 'prepared' else journal['state'],
            'revision_file': path.relative_to(root).as_posix(), 'write_performed': False,
            'formal_artifacts_unchanged': journal['state'] == 'prepared', 'changes': journal['items'],
            'retirement_candidates': journal['retirement_candidates'],
            'review_template': {'schema_version': 'current_source_review_v1',
                'scope': 'current_content_not_historical_reconstruction',
                'source_identity': journal['source_identity'], 'status': 'pending', 'reviewer': '',
                'historical_limitations': 'Original completion and execution timing remain historical; this review accepts current content only.',
                'unresolved_current_findings': [],
                'reviewed_files': [{'path': path, 'sha256': value} for path,value in {
                    **{item['current_path']: item['sha256'] for item in journal['prior_changes'].values()},
                    **{item['path']: item['after_sha256'] for item in journal['items']}}.items()]}}


def _verify_prepare_predecessor(root, package, completion, journal):
    """Bind a pending transaction to its accepted predecessor without blocking recovery.

    A crash may leave a reviewed transaction's own target at either its staged
    before or after hash while the predecessor pointer is still current.  Those
    targets are bounded by the immutable staging evidence; every other receipt
    artifact must still match the accepted predecessor (or its receipt hash).
    """
    worker, _, _ = _modules()
    allowed = {
        item['path']: {item['before_sha256'], item['after_sha256']}
        for item in journal['items']
    }
    record = read_current(root, package, completion, include_record=True, verify=False)
    if record and record.get('payload_sha256') != digest(record):
        raise ValueError('accepted content revision receipt changed')
    current_changes = validate(root, record, completion, allowed_current_hashes=allowed) if record else {}
    if current_changes != journal['prior_changes']:
        raise ValueError('accepted content revision changed since prepare')
    for original_path, original_sha256 in original_files(completion).items():
        if original_path in allowed:
            continue
        prior = current_changes.get(original_path)
        current_path = prior['current_path'] if prior else original_path
        expected = prior['sha256'] if prior else original_sha256
        if original_path.endswith('.html'):
            # The outcome projection has its own receipt-bound revision chain;
            # a content revision must accept that current derived HTML, never
            # force it back to the completion receipt's historical bytes.
            expected = worker.outcome_revision_hash(root, package, completion, original_path) or expected
        if not artifact_matches(inside(root, current_path), expected):
            raise ValueError('unamended completed artifact changed since prepare: ' + current_path)


def _content_revision_record(root, package, completion, journal, review_file, review_bytes):
    """The receipt shape used for both staged validation and activation."""
    originals = original_files(completion)
    evidence = [{'path': review_file.relative_to(root).as_posix(), 'sha256': sha(review_bytes),
                 'supports': journal['reason']}]
    changes = copy.deepcopy(journal['prior_changes'])
    for item in journal['items']:
        if item['path'] not in originals:
            continue
        changes[item['path']] = {
            'original_path': item['path'], 'original_sha256': originals[item['path']],
            'current_path': item['path'], 'sha256': item['after_sha256'],
            'reason': journal['reason'], 'evidence': evidence,
        }
    projections = [item for item in journal['items'] if item['owner'] == 'vault_adapter']
    record = {
        'schema_version': SCHEMA, 'completion_path': journal['completion_path'],
        'completion_payload_sha256': completion['payload_sha256'], 'source_id': journal['source_id'],
        'source_identity': journal['source_identity'], 'authorization': journal['authorization'],
        'review_summary': journal['reason'], 'previous_pointer_sha256': journal['previous_pointer_sha256'],
        'protected_inputs': journal['protected_inputs'], 'changes': list(changes.values()),
        'current_source_review': {'path': review_file.relative_to(root).as_posix(), 'sha256': sha(review_bytes)},
        'projection_evidence': [{'path': item['path'], 'candidate_path': item['candidate_path'],
                                 'before_sha256': item['before_sha256'], 'after_sha256': item['after_sha256']}
                                for item in projections],
        'retirement_candidates': journal['retirement_candidates'],
    }
    if journal.get('source_attachments'):
        record['source_attachments'] = copy.deepcopy(journal['source_attachments'])
    record['payload_sha256'] = digest(record)
    return record


def apply_content_revision(root, journal_path, review_path, *, dry_run=False):
    """Resume this same transaction; activation reuses the existing revision ledger."""
    worker, adapter, _ = _modules()
    root = Path(root).resolve()
    journal_path = inside(root, Path(journal_path).relative_to(root).as_posix())
    journal = worker.read_json(journal_path)
    if journal.get('schema_version') != 'completed_content_revision_transaction_v1' or journal.get('payload_sha256') != digest(journal):
        raise ValueError('invalid content revision transaction')
    review = json.loads(Path(review_path).read_text(encoding='utf-8-sig'))
    if (review.get('schema_version') != 'current_source_review_v1' or review.get('payload_sha256') != digest(review)
            or review.get('scope') != 'current_content_not_historical_reconstruction'
            or review.get('source_identity') != journal['source_identity'] or review.get('status') != 'accepted'
            or not review.get('reviewer') or not review.get('historical_limitations') or review.get('unresolved_current_findings') != []):
        raise ValueError('content revision requires independent current-source review')
    reviewed = {item['path']: item['sha256'] for item in review['reviewed_files']}
    for item in journal['items']:
        if reviewed.get(item['path']) != item['after_sha256']:
            raise ValueError('review does not cover every content projection')
        for field, hash_field in (('candidate_path', 'after_sha256'), ('before_path', 'before_sha256')):
            if sha(inside(root, item[field]).read_bytes()) != item[hash_field]:
                raise ValueError('content revision staged evidence changed')
        target = inside(root, item['path'])
        observed = sha(target.read_bytes() if target.exists() else b'')
        if observed not in {item['before_sha256'], item['after_sha256']}:
            raise ValueError('content revision target changed since prepare')
    completion_path = inside(root, journal['completion_path'])
    if sha(completion_path.read_bytes()) != journal['completion_sha256'] or sha(inside(root,journal['job_path']).read_bytes()) != journal['job_sha256']:
        raise ValueError('content revision changed original completion or job/timing')
    for item in journal['protected_inputs']:
        if not artifact_matches(inside(root,item['path']), item['sha256']):
            raise ValueError('content revision protected input changed')
    package = inside(root,journal['kp_path']).parent
    pointer = package/'completion_revision_current.json'
    if journal['state'] == 'completed':
        worker.completed_receipt_readback(root,package,inside(root,journal['kp_path']),worker.read_json(inside(root,journal['kp_path'])))
        if journal.get('projection_scope') == RECOVERY_SCOPE:
            child_kp_path = inside(root, journal['child_revision']['kp_path'])
            worker.completed_receipt_readback(root, child_kp_path.parent, child_kp_path, worker.read_json(child_kp_path))
        completion = worker.read_json(completion_path)
        candidates_written = False if dry_run else _ensure_content_revision_candidates(root,package,completion,journal_path,journal)
        if candidates_written: _save_json(journal_path,journal)
        return {'status': 'completed', 'revision_file': journal_path.relative_to(root).as_posix(), 'write_performed': candidates_written,
                'idea_candidates': journal.get('idea_candidates'),
                'original_completion_preserved': True, 'original_job_and_timing_preserved': True}
    if journal.get('projection_scope') == RECOVERY_SCOPE:
        return _apply_infographic_recovery(root, journal_path, journal, review, dry_run=dry_run)
    pointer_hash = sha(pointer.read_bytes()) if pointer.exists() else ''
    current_pointer = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
    own_activation = bool(journal.get('record_path')
                           and current_pointer.get('revision_path') == journal['record_path'])
    if pointer_hash != journal['previous_pointer_sha256'] and not own_activation:
        raise ValueError('stale content revision pointer')
    # Before the first write, prove every accepted predecessor still has the
    # exact bytes we incorporated.  If activation already happened when an
    # interruption occurred, validate the new current record instead so a
    # legitimate resume stays recoverable.
    completion = worker.read_json(completion_path)
    if own_activation:
        read_current(root, package, completion, include_record=True)
    else:
        _verify_prepare_predecessor(root, package, completion, journal)
    review_file = _review_file_path(journal_path.parent, review)
    if review_file.exists():
        if json.loads(review_file.read_text(encoding='utf-8')) != review:
            raise ValueError('immutable content review conflict')
        review_bytes = review_file.read_bytes()
    else:
        review_bytes = _json_bytes(review)
    if journal.get('review_sha256') and journal['review_sha256'] != sha(review_bytes):
        raise ValueError('recovery requires the original reviewed candidate')
    record = _content_revision_record(root, package, completion, journal, review_file, review_bytes)
    virtual = {item['path']: inside(root, item['candidate_path']).read_bytes() for item in journal['items']}
    virtual[review_file.relative_to(root).as_posix()] = review_bytes
    validate(root, record, completion, virtual=virtual)
    if dry_run:
        return {'status': 'reviewed_ready', 'write_performed': False, 'formal_artifacts_unchanged': True,
                'revision_file': journal_path.relative_to(root).as_posix(), 'changes': journal['items']}
    if not review_file.exists(): review_file.write_bytes(review_bytes)
    if journal.get('review_sha256') and journal['review_sha256'] != sha(review_file.read_bytes()):
        raise ValueError('recovery requires the original reviewed candidate')
    journal.update(review_sha256=sha(review_file.read_bytes()), state='applying')
    _save_json(journal_path,journal)
    projections = [item for item in journal['items'] if item['owner'] == 'vault_adapter']
    adapter.apply_reviewed_content_projection(root,worker.vault_path(root),projections,source_id=journal['source_id'])
    for item in journal['items']:
        if item['owner'] != 'compiler': continue
        target = inside(root,item['path'])
        candidate = inside(root,item['candidate_path']).read_bytes()
        if sha(target.read_bytes()) == item['after_sha256']: continue
        if sha(target.read_bytes()) != item['before_sha256']: raise ValueError('compiler projection changed during revision')
        temporary = target.with_name('.'+target.name+'.content-revision.tmp')
        if temporary.exists():
            if temporary.read_bytes() != candidate: raise ValueError('unowned compiler temporary file')
        else:
            with temporary.open('xb') as stream: stream.write(candidate)
        os.replace(temporary,target)
    kp_path = inside(root,journal['kp_path'])
    kp = worker.read_json(kp_path)
    completion = worker.read_json(completion_path)
    data = adapter.parse_slice(inside(root,journal['slice_path']),max_kp=len(kp['knowledge']))
    live_plan = adapter.build_plan(root,worker.vault_path(root),inside(root,journal['slice_path']),data)
    verification = adapter.verify_plan(live_plan,source_local=True)
    if not verification.get('passed'):
        failed = sorted(key for key, value in verification.items() if value is False)
        raise ValueError('content revision writer readback failed: ' + ', '.join(failed))
    journal['record_path'] = (package/'completion_revisions'/(record['payload_sha256']+'.json')).relative_to(root).as_posix()
    _save_json(journal_path,journal)
    if (sha(pointer.read_bytes()) if pointer.exists() else '') == journal['previous_pointer_sha256']:
        accept(root,package,completion,record)
    else:
        read_current(root,package,completion)
    journal['state'] = 'activated'
    _save_json(journal_path,journal)
    # Reuse the existing receipt-bound HTML projection revision owner.
    worker.refresh_completed_outcome(root,kp_path)
    worker.completed_receipt_readback(root,package,kp_path,kp)
    _ensure_content_revision_candidates(root,package,completion,journal_path,journal)
    journal['state'] = 'completed'
    _save_json(journal_path,journal)
    return {'status': 'completed', 'revision_file': journal_path.relative_to(root).as_posix(),
            'write_performed': True, 'original_completion_preserved': True,
            'idea_candidates': journal.get('idea_candidates'),
            'original_job_and_timing_preserved': True, 'retirement_candidates': journal['retirement_candidates']}


# ---------------------------------------------------------------------------
# Exact recovery after an infographic child that the old full writer produced.
#
# A knowledge-only infographic child of a completed source must only overlay its pictures (compiler_worker_v2 overlay journal).  The
# old full writer instead re-rendered the notes, the concept pages and the registries from the child's slice, so the accepted text of the
# parent fell back to older bytes and neither receipt read back.  This section is the one reviewed transaction that puts the accepted
# text back and adds the pictures on top of it:
#
#   prepare_infographic_recovery   pure derivation + staging (writes only its own transaction folder)
#   apply_content_revision         the existing entry; a transaction with ``projection_scope == infographic_recovery`` is handled by
#                                  _apply_infographic_recovery (bytes through the unique CAS writer, then the parent record, then
#                                  the child record, then the child's reader page)
#
# Nothing here invents bytes: every target is the accepted bytes found in immutable evidence (a hash match), the picture overlay of
# those bytes (render_infographic_overlay, body unchanged, only the child's own PNGs referenced), or a registry row set copied from a
# staged before snapshot.  A rejected transaction is evidence only through its before snapshots; it is never applied.
# ---------------------------------------------------------------------------

RECOVERY_SCOPE = 'infographic_recovery'
RECOVERY_PROJECTION_VERSION = 'infographic_recovery_projection_v1'
_RECOVERY_BEFORE_NAME = re.compile(r'\d{3}-before(?:-.+|\..+)?')
_RECOVERY_CANDIDATE_NAME = re.compile(r'\d{3}-(?:after(?:\..+)?|(?!before(?:-|\.|$)).+)')
_RECOVERY_REGISTRIES = ('sources', 'knowledge_points', 'relations')


def _recovery_relative(root, value):
    path = Path(value)
    if path.is_absolute():
        path = path.resolve().relative_to(Path(root).resolve())
    return path.as_posix()


def _recovery_parent(worker, root, child_kp_path):
    """(child package, child kp, child completion path/value, parent package, parent kp path/kp, parent completion path/value)."""
    root = Path(root).resolve()
    child_kp_path = inside(root, _recovery_relative(root, child_kp_path))
    child_package = child_kp_path.parent
    child_kp = worker.read_json(child_kp_path)
    if not worker.is_completed_source_infographic_revision(child_kp):
        raise ValueError('infographic recovery needs a knowledge-only infographic revision')
    child_completion_path = worker.bound_completion_path(root, child_package, child_kp_path, child_kp)
    if not child_completion_path.is_file():
        raise ValueError('infographic recovery needs a completed child')
    child_completion = worker.read_json(child_completion_path)
    origin_package = worker.infographic_origin_package(root, child_package, child_kp)
    origin_kp_path = origin_package / 'knowledge_package.json'
    origin_kp = worker.read_json(origin_kp_path)
    parent_path = worker.bound_completion_path(root, origin_package, origin_kp_path, origin_kp)
    parent = worker.read_json(parent_path)
    return {'root': root, 'child_kp_path': child_kp_path, 'child_package': child_package, 'child_kp': child_kp,
            'child_completion_path': child_completion_path, 'child_completion': child_completion,
            'package': origin_package, 'kp_path': origin_kp_path, 'kp': origin_kp,
            'completion_path': parent_path, 'completion': parent}


def _recovery_check_receipts(worker, ctx):
    root, child, parent = ctx['root'], ctx['child_completion'], ctx['completion']
    child_kp = ctx['child_kp']
    source = child_kp.get('source') or {}
    child_rel = ctx['child_completion_path'].relative_to(root).as_posix()
    parent_rel = ctx['completion_path'].relative_to(root).as_posix()
    revision = child_kp.get('revision') or {}
    for receipt in (child, parent):
        if (receipt.get('schema_version') != worker.COMPLETION_SCHEMA_VERSION or receipt.get('status') != 'completed'
                or receipt.get('payload_sha256') != worker.canonical_payload_sha256(receipt)
                or receipt.get('source_id') != source.get('source_id') or receipt.get('source_identity') != source.get('source_identity')):
            raise ValueError('a completion receipt of infographic recovery is not valid')
    if child.get('job_id') != child_kp.get('job_id') or parent.get('job_id') != ctx['kp'].get('job_id'):
        raise ValueError('a completion receipt of infographic recovery belongs to another job')
    if isinstance(child.get('completed_source_infographic_overlay'), dict):
        raise ValueError('the child was written by the overlay owner; there is nothing to recover')
    if (revision.get('supersedes_completion_path') != parent_rel
            or revision.get('supersedes_completion_payload_sha256') != parent.get('payload_sha256')):
        raise ValueError('the child does not supersede this parent receipt')
    job = worker.read_json(worker.job_file(root, str(child_kp.get('job_id') or '')))
    if job.get('status') != 'completed' or job.get('completion_receipt_path') != child_rel:
        raise ValueError('the child job is not completed on its receipt')
    pointer = worker.read_current_infographic_pointer(root, ctx['child_package'], child_kp)
    if (not pointer or pointer.get('current_completion_path') != child_rel
            or pointer.get('supersedes_completion_path') != parent_rel
            or pointer.get('supersedes_completion_payload_sha256') != parent.get('payload_sha256')):
        raise ValueError('the current infographic pointer does not select this child')
    return pointer


def _recovery_evidence(root, ctx, evidence_transactions, accepted_folder):
    """Immutable evidence of the same package: {'files': [{path, sha256}], 'pool': {sha: bytes}, 'before': {path: {sha: snapshot}}}.

    Every transaction contributes only its before snapshots; the one exception is the transaction folder of the parent's own current
    revision (``accepted_folder``), whose staged candidates are bytes that revision accepted.  A rejected transaction's candidates
    are never evidence."""
    package, parent = ctx['package'], ctx['completion']
    kp_rel = ctx['kp_path'].relative_to(root).as_posix()
    parent_rel = ctx['completion_path'].relative_to(root).as_posix()
    evidence_root = inside(root, (package / 'completion_revision_evidence').relative_to(root).as_posix())
    if not evidence_transactions:
        raise ValueError('infographic recovery needs at least one evidence transaction')
    files, pool, before_by_path, seen = [], {}, {}, set()
    for value in evidence_transactions:
        relative = _recovery_relative(root, value)
        path = inside(root, relative)
        if path.name != 'transaction.json' or path.parent.parent != evidence_root or not path.is_file():
            raise ValueError('recovery evidence must be a transaction of the same package: ' + relative)
        if relative in seen:
            continue
        seen.add(relative)
        transaction = json.loads(path.read_text(encoding='utf-8'))
        if (transaction.get('schema_version') != 'completed_content_revision_transaction_v1'
                or transaction.get('payload_sha256') != digest(transaction)
                or transaction.get('source_id') != parent['source_id'] or transaction.get('source_identity') != parent['source_identity']
                or transaction.get('kp_path') != kp_rel or transaction.get('completion_path') != parent_rel
                or transaction.get('completion_payload_sha256') != parent['payload_sha256']):
            raise ValueError('recovery evidence is not a transaction of this completed source: ' + relative)
        folder = path.parent
        for item in transaction.get('items', []):
            before_path = inside(root, item['before_path'])
            candidate_path = inside(root, item['candidate_path'])
            if (before_path.parent != folder or candidate_path.parent != folder or before_path == candidate_path
                    or not _RECOVERY_BEFORE_NAME.fullmatch(before_path.name) or not _RECOVERY_CANDIDATE_NAME.fullmatch(candidate_path.name)):
                raise ValueError('recovery evidence item is not a staged before snapshot: ' + item['path'])
            if not before_path.is_file() or sha(before_path.read_bytes()) != item['before_sha256']:
                raise ValueError('recovery evidence before snapshot changed: ' + item['path'])
            pool.setdefault(item['before_sha256'], before_path.read_bytes())
            before_by_path.setdefault(item['path'], {})[item['before_sha256']] = item['before_path']
            if candidate_path.is_file():
                candidate = candidate_path.read_bytes()
                if sha(candidate) != item['after_sha256']:
                    raise ValueError('recovery evidence candidate changed: ' + item['path'])
                if folder == accepted_folder:
                    pool.setdefault(item['after_sha256'], candidate)
        files.append({'path': relative, 'sha256': sha(path.read_bytes())})
    return {'files': sorted(files, key=lambda row: row['path']), 'pool': pool, 'before': before_by_path}


_RECOVERY_ABSENT = '<absent>'


def _recovery_registry_after(live, evidence, source_id, label):
    """(bytes, row changes): live with this source's rows replaced, row for row, by the evidence rows; every other row stays live.

    A live row of this source that carries a field the evidence row lacks was changed after the evidence was taken; replacing it would
    silently drop that field, so it is refused.  Every field that does change is returned (live value, restored value) for the review."""
    def split(data):
        rows = []
        for line in data.splitlines(keepends=True):
            value = None
            if line.strip():
                parsed = json.loads(line.decode('utf-8-sig'))
                value = parsed.get('source_id') if isinstance(parsed, dict) else None
            rows.append((line, value))
        return rows

    live_rows, evidence_rows = split(live), split(evidence)
    if [line for line, owner in live_rows if owner != source_id] != [line for line, owner in evidence_rows if owner != source_id]:
        raise ValueError('the other sources of ' + label + ' differ between the evidence and the live registry')
    replacements = [line for line, owner in evidence_rows if owner == source_id]
    own_live = [line for line, owner in live_rows if owner == source_id]
    if len(replacements) != len(own_live):
        raise ValueError('the rows of this source in ' + label + ' differ in number between the evidence and the live registry')
    changes = []
    for position, (live_line, evidence_line) in enumerate(zip(own_live, replacements), start=1):
        live_row, evidence_row = json.loads(live_line.decode('utf-8-sig')), json.loads(evidence_line.decode('utf-8-sig'))
        added = sorted(set(live_row) - set(evidence_row))
        if added:
            raise ValueError(f'row {position} of this source in {label} has fields the evidence lacks ({", ".join(added)}); '
                             'restoring it would drop a later change')
        fields = [{'field': key, 'live': live_row.get(key, _RECOVERY_ABSENT), 'restored': evidence_row[key]}
                  for key in sorted(evidence_row) if live_row.get(key, _RECOVERY_ABSENT) != evidence_row[key]]
        if fields:
            changes.append({'path': label, 'row': position, 'fields': fields})
    rows = iter(replacements)
    return b''.join(next(rows) if owner == source_id else line for line, owner in live_rows), changes


_RECOVERY_UPDATED = re.compile(r'^updated:')
_RECOVERY_SOURCE_COUNT = re.compile(r'^\s*-?\s*連到此概念的不同來源[：:]\s*\d+\s*$')
_RECOVERY_LABEL = re.compile(r'[：:]\s*$')


def _recovery_concept_limits(label, live, evidence, source_id, tokens, texts):
    """The hunks (live lines, restored lines) that turn the live concept page into the evidence page, or a refusal.

    Replacing the whole page is only exact if the pages differ in this source's contribution alone: its record names and ids, its own
    claim text, the ``updated`` line, and the count of distinct sources.  Any other differing line (another source's link or prose,
    a hand-added line) would be silently reverted, so it refuses and names the line.  A label line (ending in a colon) may only
    differ inside a hunk that also holds a line of this source."""
    import difflib

    def lines(data):
        return data.decode('utf-8-sig').replace('\r\n', '\n').split('\n')

    trailing_digits = re.search(r'\d+$', source_id)
    if not trailing_digits:
        raise ValueError('the recovery source id has no trailing digits')
    own_source_id = source_id
    source_identifier = re.compile(r'(?<![A-Za-z])' + re.escape(source_id[:trailing_digits.start()]) + r'\d+(?!\d)')

    def names_other_source(line):
        return any(source_id != own_source_id for source_id in source_identifier.findall(line))

    def own(line):
        if names_other_source(line):
            return False
        return bool(_RECOVERY_UPDATED.match(line) or _RECOVERY_SOURCE_COUNT.match(line)
                    or any(token in line for token in tokens) or any(text in line for text in texts))

    live_lines, evidence_lines = lines(live), lines(evidence)
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, live_lines, evidence_lines, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        changed = [line for line in live_lines[i1:i2] + evidence_lines[j1:j2] if line.strip()]
        has_own = any(own(line) for line in changed)
        foreign = [line for line in changed if not own(line) and (names_other_source(line) or not (has_own and _RECOVERY_LABEL.search(line)))]
        if foreign:
            raise ValueError(f'the live concept page {label} differs from the evidence outside this source '
                             f'(line {foreign[0][:120]!r}); restoring it would drop a later change')
        hunks.append({'live': [line[:240] for line in live_lines[i1:i2]], 'restored': [line[:240] for line in evidence_lines[j1:j2]]})
    return hunks


def _recovery_protected(root, package, completion, skip_paths):
    adapter_value = completion.get('adapter') or {}
    note_paths = {adapter_value.get('source_note'), adapter_value.get('application_note'), *adapter_value.get('knowledge_points', [])}
    protected = []
    for path, expected in original_files(completion).items():
        if path in skip_paths or path in note_paths or path.endswith('.html'):
            continue
        protected.append({'path': path, 'sha256': expected})
    for name in ('input_bundle.json', 'canonical_source.md', 'source_capture.md', 'source_capture_metadata.json',
                 'image_analysis.json', 'imagegen_attempts.json'):
        candidate = package / name
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_file() and relative not in {row['path'] for row in protected}:
            protected.append({'path': relative, 'sha256': sha(candidate.read_bytes())})
    return protected


def _recovery_check_child_artifacts(worker, root, package, completion, skip_paths):
    """Every receipt artifact that is not being rewritten still has its receipt bytes (the reader page may sit on its own chain)."""
    for item in completion.get('artifact_manifest') or []:
        path = item['path']
        if path in skip_paths:
            continue
        expected = {item['sha256']}
        chained = worker.outcome_revision_hash(root, package, completion, path)
        if chained:
            expected.add(chained)
        target = inside(root, path)
        if not target.is_file() or not any(artifact_matches(target, value) for value in expected):
            raise ValueError('a child receipt artifact differs and is not restored by the evidence: ' + path)


def _derive_infographic_recovery(root, child_kp_path, evidence_transactions):
    """Everything a recovery transaction contains, computed from the files as they are; reads only."""
    worker, adapter, _ = _modules()
    ctx = _recovery_parent(worker, root, child_kp_path)
    root = ctx['root']
    pointer = _recovery_check_receipts(worker, ctx)
    child_package, package = ctx['child_package'], ctx['package']
    if (child_package / 'completion_revision_current.json').exists():
        raise ValueError('the child already carries a completion revision')
    parent, child = ctx['completion'], ctx['child_completion']
    parent_record = read_current(root, package, parent, include_record=True, verify=False)
    if parent_record and (parent_record.get('payload_sha256') != digest(parent_record)
                          or parent_record.get('completion_payload_sha256') != parent['payload_sha256']):
        raise ValueError('accepted content revision receipt changed')
    prior_changes = {row['original_path']: row for row in (parent_record or {}).get('changes', [])}
    originals = original_files(parent)
    review_reference = (parent_record or {}).get('current_source_review')
    evidence = _recovery_evidence(root, ctx, evidence_transactions,
                                  inside(root, review_reference['path']).parent if review_reference else None)
    pool = evidence['pool']
    parent_adapter = parent.get('adapter') or {}
    source_rel, application_rel = parent_adapter.get('source_note'), parent_adapter.get('application_note')
    if not source_rel or not application_rel:
        raise ValueError('the parent receipt lacks its source or application note')

    def accepted(relative):
        change = prior_changes.get(relative)
        return change['sha256'] if change else originals[relative]

    def live_file(relative):
        path = inside(root, relative)
        if not path.is_file():
            raise ValueError('a note to recover is missing: ' + relative)
        return path

    def accepted_bytes(relative):
        path = live_file(relative)
        if artifact_matches(path, accepted(relative)):
            return path.read_bytes()
        if accepted(relative) not in pool:
            raise ValueError('no evidence holds the accepted bytes of ' + relative)
        return pool[accepted(relative)]

    files = {}
    # 1. source note and application note: the accepted bytes plus the picture overlay of the live (full-writer) text
    live_source, live_application = live_file(source_rel).read_bytes(), live_file(application_rel).read_bytes()
    source_base, application_base = accepted_bytes(source_rel), accepted_bytes(application_rel)
    try:
        source_after, application_after = adapter.render_infographic_overlay(
            source_before=source_base, source_rendered=live_source.decode('utf-8-sig'),
            application_before=application_base, application_rendered=live_application.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('the picture overlay cannot be derived: ' + str(exc)) from exc
    child_adapter = child.get('adapter') or {}
    vault_prefix = worker.vault_relative_posix() + '/'
    assets = [str(value) for value in child_adapter.get('assets') or []]
    if not assets or any(not value.startswith(vault_prefix + 'assets/') or not value.lower().endswith('.png') for value in assets):
        raise ValueError('the child receipt lists no formal infographic images')
    child_assets = {value[len(vault_prefix):] for value in assets}
    child_images = {identity for value in child_assets if (identity := worker._overlay_image_identity(value))}

    def text(data):
        return data.decode('utf-8-sig').replace('\r\n', '\n')

    for label, base, after in (('source', source_base, source_after), ('application', application_base, application_after)):
        allowed = worker._overlay_image_references(text(base)) | child_images
        if any(not worker._overlay_image_allowed(ref, allowed) for ref in worker._overlay_image_references(text(after))):
            raise ValueError('the recovered ' + label + ' note names an image that is not the child\'s')
    for field in ('source_assets', 'infographics'):
        value = adapter.frontmatter_fields_from_text(text(source_after)).get(field)
        listed = set(value) if isinstance(value, list) else ({value} if value else set())
        if listed != child_assets:
            raise ValueError('the recovered source note does not list exactly the child images')
    if source_after != live_source:
        files[source_rel] = source_after
    if application_after != live_application:
        files[application_rel] = application_after
    # 2. knowledge-point notes that are not at their accepted bytes
    for relative in parent_adapter.get('knowledge_points') or []:
        path = live_file(relative)
        if not artifact_matches(path, accepted(relative)):
            if accepted(relative) not in pool:
                raise ValueError('no evidence holds the accepted bytes of ' + relative)
            files[relative] = pool[accepted(relative)]
    # 3. registries: this source's rows come from the evidence, every other row stays live
    differences = {'registry_rows': [], 'concept_pages': []}
    own_texts = set()
    for name in _RECOVERY_REGISTRIES:
        relative = f'{vault_prefix}_registry/{name}.jsonl'
        options = evidence['before'].get(relative)
        if not options:
            continue
        if len(options) != 1:
            raise ValueError('the evidence disagrees about ' + relative)
        path = live_file(relative)
        evidence_registry = pool[next(iter(options))]
        replaced, row_changes = _recovery_registry_after(path.read_bytes(), evidence_registry, parent['source_id'], relative)
        differences['registry_rows'].extend(row_changes)
        for line in evidence_registry.splitlines():
            row = json.loads(line.decode('utf-8-sig')) if line.strip() else {}
            if isinstance(row, dict) and row.get('source_id') == parent['source_id']:
                own_texts.update(value for value in row.values() if isinstance(value, str) and len(value) >= 12)
        if replaced != path.read_bytes():
            files[relative] = replaced
    # 4. generated concept pages the evidence lists, limited to this source's own contribution
    own_tokens = {parent['source_id'], *(Path(value).stem for value in [source_rel, application_rel, *(parent_adapter.get('knowledge_points') or [])])}
    for relative in parent_adapter.get('knowledge_points') or []:
        fields = adapter.frontmatter_fields_from_text(text(files[relative] if relative in files else live_file(relative).read_bytes()))
        own_texts.update(value for key in ('knowledge_point', 'definition', 'source_anchor', 'knowledge_title', 'reusable_concept_name',
                                           'reusable_concept_reason') for value in [fields.get(key)] if isinstance(value, str) and len(value) >= 12)
    concept_prefix = vault_prefix + 'concepts/'
    for relative in sorted(evidence['before']):
        if not (relative.startswith(concept_prefix) and relative.endswith('.md')):
            continue
        options = evidence['before'][relative]
        if len(options) != 1:
            raise ValueError('the evidence disagrees about ' + relative)
        before_sha, snapshot = next(iter(options.items()))
        path = inside(root, relative)
        if before_sha == sha(b'') or (path.is_file() and sha(path.read_bytes()) == before_sha):
            continue
        if path.is_file() and not adapter.is_generated_concept_note(path):
            raise ValueError('a handwritten concept page cannot be recovered: ' + relative)
        if not adapter.is_generated_concept_note(inside(root, snapshot)):
            raise ValueError('the evidence page is not a generated concept page: ' + relative)
        if path.is_file():
            differences['concept_pages'].append({'path': relative, 'hunks': _recovery_concept_limits(
                relative, path.read_bytes(), pool[before_sha], parent['source_id'], own_tokens, own_texts)})
        files[relative] = pool[before_sha]
    if not files:
        raise ValueError('the live files already hold the recovered text; there is nothing to recover')
    # the child's own receipt: which notes it must now bind, and that nothing else of it moved
    child_manifest = {row['path']: row['sha256'] for row in child.get('artifact_manifest') or []}
    child_notes = [child_adapter.get('source_note'), child_adapter.get('application_note'), *(child_adapter.get('knowledge_points') or [])]
    child_changes = []
    for relative in child_notes:
        if relative not in child_manifest:
            raise ValueError('the child receipt does not bind a note: ' + str(relative))
        if relative in files:
            final = sha(files[relative])
        elif artifact_matches(live_file(relative), child_manifest[relative]):
            continue
        else:
            raise ValueError('a child receipt note differs and is not restored by the evidence: ' + relative)
        if final != child_manifest[relative]:
            child_changes.append({'original_path': relative, 'original_sha256': child_manifest[relative],
                                  'current_path': relative, 'sha256': final})
    _recovery_check_child_artifacts(worker, root, child_package, child, set(child_notes))
    parent_pointer = package / 'completion_revision_current.json'
    return {'ctx': ctx, 'pointer': pointer, 'evidence': evidence, 'files': files, 'prior_changes': prior_changes,
            'differences': differences, 'child_changes': child_changes, 'parent_previous_pointer_sha256': sha(parent_pointer.read_bytes()) if parent_pointer.exists() else ''}


def prepare_infographic_recovery(root, child_kp_path, *, evidence_transactions, authorization, reason):
    """Stage one recovery transaction for an infographic child written by the old full writer; never writes a vault file."""
    worker, adapter, _ = _modules()
    root = Path(root).resolve()
    if len(authorization.strip()) < 6 or len(reason.strip()) < 6:
        raise ValueError('infographic recovery needs explicit authorization and a concrete reason')
    ctx = _recovery_parent(worker, root, child_kp_path)
    package = ctx['package']
    evidence_files = []
    for value in evidence_transactions or []:
        relative = _recovery_relative(root, value)
        evidence_files.append({'path': relative, 'sha256': sha(inside(root, relative).read_bytes())})
    request = {'child_kp_path': ctx['child_kp_path'].relative_to(root).as_posix(),
               'child_completion_payload_sha256': ctx['child_completion'].get('payload_sha256'),
               'evidence_transactions': sorted({row['path']: row for row in evidence_files}.values(), key=lambda row: row['path']),
               'authorization': authorization, 'reason': reason, 'projection_contract_version': RECOVERY_PROJECTION_VERSION}
    request_sha256 = digest(request)
    folder = inside(root, _evidence_folder(package, 'recovery', request_sha256).relative_to(root).as_posix())
    journal_path = folder / 'transaction.json'
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        if journal.get('payload_sha256') != digest(journal) or journal.get('request_sha256') != request_sha256:
            raise ValueError('infographic recovery staging conflict')
        return _prepared_result(root, journal_path, journal)
    derived = _derive_infographic_recovery(root, ctx['child_kp_path'], evidence_transactions)
    parent, child = ctx['completion'], ctx['child_completion']
    originals = original_files(parent)
    staged_paths = []
    for index, relative in enumerate(sorted(derived['files'], key=lambda value: (not value.endswith('.md'), value))):
        path = inside(root, relative)
        staged_paths.extend(_staged_evidence_paths(folder, index, path))
    _assert_evidence_path_budget(folder, staged_paths)
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder / 'request.json'
    request_path.write_bytes(_json_bytes(request))
    items = []
    for index, relative in enumerate(sorted(derived['files'], key=lambda value: (not value.endswith('.md'), value))):
        path = inside(root, relative)
        before, after = path.read_bytes() if path.exists() else b'', derived['files'][relative]
        staged, snapshot = _staged_evidence_paths(folder, index, path)
        staged.write_bytes(after)
        snapshot.write_bytes(before)
        items.append({'path': relative, 'candidate_path': staged.relative_to(root).as_posix(),
                      'before_path': snapshot.relative_to(root).as_posix(), 'before_sha256': sha(before),
                      'after_sha256': sha(after), 'owner': 'vault_adapter'})
    kp_path, child_kp_path = ctx['kp_path'], ctx['child_kp_path']
    child_package = ctx['child_package']
    job_path = worker.job_file(root, ctx['kp']['job_id'])
    child_job_path = worker.job_file(root, ctx['child_kp']['job_id'])
    infographic_pointer = package / 'infographic_current.json'
    journal = {'schema_version': 'completed_content_revision_transaction_v1', 'state': 'prepared',
               'request_sha256': request_sha256, 'request_path': request_path.relative_to(root).as_posix(),
               'source_id': parent['source_id'], 'source_identity': parent['source_identity'],
               'kp_path': kp_path.relative_to(root).as_posix(), 'slice_path': parent['source_slice_path'],
               'completion_path': ctx['completion_path'].relative_to(root).as_posix(),
               'completion_sha256': sha(ctx['completion_path'].read_bytes()), 'completion_payload_sha256': parent['payload_sha256'],
               'job_path': job_path.relative_to(root).as_posix(), 'job_sha256': sha(job_path.read_bytes()),
               'previous_pointer_sha256': derived['parent_previous_pointer_sha256'],
               'authorization': authorization, 'reason': reason, 'items': items, 'projection_scope': RECOVERY_SCOPE,
               'protected_inputs': _recovery_protected(root, package, parent, {row['path'] for row in items} | set(derived['prior_changes'])),
               'prior_changes': derived['prior_changes'], 'retirement_candidates': [],
               'recovery_differences': derived['differences'],
               'evidence_transactions': request['evidence_transactions'],
               'child_revision': {
                   'kp_path': child_kp_path.relative_to(root).as_posix(),
                   'completion_path': ctx['child_completion_path'].relative_to(root).as_posix(),
                   'completion_sha256': sha(ctx['child_completion_path'].read_bytes()), 'completion_payload_sha256': child['payload_sha256'],
                   'job_path': child_job_path.relative_to(root).as_posix(), 'job_sha256': sha(child_job_path.read_bytes()),
                   'previous_pointer_sha256': '', 'infographic_pointer_sha256': sha(infographic_pointer.read_bytes()),
                   'changes': derived['child_changes'],
                   'protected_inputs': _recovery_protected(root, child_package, child, {row['original_path'] for row in derived['child_changes']})}}
    _save_json(journal_path, journal)
    return _prepared_result(root, journal_path, json.loads(journal_path.read_text(encoding='utf-8')))


def _recovery_predecessors(worker, root, journal, ctx, *, parent_own, child_own):
    """Read-only proof, before the first write, that parent and child are exactly what the transaction was prepared against."""
    child = journal['child_revision']
    package, child_package = ctx['package'], ctx['child_package']
    parent, child_completion = ctx['completion'], ctx['child_completion']
    allowed = {row['path']: {row['before_sha256'], row['after_sha256']} for row in journal['items']}
    pointer = worker.read_current_infographic_pointer(root, child_package, ctx['child_kp'])
    if not pointer or pointer.get('current_completion_path') != child['completion_path']:
        raise ValueError('the current infographic pointer no longer selects the child')
    if parent_own:
        read_current(root, package, parent, include_record=True)
    else:
        record = read_current(root, package, parent, include_record=True, verify=False)
        if record and record.get('payload_sha256') != digest(record):
            raise ValueError('accepted content revision receipt changed')
        current = validate(root, record, parent, allowed_current_hashes=allowed) if record else {}
        if current != journal['prior_changes']:
            raise ValueError('accepted content revision changed since prepare')
        # The pointer that selects the child supersedes the parent's own manifest artifacts (completed_receipt_readback),
        # so only what the pointer does not supersede must still match the parent's accepted history.
        superseded = set(pointer.get('superseded_artifact_paths') or [])
        for original_path, original_sha256 in original_files(parent).items():
            if original_path in allowed or original_path in superseded:
                continue
            prior = current.get(original_path)
            expected = prior['sha256'] if prior else original_sha256
            if original_path.endswith('.html'):
                expected = worker.outcome_revision_hash(root, package, parent, original_path) or expected
            if not artifact_matches(inside(root, prior['current_path'] if prior else original_path), expected):
                raise ValueError('unamended completed artifact changed since prepare: ' + (prior['current_path'] if prior else original_path))
    if child_own:
        read_current(root, child_package, child_completion, include_record=True)
        return
    adapter_value = child_completion.get('adapter') or {}
    notes = [adapter_value.get('source_note'), adapter_value.get('application_note'), *(adapter_value.get('knowledge_points') or [])]
    manifest = {row['path']: row['sha256'] for row in child_completion.get('artifact_manifest') or []}
    child_final = {row['current_path']: row['sha256'] for row in child['changes']}
    for relative in notes:
        accepted_values = {manifest[relative], *allowed.get(relative, set()), *([child_final[relative]] if relative in child_final else [])}
        if not any(artifact_matches(inside(root, relative), value) for value in accepted_values):
            raise ValueError('a child receipt note changed since prepare: ' + relative)
    _recovery_check_child_artifacts(worker, root, child_package, child_completion, set(notes))


def _recovery_own(pointer, record_path):
    if not record_path or not pointer.exists():
        return False
    return json.loads(pointer.read_text(encoding='utf-8')).get('revision_path') == record_path


def _recovery_records(root, journal, completion, child_completion, review_reference):
    """The parent's new revision record and the child's (None when the child's notes need no change); deterministic."""
    child = journal['child_revision']
    originals = original_files(completion)
    evidence = [{'path': review_reference['path'], 'sha256': review_reference['sha256'], 'supports': journal['reason']}]
    changes = copy.deepcopy(journal['prior_changes'])
    for item in journal['items']:
        if item['path'] in originals:
            changes[item['path']] = {'original_path': item['path'], 'original_sha256': originals[item['path']],
                'current_path': item['path'], 'sha256': item['after_sha256'], 'reason': journal['reason'], 'evidence': evidence}
    record = {'schema_version': SCHEMA, 'completion_path': journal['completion_path'],
              'completion_payload_sha256': completion['payload_sha256'], 'source_id': journal['source_id'],
              'source_identity': journal['source_identity'], 'authorization': journal['authorization'],
              'review_summary': journal['reason'], 'previous_pointer_sha256': journal['previous_pointer_sha256'],
              'protected_inputs': journal['protected_inputs'], 'changes': list(changes.values()),
              'current_source_review': review_reference,
              'projection_evidence': [{'path': item['path'], 'candidate_path': item['candidate_path'],
                                       'before_sha256': item['before_sha256'], 'after_sha256': item['after_sha256']}
                                      for item in journal['items']],
              'retirement_candidates': journal['retirement_candidates']}
    record['payload_sha256'] = digest(record)
    child_record = None
    if child['changes']:
        child_record = {'schema_version': SCHEMA, 'completion_path': child['completion_path'],
                        'completion_payload_sha256': child_completion['payload_sha256'], 'source_id': journal['source_id'],
                        'source_identity': journal['source_identity'], 'authorization': journal['authorization'],
                        'review_summary': journal['reason'], 'previous_pointer_sha256': child['previous_pointer_sha256'],
                        'protected_inputs': child['protected_inputs'],
                        'changes': [{**row, 'reason': journal['reason'], 'evidence': evidence} for row in child['changes']],
                        'current_source_review': review_reference}
        child_record['payload_sha256'] = digest(child_record)
    return record, child_record


def _apply_infographic_recovery(root, journal_path, journal, review, *, dry_run):
    """The infographic_recovery branch of apply_content_revision; the common review/evidence checks have already passed."""
    worker, adapter, _ = _modules()
    child = journal['child_revision']
    package = inside(root, journal['kp_path']).parent
    child_kp_path = inside(root, child['kp_path'])
    child_package = child_kp_path.parent
    completion_path = inside(root, journal['completion_path'])
    child_completion_path = inside(root, child['completion_path'])
    if (sha(child_completion_path.read_bytes()) != child['completion_sha256']
            or sha(inside(root, child['job_path']).read_bytes()) != child['job_sha256']):
        raise ValueError('infographic recovery changed the child completion or job')
    if sha((package / 'infographic_current.json').read_bytes()) != child['infographic_pointer_sha256']:
        raise ValueError('infographic recovery changed the current infographic pointer')
    for item in child['protected_inputs']:
        if not artifact_matches(inside(root, item['path']), item['sha256']):
            raise ValueError('infographic recovery protected child input changed')
    pointer, child_pointer = package / 'completion_revision_current.json', child_package / 'completion_revision_current.json'
    parent_own = _recovery_own(pointer, journal.get('record_path'))
    child_own = _recovery_own(child_pointer, journal.get('child_record_path'))
    if (sha(pointer.read_bytes()) if pointer.exists() else '') != journal['previous_pointer_sha256'] and not parent_own:
        raise ValueError('stale content revision pointer')
    if (sha(child_pointer.read_bytes()) if child_pointer.exists() else '') != child['previous_pointer_sha256'] and not child_own:
        raise ValueError('stale child revision pointer')
    completion = worker.read_json(completion_path)
    child_completion = worker.read_json(child_completion_path)
    ctx = {'package': package, 'child_package': child_package, 'completion': completion, 'child_completion': child_completion,
           'child_kp': worker.read_json(child_kp_path)}
    _recovery_predecessors(worker, root, journal, ctx, parent_own=parent_own, child_own=child_own)
    # Both new records are built and validated against the review BEFORE the first write (a dry run included): the review must cover
    # every change of the parent's new record (its accepted prior changes too, such as the KP JSON and the slice) and of the child's.
    review_file = _review_file_path(journal_path.parent, review)
    review_bytes = _json_bytes(review)
    if review_file.exists():
        if json.loads(review_file.read_text(encoding='utf-8')) != review:
            raise ValueError('immutable content review conflict')
        review_bytes = review_file.read_bytes()
    review_reference = {'path': review_file.relative_to(root).as_posix(), 'sha256': sha(review_bytes)}
    if journal.get('review_sha256') and journal['review_sha256'] != review_reference['sha256']:
        raise ValueError('recovery requires the original reviewed candidate')
    record, child_record = _recovery_records(root, journal, completion, child_completion, review_reference)
    allowed = {row['path']: {row['before_sha256'], row['after_sha256']} for row in journal['items']}
    virtual = {row['path']: inside(root, row['candidate_path']).read_bytes() for row in journal['items']}
    virtual[review_reference['path']] = review_bytes
    validate(root, record, completion, allowed_current_hashes=allowed, virtual=virtual)
    if child_record is not None:
        validate(root, child_record, child_completion, allowed_current_hashes=allowed, virtual=virtual)
    if dry_run:
        return {'status': 'reviewed_ready', 'write_performed': False, 'formal_artifacts_unchanged': True,
                'revision_file': journal_path.relative_to(root).as_posix(), 'changes': journal['items']}
    vault = worker.vault_path(root)
    child_kp = ctx['child_kp']
    child_slice = inside(root, child_completion['source_slice_path'])
    adapter.apply_reviewed_content_projection(root, vault, journal['items'], source_id=journal['source_id'])
    try:
        # The writer's own readback runs on the child's slice after its projection.
        data = adapter.parse_slice(child_slice, max_kp=len(child_kp['knowledge']))
        live_plan = adapter.build_plan(root, vault, child_slice, data)
        verification = adapter.verify_plan(live_plan, source_local=True)
        if not verification.get('passed'):
            failed = sorted(key for key, value in verification.items() if value is False)
            raise ValueError('infographic recovery writer readback failed: ' + ', '.join(failed))
    except Exception:
        reverse = []
        empty_sha256 = sha(b'')
        for item in journal['items']:
            if item['before_sha256'] == empty_sha256:
                target = inside(root, item['path'])
                if target.exists():
                    if sha(target.read_bytes()) != item['after_sha256']:
                        raise ValueError('infographic recovery target changed before rollback: ' + item['path'])
                    getattr(target, 'unlink')()
                if target.exists():
                    raise ValueError('infographic recovery rollback left an absent target: ' + item['path'])
            else:
                reverse.append({**item, 'candidate_path': item['before_path'],
                                'before_sha256': item['after_sha256'], 'after_sha256': item['before_sha256']})
        if reverse:
            adapter.apply_reviewed_content_projection(root, vault, reverse, source_id=journal['source_id'])
        raise
    if not review_file.exists():
        review_file.write_bytes(review_bytes)
    journal.update(review_sha256=review_reference['sha256'], state='applying')
    _save_json(journal_path, journal)
    journal['record_path'] = (package / 'completion_revisions' / (record['payload_sha256'] + '.json')).relative_to(root).as_posix()
    if child_record is not None:
        journal['child_record_path'] = (child_package / 'completion_revisions' / (child_record['payload_sha256'] + '.json')).relative_to(root).as_posix()
    _save_json(journal_path, journal)
    if (sha(pointer.read_bytes()) if pointer.exists() else '') == journal['previous_pointer_sha256']:
        accept(root, package, completion, record)
    else:
        read_current(root, package, completion)
    if child_record is not None:
        if (sha(child_pointer.read_bytes()) if child_pointer.exists() else '') == child['previous_pointer_sha256']:
            accept(root, child_package, child_completion, child_record)
        else:
            read_current(root, child_package, child_completion)
    journal['state'] = 'activated'
    _save_json(journal_path, journal)
    # The reader page belongs to the current (child) completion's own chain.
    worker.refresh_completed_outcome(root, child_kp_path)
    worker.completed_receipt_readback(root, package, inside(root, journal['kp_path']), worker.read_json(inside(root, journal['kp_path'])))
    worker.completed_receipt_readback(root, child_package, child_kp_path, worker.read_json(child_kp_path))
    _ensure_content_revision_candidates(root, package, completion, journal_path, journal)
    journal['state'] = 'completed'
    _save_json(journal_path, journal)
    return {'status': 'completed', 'revision_file': journal_path.relative_to(root).as_posix(),
            'write_performed': True, 'original_completion_preserved': True,
            'idea_candidates': journal.get('idea_candidates'),
            'original_job_and_timing_preserved': True, 'retirement_candidates': journal['retirement_candidates']}


# ---------------------------------------------------------------------------
# The one writer of the knowledge-point notes of a completed v2 source.
#
# A completion receipt keeps the bytes each note had when the source was ingested.  A rewritten note without an accepted revision
# makes ``completed_receipt_readback`` fail, and the reader service cannot start (2026-10-16).  Nothing but this section may
# rewrite such a note: bytes, revision record, read-back and undo are one transaction.  The check that keeps it that way is
# tests/test_completed_kp_writer.py (static: every write call that can reach a knowledge-point note is listed with its reason;
# runtime: a direct rewrite fails ``receipt_readback_check.py``).
#
#   classify_kp_notes   pure: which source owns each note, and whether that source is completed (refuses what no revision can hold)
#   stage_kp_notes      first half: the bytes (refuses before the first byte what ``register_kp_notes`` could not register)
#   register_kp_notes   second half: one revision record per completed source, read back at once; every registration undone on failure
#   restore_kp_notes    puts back the notes a stage wrote (only those still holding exactly the staged bytes)
#   rewrite_kp_notes    the entry for a new caller: stage, register, and on any failure restore
#
# The caller holds the vault write lock (the writer never takes it, so a caller that already holds it cannot deadlock).  A note of
# a source that is not completed is written as it is; a note of a completed source needs immutable evidence, and a completed
# source whose package the resolver cannot find (an open job beside a receipt) is refused, never treated as unfinished.
# ---------------------------------------------------------------------------

OPEN_JOB_STATUSES = frozenset({'running', 'awaiting_imagegen', 'committing'})  # the reader's own "still writing" job statuses
UNSUPPORTED_PRIOR_FIELDS = ('current_source_review', 'legacy_receipt', 'projection_evidence')
NOTE_INVALID = 'completed_kp_note_invalid'
OPEN_JOB = 'completed_kp_open_job'
PACKAGE_UNRESOLVED = 'completed_kp_package_unresolved'
NOT_IN_RECEIPT = 'completed_kp_note_not_in_receipt'
REVISION_UNSUPPORTED = 'completed_kp_revision_unsupported'
EVIDENCE_INVALID = 'completed_kp_evidence_invalid'
NOTE_CHANGED = 'completed_kp_note_changed'
WRITE_FAILED = 'completed_kp_write_failed'
UNDO_INCOMPLETE = 'completed_kp_undo_incomplete'
# NOTE_INVALID .. REVISION_UNSUPPORTED refuse before the first byte is written; EVIDENCE_INVALID, NOTE_CHANGED, WRITE_FAILED and UNDO_INCOMPLETE fail after it (EVIDENCE_INVALID at registration, when the staged notes are put back)


class CompletedNoteError(ValueError):
    """A refusal or failure of the knowledge-point note writer; the message starts with its error code (``code``)."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


def _kp_note(base, value):
    """(relative posix path, path, source id, vault folder name) of a note below the configured vault."""
    path = Path(value)
    absolute = path if path.is_absolute() else base / path
    try:
        relative = absolute.relative_to(base)
        target = inside(base, relative.as_posix())
    except ValueError as exc:
        raise CompletedNoteError(NOTE_INVALID, f'{value} is not a path inside the workspace without links ({exc})') from exc
    try:
        from .workspace_paths import WorkspacePathError, managed_vault_path
    except ImportError:
        from workspace_paths import WorkspacePathError, managed_vault_path
    try:
        vault_relative = managed_vault_path(base).relative_to(base)
    except WorkspacePathError as exc:
        raise CompletedNoteError(NOTE_INVALID, str(exc)) from exc
    parts = relative.parts
    prefix, tail = vault_relative.parts, parts[len(vault_relative.parts):]
    if (parts[:len(prefix)] != prefix or len(tail) != 4 or tail[0] != 'knowledge-points'
            or tail[1] != 'by-source' or not tail[3].endswith('.md')):
        raise CompletedNoteError(NOTE_INVALID, f'{relative.as_posix()} is not <configured vault>/knowledge-points/by-source/<source id>/<name>.md')
    return relative.as_posix(), target, tail[2], vault_relative.as_posix()


def _kp_targets(base, notes):
    """{relative path: (path, bytes)} for a mapping of notes to their new bytes."""
    targets = {}
    for value, data in dict(notes).items():
        if not isinstance(data, (bytes, bytearray)):
            raise CompletedNoteError(NOTE_INVALID, f'the new content of {value} must be bytes')
        relative, path, _, _ = _kp_note(base, value)
        targets[relative] = (path, bytes(data))
    if not targets:
        raise CompletedNoteError(NOTE_INVALID, 'no knowledge-point note was given')
    return targets


def _kp_resolver():
    try:
        from .outcome_page_generator import completed_v2_package_for_standard_outcome
    except ImportError:
        from outcome_page_generator import completed_v2_package_for_standard_outcome
    return completed_v2_package_for_standard_outcome


def _open_job(package_root, source_id):
    jobs = package_root / '_jobs'
    for path in jobs.glob('*.json') if jobs.is_dir() else ():
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if (isinstance(job, dict) and job.get('schema_version') == 'compiler_worker_v2_job_v1'
                and job.get('source_id') == source_id and job.get('status') in OPEN_JOB_STATUSES):
            return True
    return False


def _has_completion_receipt(package_root, source_id):
    for path in package_root.rglob('completion_receipt*.json'):
        try:
            receipt = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(receipt, dict) and receipt.get('source_id') == source_id:
            return True
    return False


def _kp_owner(base, source_id, vault, notes, strict):
    """One source's owner record; refuses what no revision can hold.  Reads only.  ``strict``: the existing revision must read back
    with the notes as they are (before the first write); otherwise (the notes already hold their new bytes) only its record is read."""
    worker, _, _ = _modules()
    owner = {'source_id': source_id, 'completed': False, 'notes': notes}
    package_root = base / 'outputs' / 'compiler_worker_v2'
    if _open_job(package_root, source_id):
        # The reader's resolver answers "no package" while a job is open, even beside a receipt: that is not "unfinished".
        if _has_completion_receipt(package_root, source_id):
            raise CompletedNoteError(OPEN_JOB, f'{source_id} has a completion receipt and also an open (running, awaiting_imagegen or '
                                     'committing) job, so its package cannot be resolved; finish or close that job first. Its notes were not written.')
        return owner
    try:
        kp_path = _kp_resolver()(vault, source_id, (vault / 'outcome-pages' / f'{source_id}_outcome.html').resolve(), root=base)
        if kp_path is None:
            return owner
        kp = worker.read_json(kp_path)
        completion_path = worker.bound_completion_path(base, kp_path.parent, kp_path, kp)
        completion = worker.read_json(completion_path)
        originals = original_files(completion)
    except (SystemExit, worker.CompilerWorkerV2Error, OSError, ValueError, KeyError) as exc:
        raise CompletedNoteError(PACKAGE_UNRESOLVED, f'{source_id} is a completed v2 source without one verified current completion '
                                 f'({exc}); repair that first. Its notes were not written.') from exc
    listed = set((completion.get('adapter') or {}).get('knowledge_points') or [])
    for relative in sorted(notes):
        if relative not in listed or relative not in originals:
            raise CompletedNoteError(NOT_IN_RECEIPT, f'{relative} belongs to the completed source {source_id} but is not a saved '
                                     'knowledge-point note of its completion receipt, so no revision can cover it.')
    package = kp_path.parent
    try:
        prior = read_current(base, package, completion, include_record=True, verify=strict)
    except (ValueError, KeyError, OSError) as exc:
        raise CompletedNoteError(REVISION_UNSUPPORTED, f'the existing revision of {source_id} does not read back ({exc}); '
                                 'repair that first, then rerun.') from exc
    if any(prior.get(field) for field in UNSUPPORTED_PRIOR_FIELDS):
        raise CompletedNoteError(REVISION_UNSUPPORTED, f'{source_id} already carries a content or historical-receipt revision; a note of it '
                                 'cannot be added to that revision here. Use the content-revision flow, or leave the note alone.')
    owner.update(completed=True, package=package, kp_path=kp_path, kp=kp, completion_path=completion_path, completion=completion,
                 originals=originals, prior=prior)
    return owner


def classify_kp_notes(root, notes, *, strict=True):
    """Pure.  One owner record per source, in source order: {'source_id', 'completed', 'notes': {relative path: path}} and, when the
    source is completed, 'package', 'kp_path', 'kp', 'completion_path', 'completion', 'originals' and 'prior' (its current revision
    record, or {}).  Raises CompletedNoteError, before anything is written, for a note no revision can hold.  ``strict=False`` is for
    notes that already hold their new bytes (``register_kp_notes``): a note that already carries an accepted revision may be rewritten
    again, and ``accept`` then validates the whole new record."""
    base = Path(root).resolve()
    groups = {}
    for value in notes:
        relative, path, source_id, vault_name = _kp_note(base, value)
        group = groups.setdefault(source_id, {'vault': base / vault_name, 'notes': {}})
        if group['vault'] != base / vault_name:
            raise CompletedNoteError(NOTE_INVALID, f'the notes of {source_id} sit in two vault folders')
        group['notes'][relative] = path
    return [_kp_owner(base, source_id, group['vault'], group['notes'], strict) for source_id, group in sorted(groups.items())]


def _kp_atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.kp-writer.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def stage_kp_notes(root, notes):
    """First half of the writer.  Writes the bytes of every note that does not already hold them and returns the relative paths it wrote.

    Refuses, before the first byte, a note of a completed source that ``register_kp_notes`` could not register.  If a write fails
    half way, the notes already written by this call are put back.  A caller that stages a completed source's note must register
    it in the same transaction (the vault write lock); ``rewrite_kp_notes`` does both."""
    base = Path(root).resolve()
    targets = _kp_targets(base, notes)
    classify_kp_notes(base, [path for path, _ in targets.values()])
    staged = {}
    try:
        for relative, (path, data) in sorted(targets.items()):
            before = path.read_bytes() if path.is_file() else None
            if before == data:
                continue
            staged[relative] = (path, before, data)
            _kp_atomic_write(path, data)
    except BaseException as exc:
        try:
            restore_kp_notes(base, {path: (before, data) for path, before, data in staged.values()})
        except CompletedNoteError as undo:
            raise undo from exc
        raise
    return sorted(staged)


def restore_kp_notes(root, staged):
    """Put back the notes a stage wrote: ``staged`` maps each note to (its bytes before, the bytes the stage wrote); a note that no
    longer holds exactly the staged bytes is left alone."""
    base = Path(root).resolve()
    problems = []
    for value, (before, after) in sorted(dict(staged).items(), key=lambda item: str(item[0]), reverse=True):
        relative, path, _, _ = _kp_note(base, value)
        try:
            if path.is_file() and path.read_bytes() == after:
                if before is None:
                    path.unlink()
                else:
                    _kp_atomic_write(path, before)
        except OSError as exc:
            problems.append(f'{relative}: {type(exc).__name__}: {exc}')
    if problems:
        raise CompletedNoteError(UNDO_INCOMPLETE, 'these notes could not be put back: ' + '; '.join(problems))


def _kp_evidence(base, evidence):
    if evidence is None:
        raise CompletedNoteError(EVIDENCE_INVALID, 'a note of a completed source needs an immutable evidence file')
    try:
        path = Path(evidence)
        relative = (path if path.is_absolute() else base / path).relative_to(base).as_posix()
        path = inside(base, relative)
        data = path.read_bytes()
    except (ValueError, OSError) as exc:
        raise CompletedNoteError(EVIDENCE_INVALID, f'the evidence file {evidence} is not a readable file inside the workspace ({exc})') from exc
    return {'path': relative, 'sha256': sha(data)}


def _kp_revision_record(base, owner, entries, evidence, authorization, summary):
    """The record for one completed source: the whole accepted change set so far plus these notes."""
    completion, package, prior = owner['completion'], owner['package'], owner['prior']
    originals = owner['originals']
    changes = {change['original_path']: change for change in prior.get('changes', [])}
    for entry in entries:
        changes[entry['path']] = {
            'original_path': entry['path'], 'original_sha256': originals[entry['path']], 'current_path': entry['path'],
            'sha256': entry['sha256'], 'reason': entry['reason'],
            'evidence': [{'path': evidence['path'], 'sha256': evidence['sha256'], 'supports': entry['supports']}]}
    adapter_info = completion.get('adapter') or {}
    note_paths = {adapter_info.get('source_note'), adapter_info.get('application_note'), *(adapter_info.get('knowledge_points') or [])} - {None, ''}
    protected = {entry['path']: entry['sha256'] for entry in prior.get('protected_inputs', [])}
    for path, expected in originals.items():
        if (path not in changes and path not in note_paths and not path.endswith('.html') and '/assets/' not in path
                and path not in {completion['knowledge_package_path'], completion['source_slice_path']}):
            protected.setdefault(path, expected)
    for name in ('input_bundle.json', 'canonical_source.md', 'source_capture.md'):
        candidate = package / name
        if candidate.is_file():
            protected.setdefault(candidate.relative_to(base).as_posix(), sha(candidate.read_bytes()))
    pointer = package / 'completion_revision_current.json'
    record = {'schema_version': SCHEMA, 'completion_path': owner['completion_path'].relative_to(base).as_posix(),
              'completion_payload_sha256': completion['payload_sha256'], 'source_id': completion['source_id'],
              'source_identity': completion['source_identity'], 'authorization': authorization, 'review_summary': summary,
              'previous_pointer_sha256': sha(pointer.read_bytes()) if pointer.exists() else '',
              'protected_inputs': [{'path': path, 'sha256': value} for path, value in sorted(protected.items())],
              'changes': list(changes.values())}
    record['payload_sha256'] = digest(record)
    return record


def _undo_registrations(states):
    """Remove what failed registrations may have left behind: the record file, the pointer change and an empty folder they created."""
    problems = []
    for state in reversed(states):
        record, pointer, folder = state['record_path'], state['pointer'], state['folder']
        try:
            if not state['record_existed'] and record.exists():
                record.unlink()
            if state['pointer_before'] is None:
                if pointer.exists():
                    pointer.unlink()
            elif not pointer.exists() or pointer.read_bytes() != state['pointer_before']:
                _kp_atomic_write(pointer, state['pointer_before'])
            temporary = pointer.with_suffix('.tmp')
            if temporary.exists():
                temporary.unlink()
            if not state['folder_existed'] and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError as exc:
            problems.append(f'{folder}: {type(exc).__name__}: {exc}')
    return problems


def register_kp_notes(root, notes, *, reason, evidence, authorization, summary, note_reasons=None, note_supports=None):
    """Second half of the writer: the notes already hold their bytes; accept one revision record per completed source and read each
    source back at once (``completed_receipt_readback``, never relaxed).  Any failure undoes every registration of this call.

    ``reason`` is the fixed reason sentence of every change; ``note_reasons`` / ``note_supports`` override it per note (relative path or
    path as keys).  ``evidence`` is an immutable file inside the workspace; its sha256 is pinned in each record.  Returns
    {'revisions': [{'source_id', 'package', 'record', 'record_sha256', 'notes'}], 'readbacks': [{'source_id', 'status'}]}."""
    base = Path(root).resolve()
    targets = _kp_targets(base, notes)
    owners = [owner for owner in classify_kp_notes(base, [path for path, _ in targets.values()], strict=False) if owner['completed']]
    if not owners:
        return {'revisions': [], 'readbacks': []}
    if not all(str(value or '').strip() for value in (reason, authorization, summary)):
        raise CompletedNoteError(EVIDENCE_INVALID, 'a note of a completed source needs a reason, an authorization statement and a review summary')
    pinned = _kp_evidence(base, evidence)
    reasons = {_kp_note(base, key)[0]: str(text) for key, text in dict(note_reasons or {}).items()}
    supports = {_kp_note(base, key)[0]: str(text) for key, text in dict(note_supports or {}).items()}
    worker, _, _ = _modules()
    states, revisions, readbacks = [], [], []
    try:
        for owner in owners:
            registered = {change['original_path']: change['sha256'] for change in owner['prior'].get('changes', [])}
            entries = []
            for relative, path in sorted(owner['notes'].items()):
                data = targets[relative][1]
                if not path.is_file() or path.read_bytes() != data:
                    raise CompletedNoteError(NOTE_CHANGED, f'{relative} does not hold the bytes it is registered with')
                if registered.get(relative) == sha(data):
                    continue  # registered by an earlier run
                entries.append({'path': relative, 'sha256': sha(data), 'reason': reasons.get(relative) or reason,
                                'supports': supports.get(relative) or reason})
            if not entries:
                continue
            package = owner['package']
            record = _kp_revision_record(base, owner, entries, pinned, authorization, summary)
            pointer, folder = package / 'completion_revision_current.json', package / 'completion_revisions'
            record_path = folder / (record['payload_sha256'] + '.json')
            # recorded before the first write, so a half-finished accept is undone too
            states.append({'pointer': pointer, 'pointer_before': pointer.read_bytes() if pointer.exists() else None, 'folder': folder,
                           'folder_existed': folder.exists(), 'record_path': record_path, 'record_existed': record_path.exists()})
            target = accept(base, package, owner['completion'], record)
            completion = worker.completed_receipt_readback(base, package, owner['kp_path'], owner['kp'], allow_derived_outcome_drift=True)
            revisions.append({'source_id': owner['source_id'], 'package': package.relative_to(base).as_posix(),
                              'record': target.relative_to(base).as_posix(), 'record_sha256': sha(target.read_bytes()),
                              'notes': [entry['path'] for entry in entries]})
            readbacks.append({'source_id': owner['source_id'], 'status': (completion or {}).get('status')})
    except BaseException as exc:
        problems = _undo_registrations(states)
        if problems:
            raise CompletedNoteError(UNDO_INCOMPLETE, 'a registration could not be undone: ' + '; '.join(problems)) from exc
        if not isinstance(exc, Exception) or isinstance(exc, CompletedNoteError):
            raise
        raise CompletedNoteError(WRITE_FAILED, f'{type(exc).__name__}: {exc}') from exc
    return {'revisions': revisions, 'readbacks': readbacks}


def rewrite_kp_notes(root, notes, *, reason, evidence=None, authorization='', summary='', note_reasons=None, note_supports=None):
    """The entry for rewriting knowledge-point notes: ``notes`` maps each note to its new bytes.  A note of a source that is not
    completed is simply written; a note of a completed source is written, registered as a completed-source revision and read back,
    all in one transaction: on any failure every registration is undone and every note put back, and the error carries its code.
    Returns {'written': [...], 'revisions': [...], 'readbacks': [...]}."""
    base = Path(root).resolve()
    targets = _kp_targets(base, notes)
    before = {relative: path.read_bytes() if path.is_file() else None for relative, (path, _) in targets.items()}
    written = stage_kp_notes(base, notes)
    try:
        result = register_kp_notes(base, notes, reason=reason, evidence=evidence, authorization=authorization, summary=summary,
                                   note_reasons=note_reasons, note_supports=note_supports)
    except BaseException as exc:
        try:
            restore_kp_notes(base, {targets[relative][0]: (before[relative], targets[relative][1]) for relative in written})
        except CompletedNoteError as undo:
            raise undo from exc
        raise
    return {'written': written, **result}
