import fcntl, pathlib, os, time
class FileLock:
    def __init__(self, path):
        self.path=pathlib.Path(path);self.path.parent.mkdir(parents=True, exist_ok=True);self.fd=None
    def acquire(self, timeout=10):
        self.fd=open(self.path,'w');start=time.time()
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX|fcntl.LOCK_NB);return True
            except BlockingIOError:
                if time.time()-start>timeout: return False
                time.sleep(0.1)
