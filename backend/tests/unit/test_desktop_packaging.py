"""The MSI metadata must preserve preview order without changing application SemVer."""
import runpy
from pathlib import Path

import pytest

windows_version = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'scripts/desktop_version.py'))['windows_version']


@pytest.mark.parametrize(('version', 'expected'), [('1.6.0-beta.5', '1.6.0.5'), ('1.6.0', '1.6.0.65535')])
def test_windows_numeric_version(version, expected):
    assert windows_version(version) == expected


@pytest.mark.parametrize('version', ['1.6.0-beta.0', '1.6.0-beta.65535', '256.6.0', '1.256.0', '1.6.65536',
                                      '1.6.0-beta.99999', '1.6.0-rc.1', '../1.6.0', '01.6.0'])
def test_windows_version_rejects_unsupported_or_overflowing_values(version):
    with pytest.raises(ValueError):
        windows_version(version)
