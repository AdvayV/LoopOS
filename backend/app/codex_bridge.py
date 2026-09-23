"""Local Codex app-server client. Credentials remain managed by Codex."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import queue
import time


class CodexBridge:
    def __init__(self):
        self.process = None
        self.messages = queue.Queue()
        self.counter = 0
        self.lock = threading.Lock()

    def _executable(self):
        executable = shutil.which('codex.exe') or shutil.which('codex')
        # npm Windows shims cannot be started directly with shell=False.
        if executable and Path(executable).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
            root = Path(executable).parent / 'node_modules' / '@openai' / 'codex'
            matches = list(root.glob('node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
            if matches:
                executable = str(matches[0])
        if not executable or Path(executable).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
            raise RuntimeError('Codex executable not found. Install Codex CLI and run codex login.')
        return executable

    def _reader(self, process, messages):
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        messages.put({'closed': True})

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def _next(self, timeout):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('Codex did not respond before the request deadline.') from None
        if message.get('closed'):
            raise RuntimeError('Codex disconnected.')
        if 'method' in message and 'id' in message:
            # Never grant model-initiated command, file, or tool approvals.
            self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Tools are not permitted in Crisis Lab planning.'}})
        return message

    def _request(self, method, params, timeout=25):
        self.counter += 1
        request_id = self.counter
        self._send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._next(max(.1, deadline-time.monotonic()))
            if message.get('id') == request_id:
                if 'error' in message:
                    raise RuntimeError(str(message['error'].get('message', 'Codex request failed')))
                return message['result']
        raise TimeoutError('Codex request timed out.')

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self.messages = queue.Queue()
        self.process = subprocess.Popen([self._executable(), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._reader, args=(self.process, self.messages), daemon=True).start()
        self._request('initialize', {'clientInfo': {'name': 'loopos_crisis_lab', 'version': '0.3.0'}})
        self._send({'method': 'initialized', 'params': {}})

    def status(self):
        with self.lock:
            try:
                self._start()
                result = self._request('account/read', {})
                account = result.get('account') or {}
                signed_in = account.get('type') == 'chatgpt'
                return {'available': True, 'signed_in': signed_in,
                        'message': 'ChatGPT sign-in connected.' if signed_in else 'Run codex login in your terminal, sign in with ChatGPT, then check again.'}
            except Exception as exc:
                return {'available': False, 'signed_in': False, 'message': str(exc)}

    def plan(self, incident, budget, capacity):
        with self.lock:
            self._start()
            account = self._request('account/read', {}).get('account') or {}
            if account.get('type') != 'chatgpt':
                raise RuntimeError('ChatGPT sign-in required. Run codex login; API-key mode is not used.')
            thread = self._request('thread/start', {'approvalPolicy': 'never', 'sandbox': 'read-only',
                'ephemeral': True, 'baseInstructions': 'You plan fictional festival logistics. Do not use tools, read files, or execute commands. Return only the requested JSON.'})
            thread_id = thread['thread']['id']
            prompt = ('Return a JSON object with summary (string), resources (integer quantities for volunteers, generators, rooms), '
                      'cost (integer), steps (1-6 strings), announcement (string). Allocate exactly the minimum requirements '
                      'and minimum cost. These are simulated actions only. No tools. Context: ' + json.dumps({
                          'incident': incident['title'], 'description': incident['description'],
                          'minimum_resources': incident['needs'], 'minimum_cost': incident['cost'],
                          'budget': budget, 'capacity': capacity, 'previous_error': incident['error']}))
            turn = self._request('turn/start', {'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}]})
            turn_id = turn['turn']['id']
            deadline = time.monotonic() + 90
            answer = ''
            try:
                while time.monotonic() < deadline:
                    message = self._next(max(.1, deadline-time.monotonic()))
                    params = message.get('params', {})
                    if params.get('threadId') != thread_id:
                        continue
                    if message.get('method') == 'item/completed':
                        item = params.get('item', {})
                        if item.get('type') == 'agentMessage':
                            answer = item.get('text', '')
                    if message.get('method') == 'turn/completed':
                        if params['turn'].get('status') != 'completed':
                            raise RuntimeError('Codex turn did not complete successfully.')
                        clean = answer.strip()
                        if clean.startswith('```'):
                            clean = '\n'.join(clean.splitlines()[1:-1])
                        return json.loads(clean)
                raise TimeoutError('Codex exceeded the 90-second turn budget.')
            except Exception:
                self._send({'id': self.counter + 1000000, 'method': 'turn/interrupt',
                            'params': {'threadId': thread_id, 'turnId': turn_id}})
                raise

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
