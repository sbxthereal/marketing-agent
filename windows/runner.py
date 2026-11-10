"""Task Scheduler entry point with rotating UTF-8 logs and normal exit codes."""
import contextlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class LogStream:
    def __init__(self,logger,level):
        self.logger,self.level,self.pending=logger,level,''
    def write(self,text):
        self.pending+=text
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            if line.strip():self.logger.log(self.level,line)
        return len(text)
    def flush(self):
        if self.pending.strip():self.logger.log(self.level,self.pending)
        self.pending=''
    def isatty(self):return False


def main():
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True,mode=0o700)
    logger=logging.getLogger('pilot.scheduler');logger.setLevel(logging.INFO);logger.propagate=False
    handler=RotatingFileHandler(runtime/'worker.log',maxBytes=5*1024*1024,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    out,err=LogStream(logger,logging.INFO),LogStream(logger,logging.ERROR)
    try:
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            from pilot.__main__ import main as run
            sys.argv=['pilot','serve']
            run()
    except SystemExit:
        raise  # Nonzero failures remain visible to Task Scheduler's restart policy.
    except Exception:
        logger.exception('Worker exited unexpectedly')
        raise SystemExit(1)
    finally:
        out.flush();err.flush();handler.close();logger.removeHandler(handler)


if __name__=='__main__':main()
