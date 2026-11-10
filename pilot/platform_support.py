"""Small Windows/POSIX boundary; runtime AI remains in the OneAPI gateway."""
import errno
import os
from pathlib import Path
import shutil
import sys

from .core import PilotError


class WorkerLock:
    """Own one runtime/profile across processes; the OS releases it on exit."""
    def __init__(self, path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.file=path.open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                # Windows byte-range locks need a real byte and a stable offset.
                self.file.seek(0,os.SEEK_END)
                if self.file.tell()==0:
                    self.file.write(b'0');self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            self.file.close();self.file=None
            if error.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):
                raise PilotError('Another pilot process already owns this runtime; stop it before using the same Chrome profile') from None
            raise

    def close(self):
        if self.file is not None:
            # Closing the handle releases the OS lock; never unlink the lock file.
            self.file.close();self.file=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def chrome_executable():
    if sys.platform=='darwin':
        candidates=[Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),Path.home()/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    elif os.name=='nt':
        candidates=[Path(os.environ[key])/'Google/Chrome/Application/chrome.exe' for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA') if os.environ.get(key)]
    else:
        found=shutil.which('google-chrome') or shutil.which('google-chrome-stable')
        candidates=[Path(found)] if found else []
    return next((str(p) for p in candidates if p.is_file()),None)


def chrome_options(headless=False):
    path=chrome_executable()
    return dict(headless=headless,**({'executable_path':path} if path else {'channel':'chrome'}))
