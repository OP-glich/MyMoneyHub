"""Black-box HTTP driver: submits controls from actual rendered HTML forms."""
from contextlib import contextmanager, closing
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.forms = []
        self.form = None
        self.select = None
        self.option = None
        self.links = []
        self.text = []
        self.metrics = {}
        self.captures = []
        self.textareas = {}
        self.textarea = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('data-metric') or attrs.get('data-loan'):
            self.captures.append([attrs.get('data-metric') or attrs['data-loan'], tag, []])
        if tag == 'textarea' and attrs.get('id'):
            self.textarea = attrs['id']
            self.textareas[self.textarea] = ''
        if tag == 'form':
            self.form = dict(attrs, controls=[])
            self.forms.append(self.form)
        if tag == 'a': self.links.append(attrs)
        if self.form is not None and tag in ('input','select','textarea','button'):
            control = dict(attrs, tag=tag)
            self.form['controls'].append(control)
            if tag == 'select':
                control['options'] = []
                self.select = control
        if tag == 'option' and self.select is not None:
            self.option = dict(attrs, text='')
            self.select['options'].append(self.option)

    def handle_endtag(self, tag):
        if self.captures and self.captures[-1][1] == tag:
            key,_,chunks=self.captures.pop()
            self.metrics.setdefault(key,[]).append(''.join(chunks).strip())
        if tag == 'textarea': self.textarea = None
        if tag == 'form': self.form = None
        if tag == 'select': self.select = None
        if tag == 'option': self.option = None

    def handle_data(self, text):
        if text.strip(): self.text.append(text.strip())
        for _,_,chunks in self.captures: chunks.append(text)
        if self.textarea: self.textareas[self.textarea] += text
        if self.option is not None: self.option['text'] += text

    def find_form(self, action, **matching):
        candidates = []
        for form in self.forms:
            if form.get('action','') != action: continue
            values = self.values(form)
            if all(values.get(k) == str(v) for k,v in matching.items()): candidates.append(form)
        assert len(candidates) == 1, f'{action}: expected one matching visible form, got {len(candidates)}'
        return candidates[0]

    def metric(self, name):
        values=self.metrics.get(name,[])
        assert len(values)==1,f'Expected one visible {name} value, got {values}'
        return values[0]

    @staticmethod
    def values(form):
        result = {}
        for c in form['controls']:
            name = c.get('name')
            if not name or 'disabled' in c or c['tag'] == 'button': continue
            if c.get('type') in ('checkbox','radio') and 'checked' not in c: continue
            if c['tag'] == 'select':
                options = c['options']
                selected = next((o for o in options if 'selected' in o), options[0] if options else {})
                result[name] = selected.get('value', selected.get('text',''))
            else: result[name] = c.get('value','')
        return result


class Browser:
    def __init__(self, base):
        self.base = base
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.requests = []
        self.last_post = None

    def get(self, path):
        with self.client.open(self.base+path,timeout=10) as response:
            body=response.read()
            self.requests.append(('GET',path,response.status,response.url))
            return body

    def page(self, path):
        return Page(self.get(path).decode('utf-8'))

    def download(self, path):
        with self.client.open(self.base+path,timeout=15) as response:
            self.requests.append(('GET',path,response.status,response.url))
            return response.read(),dict(response.headers)

    def submit(self, page, action, values=None, matching=None, validate=True, submitter=None):
        form=page.find_form(action,**(matching or {}))
        fields=page.values(form)
        allowed={c.get('name') for c in form['controls']}
        assert set(values or {}) <= allowed, 'Attempt to post a field absent from the real form'
        fields.update({k:str(v) for k,v in (values or {}).items()})
        if submitter:
            assert any(c.get('formaction') == submitter for c in form['controls']), 'Submit button not present'
            action=submitter
        if validate:
            missing=[c.get('name') for c in form['controls'] if 'required' in c and c.get('name') and not fields.get(c['name'])]
            assert not missing, 'Browser validation prevents submission: '+','.join(missing)
        self.last_post=(action or '/login',urlencode(fields).encode())
        return self.repeat()

    def repeat(self):
        action,data=self.last_post
        try:
            with self.client.open(self.base+action,data=data,timeout=15) as response:
                body=response.read()
                self.requests.append(('POST',action,response.status,response.url))
                return Page(body.decode('utf-8'))
        except HTTPError as response:
            self.requests.append(('POST',action,response.code,response.url))
            return Page(response.read().decode('utf-8'))

    def signup(self):
        page=self.page('/login')
        return self.submit(page,'',dict(password='UserWorkflowPassword!',confirm='UserWorkflowPassword!'))

    def signin(self):
        page=self.page('/login')
        return self.submit(page,'',dict(password='UserWorkflowPassword!'))


@contextmanager
def live_app(root, launcher=False):
    """Real process, real authentication, own isolated persistent database."""
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    with closing(socket.socket()) as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    project=Path(__file__).resolve().parents[1]
    env=dict(os.environ, MYMONEYHUB_DATA_DIR=str(root),PYTHONUNBUFFERED='1',
             MYMONEYHUB_NO_BROWSER='1',MYMONEYHUB_PORT=str(port))
    command=[sys.executable,'run.py','--no-browser','--port',str(port)]
    if launcher: command=['cmd.exe','/c','Start MyMoney Hub.cmd']
    with (root/'http-server.log').open('ab') as output:
        proc=subprocess.Popen(command,cwd=project,env=env,stdout=output,stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        job=None
        if launcher and os.name=='nt':
            # Own the launcher's descendants without taskkill, which is denied
            # in restricted Windows environments. Never target other servers.
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype=wintypes.HANDLE
            kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            job=kernel.CreateJobObjectW(None,None)
            assert job and kernel.AssignProcessToJobObject(job,int(proc._handle)),ctypes.get_last_error()
        try:
            browser=Browser(f'http://127.0.0.1:{port}')
            deadline=time.monotonic()+20
            while True:
                assert proc.poll() is None,(root/'http-server.log').read_text(errors='replace')
                try:
                    browser.get('/login')
                    break
                except (URLError,TimeoutError):
                    if time.monotonic()>deadline: raise AssertionError('Local server did not start')
                    time.sleep(.1)
            yield browser
        finally:
            if proc.poll() is None:
                if launcher and os.name=='nt':
                    assert kernel.TerminateJobObject(job,0),ctypes.get_last_error()
                else: proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                # Termination can return before a descendant releases its file
                # handles. Wait for our test server's actual database lock.
                if launcher and os.name=='nt':
                    import msvcrt
                    deadline=time.monotonic()+10
                    while True:
                        try:
                            with (root/'application.lock').open('r+b') as lock:
                                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                                lock.seek(0)
                                msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                            break
                        except OSError:
                            if time.monotonic()>deadline:
                                raise AssertionError('Owned launcher test server did not release its lock')
                            time.sleep(.1)
            if job:
                kernel.CloseHandle(job)
