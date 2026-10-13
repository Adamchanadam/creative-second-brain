"""Windows/macOS text equivalence must not weaken binary or content integrity."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime' / 'workspace' / 'tools'))
from compiler_worker_v2 import receipt_artifact_matches
import compiler_worker_v2 as worker


class ReceiptNewlines(unittest.TestCase):
    def test_completed_receipt_across_platform_newlines(self):
        for original_newline, current_newline in ((b'\n', b'\r\n'), (b'\r\n', b'\n')):
            with tempfile.TemporaryDirectory(dir=os.environ.get('BRAIN_TEST_TMPDIR')) as folder:
                root=Path(folder); package=root/'package'; package.mkdir()
                kp={'job_id':'TEST','source':{'source_id':'TEST','source_identity':'fixture'}}
                data={'package/knowledge_package.json':(json.dumps(kp,indent=2)+'\n').encode(),
                      'package/slice.md':b'Original slice\nSecond line\n',
                      'package/ledger.json':b'{\n "attempts": []\n}\n',
                      'saved-note.md':'保留原文\n第二段\n'.encode(),
                      'image.png':b'\x89PNG\r\n\x1a\noriginal',
                      'package/source_capture.md':b'Raw captured input\n'}
                hashes={}
                for relative,body in data.items():
                    generated=relative not in ('image.png','package/source_capture.md')
                    original=body.replace(b'\n',original_newline) if generated else body
                    hashes[relative]=hashlib.sha256(original).hexdigest()
                    (root/relative).write_bytes(body.replace(b'\n',current_newline) if generated else body)
                receipt={'schema_version':worker.COMPLETION_SCHEMA_VERSION,'status':'completed',
                         'source_id':'TEST','source_identity':'fixture','job_id':'TEST',
                         'knowledge_package_path':'package/knowledge_package.json',
                         'knowledge_package_sha256':hashes['package/knowledge_package.json'],
                         'source_slice_path':'package/slice.md','source_slice_sha256':hashes['package/slice.md'],
                         'image_attempt_ledger_path':'package/ledger.json','image_attempt_ledger_sha256':hashes['package/ledger.json'],
                         'artifact_manifest':[{'path':p,'sha256':h} for p,h in hashes.items()]}
                receipt['payload_sha256']=worker.canonical_payload_sha256(receipt)
                receipt_path=package/'completion_receipt.json'
                receipt_path.write_text(json.dumps(receipt),encoding='utf8')
                receipt_bytes=receipt_path.read_bytes()
                job={'status':'completed','completion_receipt_path':'package/completion_receipt.json'}
                with patch.object(worker,'read_current_infographic_pointer',return_value=None), patch.object(worker,'outcome_revision_hash',return_value=None), patch.object(worker,'validate_job_package_binding',return_value=job):
                    self.assertEqual(worker.completed_receipt_readback(root,package,root/'package/knowledge_package.json',kp)['status'],'completed')
                    for relative in ('saved-note.md','package/knowledge_package.json','package/slice.md','package/ledger.json','image.png','package/source_capture.md'):
                        p=root/relative; before=p.read_bytes()
                        p.write_bytes(before+b'altered')
                        with self.assertRaises(worker.CompilerWorkerV2Error):
                            worker.completed_receipt_readback(root,package,root/'package/knowledge_package.json',kp)
                        p.write_bytes(before)
                self.assertEqual(receipt_path.read_bytes(),receipt_bytes)

    def test_platform_conversions_and_real_corruption(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('BRAIN_TEST_TMPDIR')) as folder:
            p = Path(folder) / 'note.md'
            lf = '原文與來源\n第二段：不能刪字。\n'.encode()
            crlf = lf.replace(b'\n', b'\r\n')
            for original, current in ((lf, crlf), (crlf, lf), (lf, lf), (crlf, crlf)):
                p.write_bytes(current)
                expected = hashlib.sha256(original).hexdigest()
                self.assertTrue(receipt_artifact_matches(p, expected))
                for corrupt in (current.replace('原文'.encode(), '改寫'.encode()),
                                current[:-3], b'\xef\xbb\xbf'+current, current+b' ',
                                current.replace(b'\n', b'\r')):
                    p.write_bytes(corrupt)
                    self.assertFalse(receipt_artifact_matches(p, expected))
            for name in ('image.png', 'input.bin', 'source_capture.md', 'source_capture_api.json', 'source_capture_youtube.info.json'):
                p = Path(folder)/name
                p.write_bytes(crlf)
                self.assertFalse(receipt_artifact_matches(p, hashlib.sha256(lf).hexdigest()))
            p=Path(folder)/'package.json'
            lf=b'{\n  "value": "original"\n}\n'
            p.write_bytes(lf.replace(b'\n',b'\r\n'))
            self.assertTrue(receipt_artifact_matches(p,hashlib.sha256(lf).hexdigest()))
            p.write_bytes(lf.replace(b'original',b'changed'))
            self.assertFalse(receipt_artifact_matches(p,hashlib.sha256(lf).hexdigest()))
            p = Path(folder)/'binary-disguised.md'
            p.write_bytes(b'\x00hello\r\n')
            self.assertFalse(receipt_artifact_matches(p, hashlib.sha256(b'\x00hello\n').hexdigest()))


if __name__ == '__main__': unittest.main()
