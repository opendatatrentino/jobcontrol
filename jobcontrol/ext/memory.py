"""
PostgreSQL-backed Job control class.

.. warning::

    This class is (currently) **not** thread-safe!

    Thread-safety will be added in the future (includes storing the current
    job-control app in a thread-local object, filtering logs by thread id,
    etc.)
"""

from collections import defaultdict
from datetime import datetime
from itertools import count
import json
import logging

from jobcontrol.base import JobControlBase, JobDefinition


class MemoryJobControl(JobControlBase):
    def install(self):
        self._job = {}
        self._job_run = {}
        # job_run_log[job_id][job_run_id] = [...messages...]
        self._job_run_log = defaultdict(lambda: defaultdict(list))
        self._job_seq = count(1)
        self._job_run_seq = count(1)

    def uninstall(self):
        self.install()  # Just flush dicts..

    def define_job(self, function, args, kwargs, dependencies=None):
        _job_id = self._job_seq.next()
        self._job[_job_id] = dict(function=function,
                                  args=args,
                                  kwargs=kwargs,
                                  dependencies=dependencies or [])
        return _job_id

    def get_job_definition(self, job_id):
        return self._job[job_id]

    def undefine_job(self, job_id):
        self._job.pop(job_id, None)

    def iter_jobs(self):
        for jobid, jobdef in sorted(self._job.iteritems()):
            yield JobDefinition(app=self, row=jobdef)

    def create_log_handler(self, job_id, job_run_id):
        handler = MemoryLogHandler(self._job_run_log[job_id][job_run_id])
        return handler

    def create_job_run(self, job_id):
        _job_run_id = self._job_run_seq.next()
        self._job_run[_job_run_id] = {
            'job_id': job_id,
            'start_time': datetime.now(),
        }
        return _job_run_id

    def update_job_run(self, job_run_id, finished=None, success=None,
                       progress_current=None, progress_total=None,
                       retval=None):

        data = {'id': job_run_id}
        if finished is not None:
            data['finished'] = finished

        if finished:
            data['end_time'] = datetime.now()
            data['retval'] = json.dumps(retval)

        if success is not None:
            data['success'] = success

        if progress_current is not None:
            data['progress_current'] = progress_current

        if progress_total is not None:
            data['progress_total'] = progress_total

        self._job_run[job_run_id].update(data)

    def delete_job_run(self, job_run_id):
        _def = self._job_run[job_run_id]
        self._job_run.pop(job_run_id, None)
        self._job_run_log[_def['job_id']].pop(job_run_id, None)


HOUR = 3600
DAY = 24 * HOUR
MONTH = 30 * DAY


class MemoryLogHandler(logging.Handler):
    """In-memory log handler, for testing purposes"""

    log_retention_policy = {
        logging.DEBUG: 15 * DAY,
        logging.INFO: MONTH,
        logging.WARNING: 3 * MONTH,
        logging.ERROR: 6 * MONTH,
        logging.CRITICAL: 6 * MONTH,
    }
    log_max_retention = 12 * MONTH

    def __init__(self, destination, extra_info=None):
        super(MemoryLogHandler, self).__init__()
        self._destination = destination
        self.extra_info = extra_info

    def flush(self):
        pass  # Nothing to flush!

    def serialize(self, record):
        import traceback

        record_dict = {
            'args': json.dumps(record.args),
            'created': record.created,
            'filename': record.filename,
            'funcName': record.funcName,
            'levelname': record.levelname,
            'levelno': record.levelno,
            'lineno': record.lineno,
            'module': record.module,
            'msecs': record.msecs,
            'msg': record.msg,
            'name': record.name,
            'pathname': record.pathname,
            'process': record.process,
            'processName': record.processName,
            'relativeCreated': record.relativeCreated,
            'thread': record.thread,
            'threadName': record.threadName}

        if record.exc_info is not None:
            # We cannot serialize exception information.
            # The best workaround here is to simply add the
            # relevant information to the message, as the
            # formatter would..
            exc_class = u'{0}.{1}'.format(
                record.exc_info[0].__module__,
                record.exc_info[0].__name__)
            exc_message = str(record.exc_info[1])
            exc_repr = repr(record.exc_info[1])
            exc_traceback = '\n'.join(
                traceback.format_exception(*record.exc_info))

            # record_dict['_orig_msg'] = record_dict['msg']
            # record_dict['msg'] += "\n\n"
            # record_dict['msg'] += exc_traceback
            record_dict['exc_class'] = exc_class
            record_dict['exc_message'] = exc_message
            record_dict['exc_repr'] = exc_repr
            record_dict['exc_traceback'] = exc_traceback

        return record_dict

    def emit(self, record):
        """Handle a received log message"""

        data = self.serialize(record)
        data.update(self.extra_info)
        self._destination.append(data)

    def cleanup_old_messages(self):
        """Delete old log messages, according to log retention policy"""

        # todo: write this..
        pass