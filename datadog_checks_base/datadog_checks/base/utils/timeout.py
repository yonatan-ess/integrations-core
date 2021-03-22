# (C) Datadog, Inc. 2018-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import functools
from multiprocessing import Process, Queue
from threading import Thread

_process_by_func = {}


class TimeoutException(Exception):
    """
    Raised when a function runtime exceeds the limit set.
    """

    pass


class ThreadMethod(Thread):
    """
    Deprecated: Please use ProcessMethod instead
    Descendant of `Thread` class.
    Run the specified target method with the specified arguments.
    Store result and exceptions.
    From: https://code.activestate.com/recipes/440569/
    """

    def __init__(self, target, args, kwargs):
        Thread.__init__(self)
        self.setDaemon(True)
        self.target, self.args, self.kwargs = target, args, kwargs
        self.start()

    def run(self):
        try:
            self.result = self.target(*self.args, **self.kwargs)
        except Exception as e:
            self.exception = e
        else:
            self.exception = None


class ProcessMethod(Process):
    """
    Descendant of `Process` class.
    Run the specified target method with the specified arguments.
    Store result and exceptions.
    Modified from: https://code.activestate.com/recipes/440569/
    """

    def __init__(self, target, args, kwargs):
        Process.__init__(self)
        self.daemon = True
        self.target, self.args, self.kwargs = target, args, kwargs
        self.q = Queue()
        self.start()

    def run(self):
        try:
            self.q.put_nowait(self.target(*self.args, **self.kwargs))
        except Exception as e:
            self.q.put_nowait(e)


def timeout(timeout):
    """
    A decorator to timeout a function. Decorated method calls are executed in a separate new process
    with a specified timeout.
    Also checks if a process for the same function already exists before creating a new one.
    Note: Compatible with Windows.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = "{0}:{1}:{2}:{3}".format(id(func), func.__name__, args, kwargs)

            if key in _process_by_func:
                # A process for the same function already exists.
                worker = _process_by_func[key]
            else:
                worker = ProcessMethod(func, args, kwargs)
                _process_by_func[key] = worker

            worker.join(timeout)
            if worker.is_alive():
                worker.terminate()
                raise TimeoutException()

            del _process_by_func[key]
            return worker.q.get_nowait()

        return wrapper

    return decorator
