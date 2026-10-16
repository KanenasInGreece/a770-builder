"""Keep an ambient registry path out of the suite.

The transcription file outside this checkout is not a shipped registry. A shell
that points A770B_PROFILES_FILE at it must not make env.sh refuse the tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_profiles_file(monkeypatch):
    # An empty export wins over ~/.config/a770-builder/builder.env. env.sh then
    # resolves the card's shipped registry. A test that names a file sets it itself.
    monkeypatch.setenv("A770B_PROFILES_FILE", "")
