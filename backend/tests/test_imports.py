"""Os pacotes importam sozinhos, em qualquer ordem, sem depender de quem importou antes."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module", ["comanda.agents", "comanda.runtime", "comanda.runtime.trace", "comanda.chat"]
)
def test_package_imports_in_a_fresh_interpreter(module):
    done = subprocess.run([sys.executable, "-I", "-c", f"import {module}"], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
