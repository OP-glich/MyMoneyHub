from pathlib import Path
import shutil
import subprocess

import pytest


def test_local_print_and_copy_handlers():
    node = shutil.which('node')
    if not node: pytest.skip('Node is needed to execute vanilla JavaScript unit tests')
    script = Path(__file__).with_name('frontend_checks.cjs')
    result = subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'print, clipboard, fallback and disclosure passed' in result.stdout
