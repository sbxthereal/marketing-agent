import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pilot.core import load_config, PilotError
from pilot.gateway import read_env
from pilot.platform_support import WorkerLock, chrome_options

ROOT=Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def test_second_process_cannot_own_profile_and_close_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.lock'
            script="from pilot.platform_support import WorkerLock; import sys; lock=WorkerLock(sys.argv[1]); print('acquired'); lock.close()"
            with WorkerLock(path):
                result=subprocess.run([sys.executable,'-c',script,str(path)],cwd=ROOT,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Another pilot process',result.stderr)
            result=subprocess.run([sys.executable,'-c',script,str(path)],cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_crashed_owner_does_not_leave_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.lock'
            script="from pilot.platform_support import WorkerLock; import sys,os; lock=WorkerLock(sys.argv[1]); os._exit(9)"
            result=subprocess.run([sys.executable,'-c',script,str(path)],cwd=ROOT,timeout=10)
            self.assertEqual(result.returncode,9)
            with WorkerLock(path):pass

    def test_windows_lock_uses_one_byte_at_offset_zero(self):
        # Native Windows CI also exercises the real backend in the process tests.
        class CRT:
            LK_NBLCK=2
            calls=[]
            @classmethod
            def locking(cls,fd,mode,length):
                cls.calls.append((os.lseek(fd,0,os.SEEK_CUR),mode,length))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.lock'
            # Patch os.name only after the native Path exists: pathlib dispatches on it.
            with patch('pilot.platform_support.Path',return_value=path),patch('pilot.platform_support.os.name','nt'),patch.dict(sys.modules,{'msvcrt':CRT}):
                with WorkerLock(path):pass
            self.assertEqual(CRT.calls,[(0,2,1)])

    def test_utf8_bom_config_and_env_work_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);config=load_config(ROOT,'config.example.json')
            (path/'config.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8-sig')
            (path/'.env').write_text('ONEAPI_BASE_URL=https://example.test/v1\nONEAPI_API_KEY=example\n',encoding='utf-8-sig')
            with patch.dict(os.environ,{},clear=True):self.assertEqual(read_env(path/'.env')['ONEAPI_API_KEY'],'example')
            self.assertEqual(load_config(path)['source'],'demo')

    def test_browser_headless_is_explicit_and_uses_detected_chrome(self):
        with patch('pilot.platform_support.chrome_executable',return_value='C:/Program Files/Google/Chrome/Application/chrome.exe'):
            self.assertFalse(chrome_options()['headless'])
            self.assertTrue(chrome_options(True)['headless'])
            self.assertIn('executable_path',chrome_options())

    def test_scheduler_log_stream_preserves_lines_and_unicode(self):
        spec=importlib.util.spec_from_file_location('scheduler',ROOT/'windows/runner.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        class Log:
            messages=[]
            def log(self,level,text):self.messages.append(text)
        logger=Log();stream=runner.LogStream(logger,20)
        stream.write('MeDo ');stream.write('你好\nnext');stream.flush()
        self.assertEqual(logger.messages,['MeDo 你好','next'])


@unittest.skipUnless(os.environ.get('PILOT_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'), 'Enable browser integration tests')
class RuntimeBrowserTests(unittest.TestCase):
    def test_actual_runtime_launches_isolated_headless_profile(self):
        script='''
from pathlib import Path
import tempfile
from pilot.browser import BrowserReader
from pilot.core import Store
from pilot.workflow import Workflow
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);flow=Workflow(Store(root/'runtime/state.db'))
    config={'source':'browser','ai':'oneapi','subreddits':['pilottest'],'browser_headless':True,'browser_fallback':False,'max_feed_pages':1,
      'permissions':{'browser_collection':True,'ai_processing':True,'community_rules_reviewed':True,'reference':'LOCAL INTERCEPTED TEST'}}
    reader=BrowserReader(root,config,[{'verified':True}],flow)
    try:
        html='<shreddit-post id="t3_local" post-title="Need a simple website" permalink="/r/pilottest/comments/local/test/"><div slot="text-body">A local fixture</div></shreddit-post>'
        reader.context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=html))
        assert reader.collect()[0]['id']=='t3_local'
        print('runtime browser passed')
    finally:reader.close()
'''
        result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('runtime browser passed',result.stdout)


if __name__=='__main__':unittest.main()
