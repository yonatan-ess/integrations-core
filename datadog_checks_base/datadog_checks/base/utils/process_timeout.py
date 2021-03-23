# (C) Datadog, Inc. 2021-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import functools
from .timeout import TimeoutException
from multiprocessing import Process, Queue
from queue import Empty

class ResultNotAvailableException(Exception):
    pass

class ProcessMethod(Process):
    """
    Descendant of `Process` class.
    Run the specified target method with the specified arguments.
    Store result and exceptions.
    """

    def __init__(self, target, args, kwargs):
        Process.__init__(self)
        self.daemon = True
        self.target, self.args, self.kwargs = target, args, kwargs
        self.__result_queue = Queue(maxsize=1)
        self.start()

    def run(self):
        try:
            self.__result_queue.put_nowait(self.target(*self.args, **self.kwargs))
        except Exception as e:
            self.__result_queue.put_nowait(e)

    @property
    def result(self):
        try:
            return self.__result_queue.get_nowait()
        except Empty as e:
            raise ResultNotAvailableException from e

def timeout(timeout):
    """
    A decorator to timeout a function. Decorated method calls are executed in a separate new process
    with a specified timeout.
    Also checks if a process for the same function already exists before creating a new one.
    Note: Compatible with Windows.
    Note: function result must be pickleable.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            worker = ProcessMethod(func, args, kwargs)
            worker.join(timeout)
            if worker.is_alive():
                worker.terminate()
                raise Exception()

            return worker.result

        return wrapper

    return decorator