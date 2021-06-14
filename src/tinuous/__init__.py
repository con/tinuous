"""
Download build logs from GitHub Actions, Travis, and Appveyor

``tinuous`` is a command for downloading build logs and (for GitHub
only) artifacts & release assets for a GitHub repository from GitHub Actions,
Travis-CI.com, and/or Appveyor.

Visit <https://github.com/con/tinuous> for more information.
"""

__author__ = "Center for Open Neuroscience"
__author_email__ = "debian@onerussian.com"
__maintainer__ = "John T. Wodder II"
__maintainer_email__ = "tinuous@varonathe.org"
__license__ = "MIT"
__url__ = "https://github.com/con/tinuous"

from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions
