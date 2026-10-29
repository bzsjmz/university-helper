"""Cross-platform worker pipes and process options, including frozen executables."""
import os
import selectors
import subprocess
import sys
import time


def worker_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--english-worker']
    return [sys.executable, '-B', '-m', 'app.services.course.english.worker']


def process_options():
    if sys.platform == 'win32':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def windows_environment():
    # Windows and the browser need these OS paths, never the server's secrets.
    names = {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT',
             'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}
    return {key: value for key, value in os.environ.items() if key.upper() in names}


class _WindowsPipeSelector:
    """Windows select() accepts sockets only; PeekNamedPipe keeps reads bounded.

    There is no background reader to consume and discard the next login reply
    when the UI polls the same worker again.
    """
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
        self.peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                              wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        self.peek.restype = wintypes.BOOL
        self.handles = []

    def register(self, stream, events):
        import msvcrt
        self.handles.append(msvcrt.get_osfhandle(stream.fileno()))

    def select(self, timeout=None):
        from ctypes import wintypes
        deadline = time.monotonic() + (timeout if timeout is not None else 1)
        while True:
            for handle in self.handles:
                available = wintypes.DWORD()
                if not self.peek(handle, None, 0, None, self.ctypes.byref(available), None) or available.value:
                    return [(None, None)]  # EOF/errors are readable too.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            time.sleep(min(0.01, remaining))

    def close(self):
        self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def pipe_selector():
    return _WindowsPipeSelector() if sys.platform == 'win32' else selectors.DefaultSelector()
