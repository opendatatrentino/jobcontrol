"""
Objects to manage the configuration.

The configuration object (stored as YAML in the configuration file)
must be a dict. Supported keys for the "main" dict are:

- storage: URL to a supported "state" storage
- webapp: Configuration for the webapp, passed to Flask
- celery: Configuration for celery
- jobs: List of job configuration blocks
- secret: Dictionary of "secrets", which can be referenced by the configuration
  but are never shown on administration pages, ...
"""

from collections import Mapping, MutableMapping

import yaml

from jobcontrol.utils import get_storage_from_url


class JobControlConfig(object):
    def __init__(self, initial=None):
        # todo: set default values here...
        self._storage = None
        self._webapp = {}
        self._celery = {}
        self._jobs = []
        self._secret = {}
        self._yaml_config = None

        if initial is not None:
            self._update(initial)

    @classmethod
    def from_file(cls, filename):
        """
        Initialize configuration from a file, or a file-like providing
        a ``read()`` method.
        """

        if isinstance(filename, basestring):
            with open(filename, 'r') as fp:
                return cls.from_string(fp.read())

        if hasattr(filename, 'read'):
            return cls.from_string(filename.read())

        raise TypeError('filename must be a string or a file-like object')

    @classmethod
    def from_string(cls, s):
        """
        Initialize configuration from a string.

        The string will first be pre-processed through jinja, then
        passed to the :py:meth:`from_object` constructor.
        """

        conf = cls.preprocess_config(s)
        conf_obj = _yaml_load(conf)
        obj = cls(conf_obj)
        obj._yaml_config = conf
        return obj

    @staticmethod
    def preprocess_config(s):
        import jinja2
        return jinja2.Template(s).render()

    def _update(self, data):
        if not isinstance(data, dict):
            raise TypeError('data must be a dict')

        if 'storage' in data:
            if not isinstance(data['storage'], basestring):
                raise TypeError('storage must be a string')
            self._storage = data['storage']

        if 'jobs' in data:
            self._jobs.extend(BuildConfig(x) for x in data['jobs'])
            self._validate_jobs(self._jobs)

        if 'webapp' in data:
            self._webapp.update(data['webapp'])

        if 'celery' in data:
            self._celery.update(data['celery'])

        if 'secret' in data:
            self._secret.update(data['secret'])

    def _validate_jobs(self, jobs):
        used_ids = set()
        for job in jobs:
            if job.get('id') is None:
                raise TypeError('Job id cannot be None')
            if job['id'] in used_ids:
                raise ValueError('Duplicate job id: {0}'.format(job['id']))
            used_ids.add(job['id'])

    @property
    def storage(self):
        return self._storage

    @property
    def jobs(self):
        return self._jobs

    @property
    def webapp(self):
        return self._webapp

    @property
    def celery(self):
        return self._celery

    @property
    def secret(self):
        return self._secret

    def get_storage(self):
        if self.storage is None:
            return None
        return get_storage_from_url(self.storage)

    def get_job_config(self, job_id):
        for job in self.jobs:
            if job['id'] == job_id:
                return job

    get_job = get_job_config

    def get_job_deps(self, job_id):
        job = self.get_job_config(job_id)
        if job is None:
            # For coherence with get_job_revdeps()
            return []
        return job.get('dependencies', [])

    def get_job_revdeps(self, job_id):
        jobs = []
        for job in self.jobs:
            if job_id in job.get('dependencies', []):
                jobs.append(job['id'])
        return jobs

    def __eq__(self, other):
        """Comparison, used mostly for testing"""
        if type(other) is not type(self):
            return False
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self.__eq__(other)


class BuildConfig(MutableMapping):
    """
    Dict-like object used to hold a build configuration.

    Provides some validation on the passed-in configuration
    values, plus default values for missing items.

    Supported keys are:

    - ``function``: a string representing the function to be called,
      in the format: ``"module:function"``.
    - ``args``: a tuple of positional arguments to be passed to
      the function.
    - ``kwargs``: a dictionary holding keyword arguments to be passed
      to the function.
    - ``dependencies``: a list of "dependency" job ids.
    - ``pinned_builds``: a dictionary mapping dependency job ids to
      the "selected" dependency build. This way we can ensure consistency
    - ``title``, ``notes``: descriptive fields, shown on the interfaces
    - ``protected``: boolean flag indicating whether this job should be
      "protected", i.e. extra care should be taken before running it.
    - ``cleanup_function``: a function to be called in order to delete
      the output result for this job. It will be passed the ``BuildInfo``
      object as only argument.
    - ``repr_function``: function to be used to represent the return value
      of this build. It will be passed the ``BuildInfo`` object as only
      argument; return values can be retrieved from the ``build.retval``
      argument, configuration from ``build.app.config``.
    """

    def __init__(self, initial=None):
        self._config = {}
        if initial is not None:
            if not isinstance(initial, (dict, Mapping)):
                raise TypeError('initial must be a dict, got {0} instead'
                                .format(type(initial).__name__))
            self.update(initial)

    def __repr__(self):
        return '{0}({1})'.format(self.__class__.__name__, repr(self._config))

    def __getitem__(self, name):
        if name in ('function', 'cleanup_function'):
            return self._config.get(name)

        if name == 'args':
            return tuple(self._config.get(name) or ())

        if name == 'kwargs':
            return self._config.get(name) or {}

        if name == 'dependencies':
            return list(self._config.get(name) or [])

        if name == 'pinned_builds':
            return self._config.get(name, {})

        # Other "common" fields which should always have a default value
        if name in ('title', 'notes'):
            return self._config.get(name)

        # The "protected" flag -> defaults to false
        if name == 'protected':
            return self._config.get(name, False)

        # These are optional
        if name in ('cleanup_function', 'repr_function'):
            return self._config.get(name, None)

        return self._config[name]

    def __setitem__(self, name, value):
        if name == 'function' and not isinstance(value, str):
            raise TypeError('Function must be a string, got {0} instead'
                            .format(type(value).__name__))

        if name == 'args':
            if isinstance(value, list):
                value = tuple(value)
            if not isinstance(value, tuple):
                raise TypeError('args must be a tuple, got {0} instead'
                                .format(type(value).__name__))

        if name == 'kwargs' and not isinstance(value, dict):
            raise TypeError('kwargs must be a dict, got {0} instead'
                            .format(type(value).__name__))

        if name == 'dependencies':
            if isinstance(value, tuple):
                value = list(value)
            if not isinstance(value, list):
                raise TypeError('{0} must be a list, got {1} instead'
                                .format(name, type(value).__name__))

        if name == 'pinned_builds':
            if not isinstance(value, dict):
                raise TypeError('{0} must be a dict, got {1} instead'
                                .format(name, type(value).__name__))

        if name in ('title', 'notes'):
            if not isinstance(value, basestring):
                raise TypeError('{0} must be a string, got {1} instead'
                                .format(name, type(value).__name__))
            if isinstance(value, str):
                value = unicode(value, encoding='utf-8')

        if name == 'protected':
            if not isinstance(value, bool):
                raise TypeError('{0} must be a boolean, got {1} instead'
                                .format(name, type(value).__name__))

        if name in ('cleanup_function', 'repr_function'):
            if not isinstance(value, str):
                raise TypeError('{0} must be a string, got {1} instead'
                                .format(name, type(value).__name__))

        self._config[name] = value

    def __delitem__(self, name):
        del self._config[name]

    def __iter__(self):
        return iter(self._config)

    def __len__(self):
        return len(self._config)

    def __getstate__(self):
        return self._config

    def __setstate__(self, state):
        self._config = state

    def __eq__(self, other):
        """Comparison, used mostly for testing"""
        if type(other) is not type(self):
            return False
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self.__eq__(other)


class Retval(object):
    """Placeholder object for ``!retval <n>``"""

    def __init__(self, job_id):
        if not isinstance(job_id, basestring):
            raise TypeError("Job id must be a string")
        self.job_id = job_id

    def __repr__(self):
        return 'Retval({0!r})'.format(self.job_id)

    def __eq__(self, other):
        if type(self) != type(other):
            return False

        return self.job_id == other.job_id

    def __ne__(self, other):
        return not self.__eq__(other)


def _yaml_dump(data):
    class CustomDumper(yaml.Dumper):
        pass

    CustomDumper.add_representer(
        Retval,
        lambda dumper, data: dumper.represent_scalar(
            u'!retval', value=unicode(data.job_id)))

    return yaml.dump_all([data], Dumper=CustomDumper,
                         default_flow_style=False)


def _yaml_load(stream):
    class CustomLoader(yaml.Loader):
        pass

    CustomLoader.add_constructor(
        u'!retval',
        lambda loader, data: Retval(loader.construct_scalar(data)))

    return yaml.load(stream, Loader=CustomLoader)
