"""The desktop build leaves out Pillow and pypdfium2 (packaging/Jobapplier.spec): pdfplumber pulls them in
only to render pages as images, which the app never does. These checks fail if a change starts needing them."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

SCRIPT = r'''
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("PIL", "pypdfium2", "pypdfium2_raw"):
            raise ImportError("left out of the desktop build: " + name)
sys.meta_path.insert(0, Block())
from pathlib import Path
from applypilot.scoring import docx_fill, style_match
from applypilot import own_docs
fixtures = Path(sys.argv[1])
style = style_match.extract(fixtures / "styled_resume.pdf")
assert style and style_match.resume_css(style)
assert own_docs.extract_text(fixtures / "styled_resume.pdf").strip()
assert own_docs.extract_text(fixtures / "word_resume.docx").strip()
docx_fill.check_docx((fixtures / "word_resume.docx").read_bytes())
print("ok")
'''


def test_resume_files_are_read_without_pillow_or_pypdfium2():
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(FIXTURES)], capture_output=True, text=True,
                         cwd=ROOT, timeout=120)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-2000:]


def test_the_build_leaves_them_out():
    spec = (ROOT / "packaging" / "Jobapplier.spec").read_text(encoding="utf-8")
    for name in ('"PIL"', '"pypdfium2"', '"pypdfium2_raw"'):
        assert name in spec
