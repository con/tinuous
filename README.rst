.. image:: https://github.com/con/tinuous/workflows/Test/badge.svg?branch=master
    :target: https://github.com/con/tinuous/actions?workflow=Test
    :alt: GitHub Actions Status

.. image:: https://travis-ci.com/con/tinuous.svg?branch=master
    :target: https://travis-ci.com/con/tinuous
    :alt: Travis CI Status

.. image:: https://ci.appveyor.com/api/projects/status/github/con/tinuous?branch=master&svg=true
    :target: https://ci.appveyor.com/project/yarikoptic/tinuous/branch/master
    :alt: Appveyor Status

.. image:: https://img.shields.io/pypi/pyversions/tinuous.svg
    :target: https://pypi.org/project/tinuous/

.. image:: https://img.shields.io/github/license/con/tinuous.svg
    :target: https://opensource.org/licenses/MIT
    :alt: MIT License

`GitHub <https://github.com/con/tinuous>`_
| `PyPI <https://pypi.org/project/tinuous/>`_
| `Issues <https://github.com/con/tinuous/issues>`_

``tinuous`` is a command for downloading build logs for a GitHub repository
from GitHub Actions, Travis-CI.com, and/or Appveyor.

Installation
============
``tinuous`` requires Python 3.8 or higher.  Just use `pip
<https://pip.pypa.io>`_ for Python 3 (You have pip, right?) to install
``tinuous`` and its dependencies::

    python3 -m pip install tinuous


Usage
=====

::

    tinuous [<global options>] <command> [<args> ...]


Global Options
--------------

-c FILE, --config FILE          Read configuration from the given file [default
                                value: ``config.yml``]

-l LEVEL, --log-level LEVEL     Set the log level to the given value.  Possible
                                values are "``CRITICAL``", "``ERROR``",
                                "``WARNING``", "``INFO``", "``DEBUG``" (all
                                case-insensitive) and their Python integer
                                equivalents.  [default value: INFO]


``fetch`` Command
-----------------

::

    tinuous [<global options>] fetch [<options>]

``tinuous fetch`` reads a configuration file telling it what repository to
retrieve logs for, where to retrieve them from, and where to save them, and
then it carries those steps out.

Options
~~~~~~~

--sanitize-secrets              Sanitize secrets from log files after
                                downloading

-S FILE, --state FILE           Store program state (e.g., timestamps before
                                which all logs are known to have been fetched)
                                in the given file [default value:
                                ``.dlstate.json``]

``sanitize`` Command
--------------------

::

    tinuous [<global options>] sanitize <path> ...

Sanitize the given files, replacing all strings matching a secret regex with a
series of asterisks.


Configuration
-------------

The configuration file is a YAML file containing a mapping with the following
keys:

``repo``
    The GitHub repository to retrieve logs for, in the form ``OWNER/NAME``

``ci``
    A mapping from the names of the CI systems from which to retrieve logs to
    sub-mappings containing CI-specific configuration.  Including a given CI
    system is optional; logs will be fetched from a given system if & only if
    it is listed in this mapping.

    The CI systems and their sub-mappings are as follows:

    ``github``
        Configuration for retrieving logs from GitHub Actions.  Subfields:

        ``path``
            A template string that will be instantiated for each workflow run
            to produce the path for the directory (relative to the current
            working directory) under which the run's build logs will be saved.
            See "`Path Templates`_" for more information.

        ``workflows``
            A list of the filenames for the workflows for which to retrieve
            logs.  The filenames should only consist of the workflow basenames,
            including the file extension (e.g., ``test.yml``, not
            ``.github/workflows/test.yml``).

    ``travis``
        Configuration for retrieving logs from Travis-CI.com.  Subfield:

        ``path``
            A template string that will be instantiated for each job of each
            build to produce the path for the file (relative to the current
            working directory) in which the job's logs will be saved.  See
            "`Path Templates`_" for more information.

    ``appveyor``
        Configuration for retrieving logs from Appveyor.  Subfields:

        ``path``
            A template string that will be instantiated for each job of each
            build to produce the path for the file (relative to the current
            working directory) in which the job's logs will be saved.  See
            "`Path Templates`_" for more information.

        ``accountName``
            The name of the Appveyor account to which the repository belongs on
            Appveyor

        ``projectSlug``
            *(optional)* The project slug for the repository on Appveyor; if
            not specified, it is assumed that the slug is the same as the
            repository name

``since``
    A timestamp (date, time, & timezone); only logs for builds started after
    the given point in time will be retrieved

    As the script retrieves new build logs, it keeps track of their starting
    points.  Once the logs for all builds for the given CI system &
    configuration have been fetched up to a certain point, the timestamp for
    the latest such build is stored in the state file and used as the new
    ``since`` value for the respective CI system on subsequent runs.

``types``
    A list of build trigger event types; only logs for builds triggered by one
    of the given events will be retrieved

    The recognized event types are:

    ``cron``
        A build run on a schedule

    ``pr``
        A build in response to activity on a pull request

    ``push``
        A build in response to new commits

``secrets``
    *(optional)* A mapping from names (used in log messages) to regexes
    matching secrets to sanitize

``allow-secrets-regex``
    *(optional)* Any strings that match a ``secrets`` regex and also match this
    regex will not be sanitized.  Note that ``allow-secrets-regex`` is tested
    against just the substring that matched a ``secrets`` regex without any
    surrounding text, and so lookahead and lookbehind will not work in this
    regex.

``datalad``
    *(optional)* A sub-mapping describing integration of ``tinuous`` with
    Datalad_.  Subfields:

    ``enabled``
        *(optional)* A boolean.  If true (default false), the current directory
        will be converted into a Datalad dataset if it is not one already,
        the logs will optionally be divided up into subdatasets, and all new
        logs will be committed at the end of a run of ``tinuous fetch``.
        ``path`` template strings may contain ``//`` separators indicating the
        boundaries of subdatasets.

    ``cfg_proc``
        *(optional)* Procedure to run on the dataset & subdatasets when
        creating them

    .. _DataLad: https://www.datalad.org

All fields are required unless stated otherwise.

A sample config file:

.. code:: yaml

    repo: datalad/datalad
    ci:
      github:
        path: '{year}/{month}/{day}/{ci}/{type}/{type_id}/{commit}/{wf_name}/{number}/'
        workflows:
          - test_crippled.yml
          - test_extensions.yml
          - test_macos.yml
      travis:
        path: '{year}/{month}/{day}/{ci}/{type}/{type_id}/{commit}/{number}/{job}.txt'
      appveyor:
        path: '{year}/{month}/{day}/{ci}/{type}/{type_id}/{commit}/{number}/{job}.txt'
        accountName: mih
        projectSlug: datalad
    since: 2021-01-20T00:00:00Z
    types: [cron, pr, push]
    secrets:
      github: '\b(v1\.)?[a-f0-9]{40}\b'
      docker-hub: '\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b'
      appveyor: '\b(v2\.)?[a-z0-9]{20}\b'
      travis: '\b[a-zA-Z0-9]{22}\b'
      aws: '\b[a-zA-Z0-9+/]{40}\b'
    datalad:
      enabled: true
      cfg_proc: text2git


Path Templates
--------------

The path at which logs for a given workflow run or build job are saved is
determined by instantiating the path template string given in the configuration
file for the corresponding CI system.  A template string is a filepath
containing placeholders of the form ``{field}``, where the available
placeholders are:

===================  ==========================================================
Placeholder          Definition
===================  ==========================================================
``{year}``           The four-digit year in which the build was started
``{month}``          The two-digit month in which the build was started
``{day}``            The two-digit day in which the build was started
``{hour}``           The two-digit hour at which the build was started
``{minute}``         The two-digit minute at which the build was started
``{second}``         The two-digit second at which the build was started
``{ci}``             The name of the CI system (``github``, ``travis``, or
                     ``appveyor``)
``{type}``           The event type that triggered the build (``cron``, ``pr``,
                     or ``push``)
``{type_id}``        Further information on the triggering event; for ``cron``,
                     this is a timestamp for the start of the build; for
                     ``pr``, this is the number of the associated pull request,
                     or ``UNK`` if it cannot be determined; for ``push``, this
                     is the name of the branch to which the push was made (or
                     possibly the tag that was pushed, if using Appveyor)
``{commit}``         The hash of the commit the build ran against
``{number}``         The run number of the workflow run (GitHub) or the build
                     number (Travis and Appveyor)
``{status}``         The success status of the workflow run (GitHub) or job
                     (Travis and Appveyor); the exact strings used depend on
                     the CI system
``{common_status}``  The success status of the workflow run or job, normalized
                     into one of ``success``, ``failed``, ``errored``, or
                     ``incomplete``
``{wf_name}``        *(GitHub only)* The name of the workflow
``{wf_file}``        *(GitHub only)* The basename of the workflow file
                     (including the file extension)
``{run_id}``         *(GitHub only)* The unique ID of the workflow run
``{job}``            *(Travis and Appveyor only)* The number of the job,
                     without the build number prefix (Travis) or the job ID
                     string (Appveyor)
===================  ==========================================================

All timestamps and timestamp components are in UTC.

Authentication
--------------

GitHub
~~~~~~

In order to retrieve logs from GitHub, a GitHub OAuth token must be specified
either via the ``GITHUB_TOKEN`` environment variable or as the value of the
``hub.oauthtoken`` Git config option.

Travis
~~~~~~

In order to retrieve logs from Travis, a Travis API access token must be either
specified via the ``TRAVIS_TOKEN`` environment variable or be retrievable by
running ``travis token --com --no-interactive``.

A Travis API access token can be acquired as follows:

- Install the `Travis command line client
  <https://github.com/travis-ci/travis.rb>`_.

- Run ``travis login --com`` to authenticate.

  - If your Travis account is linked to your GitHub account, you can
    authenticate by running ``travis login --com --github-token
    $GITHUB_TOKEN``.

- If the script will be run on the same machine that the above steps are
  carried out on, you can stop here, and the script will retrieve the token
  directly from the ``travis`` command.

- Run ``travis token --com`` to retrieve the API access token.

Appveyor
~~~~~~~~

In order to retrieve logs from Appveyor, an Appveyor API key (for either all
accessible accounts or just the specific account associated with the
repository) must be specified via the ``APPVEYOR_TOKEN`` environment variable.
Such a key can be obtained at <https://ci.appveyor.com/api-keys>.
