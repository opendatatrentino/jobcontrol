from collections import defaultdict, MutableMapping
from datetime import datetime
from urlparse import urlparse
import io
import json
import linecache
import math
import pickle
import sys

_missing = object()


class cached_property(object):
    """A decorator that converts a function into a lazy property.  The
    function wrapped is called the first time to retrieve the result
    and then that calculated result is used the next time you access
    the value::

        class Foo(object):

            @cached_property
            def foo(self):
                # calculate something important here
                return 42

    The class has to have a `__dict__` in order for this property to
    work.
    """

    # implementation detail: this property is implemented as non-data
    # descriptor.  non-data descriptors are only invoked if there is
    # no entry with the same name in the instance's __dict__.
    # this allows us to completely get rid of the access function call
    # overhead.  If one choses to invoke __get__ by hand the property
    # will still work as expected because the lookup logic is replicated
    # in __get__ for manual invocation.

    def __init__(self, func, name=None, doc=None):
        self.__name__ = name or func.__name__
        self.__module__ = func.__module__
        self.__doc__ = doc or func.__doc__
        self.func = func

    def __get__(self, obj, type=None):
        if obj is None:
            return self
        value = obj.__dict__.get(self.__name__, _missing)
        if value is _missing:
            value = self.func(obj)
            obj.__dict__[self.__name__] = value
        return value


def import_object(name):
    """
    Import an object from a module, by name.

    :param name: The object name, in the ``package.module:name`` format.
    :return: The imported object
    """

    if name.count(':') != 1:
        raise ValueError("Invalid object name: {0!r}. "
                         "Expected format: '<module>:<name>'."
                         .format(name))

    module_name, class_name = name.split(':')
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


STORAGE_ALIASES = {
    'postgresql': 'jobcontrol.ext.postgresql:PostgreSQLStorage',
    'memory': 'jobcontrol.ext.memory:MemoryStorage',
}


def get_storage_from_url(url):
    """
    Get a storage from URL.

    Storages URLs are in the format:

    - ``<scheme>://``
    - ``<class>+<scheme>://`` Load <class>, pass the URL removing ``<class>+``
    """

    # NOTE: We should improve this, as the standard format for
    #       describing imported objects is **not** compatible with the URL
    #       scheme format.

    # TODO: Use stevedore to register / load storage plugins in place
    #       of the dict above.

    parsed = urlparse(url)
    if '+' in parsed.scheme:
        clsname, scheme = parsed.scheme.split('+', 1)
        url = parsed._replace(scheme=scheme).geturl()
    else:
        clsname = scheme = parsed.scheme

    if clsname in STORAGE_ALIASES:
        clsname = STORAGE_ALIASES[clsname]

    storage_class = import_object(clsname)
    return storage_class.from_url(url)


def get_storage_from_config(config):
    """Not implemented yet"""
    raise NotImplementedError('')


def short_repr(obj, maxlen=50):
    """
    Returns a "shortened representation" of an object; that is, the
    return value of ``repr(obj)`` limited to a certain length,
    with a trailing ellipsis ``'...'`` if text was truncated.

    This function is mainly used in order to provide a nice representation
    of local variables in :py:class:`TracebackInfo` objects
    """

    # todo: unify curring with ``trim_string()``

    rep = repr(obj)
    if len(rep) <= maxlen:
        return rep

    # Cut in the middle..
    cutlen = maxlen - 3
    p1 = int(math.ceil(cutlen / 2.0))
    p2 = int(math.floor(cutlen / 2.0))
    return '...'.join((rep[:p1], rep[-p2:]))


def _json_dumps_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()

    raise TypeError('{0!r} is not JSON serializable'.format(obj))


def json_dumps(obj):
    return json.dumps(obj, default=_json_dumps_default)


def trim_string(s, maxlen=1024, ellps='...'):
    """
    Trim a string to a maximum length, adding an "ellipsis"
    indicator if the string was trimmed
    """

    # todo: allow cutting in the middle of the string,
    #       instead of just on the right end..?

    if len(s) > maxlen:
        return s[:maxlen - len(ellps)] + ellps
    return s


class FrameInfo(object):
    def __init__(self, filename, lineno, name, line, locs):
        self.filename = filename
        self.lineno = lineno
        self.name = name
        self.line = line
        self.locs = self._format_locals(locs)
        self.context = self._get_context()

    def _get_context(self, size=5):
        """Return some "context" lines from a file"""
        _start = max(0, self.lineno - size - 1)
        _end = self.lineno + size
        _lines = linecache.getlines(self.filename)[_start:_end]
        _lines = [x.rstrip() for x in _lines]
        _lines = zip(xrange(_start + 1, _end + 1), _lines)
        return _lines

    def _format_locals(self, locs):
        return dict(((k, trim_string(repr(v), maxlen=1024))
                     for k, v in locs.iteritems()))


class TracebackInfo(object):
    """
    Class used to hold information about an error traceback.

    This is meant to be serialized & stored in the database, instead
    of a full traceback object, which is *not* serializable.

    It holds information about:

    - the exception that caused the thing to fail
    - the stack frames (with file / line number, function and exact code
      around the point in which the exception occurred)
    - a representation of the local variables for each frame.

    A textual representation of the traceback information may be
    retrieved by using ``str()`` or ``unicode()`` on the object
    instance.
    """

    def __init__(self):
        self.frames = []

    @classmethod
    def from_current_exc(cls):
        """
        Instantiate with traceback from ``sys.exc_info()``.
        """
        return cls.from_tb(sys.exc_info()[2])

    @classmethod
    def from_tb(cls, tb):
        """
        Instantiate from a traceback object.
        """
        obj = cls()
        obj.frames = cls._extract_tb(tb)
        return obj

    def format(self):
        """Format traceback for printing"""

        output = io.StringIO()
        output.write(u'Traceback (most recent call last):\n\n')
        output.write(u'\n'.join(
            self._format_frame(f)
            for f in self.frames))
        return output.getvalue()

    def format_color(self):
        """Format traceback for printing on 256-color terminal"""

        output = io.StringIO()
        output.write(u'Traceback (most recent call last):\n\n')
        output.write(u'\n'.join(
            self._format_frame_color(f)
            for f in self.frames))
        return output.getvalue()

    def _format_frame(self, frame):
        output = io.StringIO()
        output.write(
            u'  File "{0}", line {1}, in {2}\n'.format(
                frame.filename, frame.lineno, frame.name))

        if frame.context:
            for line in frame.context:
                fmtstring = u'{0:4d}: {1}\n'
                if line[0] == frame.lineno:
                    fmtstring = u'    > ' + fmtstring
                else:
                    fmtstring = u'      ' + fmtstring
                output.write(fmtstring.format(line[0], line[1]))

        if len(frame.locs):
            output.write(u'\n      Local variables:\n')

            for key, val in sorted(frame.locs.iteritems()):
                output.write(u'        {0} = {1}\n'.format(key, val))

        return output.getvalue()

    def _format_frame_color(self, frame):
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name
        from pygments.formatters import Terminal256Formatter

        _code_lexer = get_lexer_by_name('python')
        _code_formatter = Terminal256Formatter(style='monokai')
        _highlight = lambda code: highlight(code, _code_lexer, _code_formatter)

        output = io.StringIO()
        output.write(
            u'  \033[1m'
            u'File \033[38;5;184m"{0}"\033[39m, '
            u'line \033[38;5;70m{1}\033[39m, '
            u'in \033[38;5;39m{2}\033[0m\n\n'
            .format(frame.filename, frame.lineno, frame.name))

        if frame.context:
            for line in frame.context:
                fmtstring = u'{0:4d}: {1}\n'
                if line[0] == frame.lineno:
                    fmtstring = (u'    \033[48;5;250m\033[38;5;232m'
                                 u'{0:4d}\033[0m {1}\n')
                else:
                    fmtstring = (u'    \033[48;5;237m\033[38;5;250m'
                                 u'{0:4d}\033[0m {1}\n')

                color_line = _highlight(line[1])
                output.write(fmtstring.format(line[0], color_line.rstrip()))

        if len(frame.locs):
            output.write(u'\n    \033[1mLocal variables:\033[0m\n')

            for key, val in sorted(frame.locs.iteritems()):
                code_line = _highlight(u'{0} = {1}'.format(key, val)).rstrip()
                output.write(u'      {0}\n'.format(code_line))

        return output.getvalue()

    @classmethod
    def _extract_tb(cls, tb, limit=None):
        if limit is None:
            if hasattr(sys, 'tracebacklimit'):
                limit = sys.tracebacklimit
        frames = []
        n = 0
        while tb is not None and (limit is None or n < limit):
            f = tb.tb_frame
            lineno = tb.tb_lineno
            co = f.f_code
            filename = co.co_filename
            name = co.co_name
            linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, f.f_globals)
            locs = f.f_locals  # Will be converted to repr() by FrameInfo
            if line:
                line = line.strip()
            else:
                line = None
            frames.append(FrameInfo(filename, lineno, name, line, locs))
            tb = tb.tb_next
            n = n+1
        return frames

    # @classmethod
    # def _dump_locals(cls, locs):
    #     return dict(((k, trim_string(repr(v), maxlen=1024))
    #                  for k, v in locs.iteritems()))

    def __str__(self):
        return self.format().encode('utf-8')

    def __unicode__(self):
        return self.format()


class ProgressReport(object):
    """
    Class used to represent progress reports.

    It supports progress reporting on a multi-level "tree" structure;
    each level can have its own progress status, or it will generate
    it automatically by summing up values from children.
    """

    def __init__(self, name, current=None, total=None, status_line=None,
                 children=None):
        self.name = name
        self._current = current
        self._total = total
        self.status_line = status_line
        self.children = []
        if children is not None:
            if not all(isinstance(x, ProgressReport)
                       for x in children):
                raise TypeError(
                    "Progress children must be ProgressReport instances")
            self.children.extend(children)

    @property
    def current(self):
        if self._current is not None:
            return self._current

        return sum(x.current for x in self.children)

    @property
    def total(self):
        if self._total is not None:
            return self._total

        return sum(x.total for x in self.children)

    @property
    def percent(self):
        if self.total == 0:
            return 0.0
        return float(self.current) / self.total

    @property
    def percent_human(self):
        return format(self.percent * 100, '.0f') + '%'

    @property
    def progress_label(self):
        return '{0}/{1} ({2:.0f}%)'.format(
            self.current, self.total, self.percent)

    @property
    def color_css_rgb(self):
        import colorsys

        # todo: use a logarithmic scale to calculate hue?
        #       we want the bar to stay "yellower" up to
        #       "almost done"..
        hue = self.percent * 120  # in degrees

        color = ''.join([
            format(int(x * 255), '02X')
            for x in colorsys.hsv_to_rgb(hue / 360.0, .8, .8)])

        return '#' + color

    @classmethod
    def from_table(cls, table, base_name=None):
        """
        :param table:
            a list of tuples: (name, current, total, status_line).

            - If there is a tuple with ``name == None`` -> use
              as the object's current/total report

            - Find all the "namespaces" and use to build progress
              sub-objects
        """

        root = None
        prefixes = []  # Need to preserve order!

        # For each prefix, build a table with prefix stripped from names
        sub_tables = defaultdict(list)

        for name, current, total, status_line in table:
            if isinstance(name, list):
                name = tuple(name)

            if not (name is None or isinstance(name, tuple)):
                raise TypeError('name must be a tuple (or None)')

            if not name:
                root = (base_name, current, total, status_line)

            else:
                prefix = name[0]
                if prefix not in prefixes:
                    prefixes.append(prefix)
                sub_tables[prefix].append(
                    (name[1:], current, total, status_line))

        if root is None:
            # the root is indefined -- should be guessed!
            obj = cls(base_name)

        else:
            name, current, total, status_line = root  # Explicit!
            obj = cls(name, current, total, status_line)

        # Add children..
        for pref in prefixes:
            obj.children.append(ProgressReport.from_table(
                sub_tables[pref], base_name=pref))

        return obj


class NotSerializableRepr(object):
    def __init__(self, obj, exception=None):
        self.obj = repr(obj)
        self.exception = repr(exception)

    def __repr__(self):
        return ('NotSerializableRepr({0}, exception={1})'
                .format(self.obj, self.exception))

    def __unicode__(self):
        return unicode(self.__repr__())


class ExceptionPlaceholder(object):
    def __init__(self, orig):
        self._repr = repr(orig)
        self._str = unicode(orig)

    def __repr__(self):
        return 'Not serializable exception: {0}'.format(self._repr)

    def __unicode__(self):
        return u'Not serializable exception: {0}'.format(self._str)


class LogRecord(MutableMapping):
    """
    Wrapper around logging messages.

    - Guarantees that the contained object can be pickled
    - Improves things like "created" -> now automatically a datetime object
    - Stores exception / TracebackInfo in separate attributes
    - Uses better field names
    """

    def __init__(self, **kwargs):
        self._attrs = {
            'args': None,
            'created': None,
            'filename': None,
            'function': None,
            'level_name': None,
            'level': None,
            'lineno': None,
            'module': None,
            'msecs': None,
            'message': None,
            'msg': None,
            'name': None,
            'pathname': None,
            'process': None,
            'process_name': None,
            'relative_created': None,
            'thread': None,
            'thread_name': None,

            # Custom
            'build_id': None,
            'exception': None,
            'exception_tb': None,
        }
        self._attrs.update(kwargs)

    @classmethod
    def from_record(cls, record):
        obj = cls()

        if getattr(record, 'message', None) is None:
            record.message = record.getMessage()

        obj.update({
            'args': record.args,
            'created': datetime.utcfromtimestamp(record.created),
            'filename': record.filename,
            'function': record.funcName,
            'level_name': record.levelname,
            'level': record.levelno,
            'lineno': record.lineno,
            'module': record.module,
            'msecs': record.msecs,
            'message': record.message,
            'msg': record.msg,
            'name': record.name,
            'pathname': record.pathname,
            'process': record.process,
            'process_name': record.processName,
            'relative_created': record.relativeCreated,
            'thread': record.thread,
            'thread_name': record.threadName,
            'exception': None,
            'exception_tb': None,
        })

        if record.exc_info:
            et, ex, tb = record.exc_info
            obj['exception'] = ex
            obj['exception_tb'] = TracebackInfo.from_tb(tb)

        return obj

    def __getitem__(self, name):
        aliases = {
            'levelno': 'level',
            'funcName': 'function',
            'levelname': 'level_name',
            'processName': 'process_name',
            'relativeCreated': 'relative_created',
            'threadName': 'thread_name',
        }

        name = aliases.get(name, name)
        return self._attrs[name]

    def __setitem__(self, name, value):
        if name not in self._attrs:
            raise KeyError(name)

        if name == 'exception':
            try:
                pickle.dumps(value)
            except:
                value = ExceptionPlaceholder(value)

        self._attrs[name] = value

    def __delitem__(self, name):
        if name not in self._attrs:
            raise KeyError(name)
        self._attrs[name] = None

    def __iter__(self):
        return iter(self._attrs)

    def __len__(self):
        return len(self._attrs)

    def __contains__(self, item):
        return item in self._attrs

    def __getattr__(self, name):
        """Emulate the standard LogRecord, wich uses attributes"""
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __getstate__(self):
        return self._attrs

    def __setstate__(self, state):
        self._attrs = state
