# Copyright 2026 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run eval suite.

The run.py module is used to run a suite of tasks, with configurable task
combinations, environment setups, and agent configurations. You can run specific
tasks or all tasks in the suite and customize various settings using the
command-line flags.
"""

from collections.abc import Sequence
import datetime
import json
import os
import pickle
import subprocess

from absl import app
from absl import flags
from absl import logging
from android_world.env import env_launcher

logging.set_verbosity(logging.WARNING)

os.environ['GRPC_VERBOSITY'] = 'ERROR'  # Only show errors
os.environ['GRPC_TRACE'] = 'none'  # Disable tracing


def _find_adb_directory() -> str:
  """Returns the directory where adb is located."""
  potential_paths = [
      '/usr/bin/adb',
      os.path.expanduser('~/Library/Android/sdk/platform-tools/adb'),
      os.path.expanduser('~/Android/Sdk/platform-tools/adb'),
  ]
  for path in potential_paths:
    if os.path.isfile(path):
      return path
  return 'adb'


_ADB_PATH = flags.DEFINE_string(
    'adb_path',
    _find_adb_directory(),
    'Path to adb. Set if not installed through SDK.',
)
_EMULATOR_SETUP = flags.DEFINE_boolean(
    'perform_emulator_setup',
    False,
    'Whether to perform emulator setup. This must be done once and only once'
    ' before running Android World. After an emulator is setup, this flag'
    ' should always be False.',
)
_DEVICE_CONSOLE_PORT = flags.DEFINE_integer(
    'console_port',
    5554,
    'The console port of the running Android device. This can usually be'
    ' retrieved by looking at the output of `adb devices`. In general, the'
    ' first connected device is port 5554, the second is 5556, and'
    ' so on.',
)

_SUITE_FAMILY = flags.DEFINE_enum(
    'suite_family',
    'android_world',
    [
        'android_world',
        'miniwob_subset',
        'miniwob',
        'android',
        'information_retrieval',
    ],
    'Suite family to run. See registry.py for more information.',
)
_TASK_RANDOM_SEED = flags.DEFINE_integer(
    'task_random_seed', 30, 'Random seed for task randomness.'
)

_TASKS = flags.DEFINE_list(
    'tasks',
    None,
    'List of specific tasks to run in the given suite family. If None, run all'
    ' tasks in the suite family.',
)
_N_TASK_COMBINATIONS = flags.DEFINE_integer(
    'n_task_combinations',
    1,
    'Number of task instances to run for each task template.',
)

_CHECKPOINT_DIR = flags.DEFINE_string(
    'checkpoint_dir',
    '',
    'The directory to save checkpoints and resume evaluation from. If the'
    ' directory contains existing checkpoint files, evaluation will resume from'
    ' the latest checkpoint. If the directory is empty or does not exist, a new'
    ' directory will be created.',
)
_OUTPUT_PATH = flags.DEFINE_string(
    'output_path',
    os.path.expanduser('~/android_world/runs'),
    'The path to save results to if not resuming from a checkpoint is not'
    ' provided.',
)

# Agent specific.
_AGENT_NAME = flags.DEFINE_string('agent_name', 'm3a_gpt4v', help='Agent name.')

_FIXED_TASK_SEED = flags.DEFINE_boolean(
    'fixed_task_seed',
    False,
    'Whether to use the same task seed when running multiple task combinations'
    ' (n_task_combinations > 1).',
)
_ADB_ONLY = flags.DEFINE_boolean(
    'adb_only',
    False,
    'Run adb-only mode without emulator gRPC.',
)
_ADB_SERIAL = flags.DEFINE_string(
    'adb_serial',
    '127.0.0.1:15500',
    'ADB serial for adb-only mode.',
)
_ADB_ONLY_AGENT = flags.DEFINE_enum(
    'adb_only_agent',
    'script',
    ['script', 'm3a'],
    'Agent for adb-only mode.',
)
_OPENAI_MODEL = flags.DEFINE_string(
    'openai_model',
    'gpt-4o-2024-11-20',
    'Model for m3a adb-only mode.',
)
_OPENAI_BASE_URL = flags.DEFINE_string(
    'openai_base_url',
    '',
    'Optional OpenAI-compatible base url, e.g. https://xxx/v1',
)
_OPENAI_API_KEY = flags.DEFINE_string(
    'openai_api_key',
    '',
    'Optional OpenAI API key. If empty, use OPENAI_API_KEY env var.',
)
_RECORD_STEPS = flags.DEFINE_boolean(
    'record_steps',
    False,
    'Record per-step artifacts/actions/model outputs in adb-only m3a mode.',
)
_RECORD_STEPS_DIR = flags.DEFINE_string(
    'record_steps_dir',
    '',
    'Directory for step records. Default: <output_path>/adb_only_steps_<ts>',
)


# MiniWoB is very lightweight and new screens/View Hierarchy load quickly.
_MINIWOB_TRANSITION_PAUSE = 0.2

# Additional guidelines for the MiniWob tasks.
_MINIWOB_ADDITIONAL_GUIDELINES = [
    (
        'This task is running in a mock app, you must stay in this app and'
        ' DO NOT use the `navigate_home` action.'
    ),
]


def _run_adb(serial: str, args: list[str], timeout_sec: int = 30) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', serial] + args,
      text=True,
      capture_output=True,
      timeout=timeout_sec,
      check=False,
  )


def _contacts_count(serial: str) -> int:
  proc = _run_adb(
      serial,
      [
          'shell',
          'content',
          'query',
          '--uri',
          'content://com.android.contacts/contacts',
          '--projection',
          '_id',
      ],
      timeout_sec=20,
  )
  if proc.returncode != 0:
    return -1
  out = proc.stdout or ''
  return sum(1 for line in out.splitlines() if line.strip().startswith('Row:'))


def _run_adb_only_task(serial: str, task_name: str) -> tuple[bool, str]:
  _run_adb(serial, ['shell', 'input', 'keyevent', '3'])

  if task_name == 'OpenSettings':
    _run_adb(
        serial,
        ['shell', 'am', 'start', '-W', '-n', 'com.android.settings/.Settings'],
    )
    focus = _run_adb(serial, ['shell', 'dumpsys', 'window', 'windows']).stdout or ''
    return ('com.android.settings' in focus, 'open settings and verify focus')

  if task_name == 'GoHome':
    _run_adb(serial, ['shell', 'input', 'keyevent', '3'])
    return (True, 'press home')

  return (False, f'unsupported adb-only task: {task_name}')


def _main_adb_only() -> None:
  from android_env.proto import adb_pb2
  from android_world.agents import infer
  from android_world.agents import m3a
  from android_world import registry
  from android_world.env import interface
  from android_world.env import json_action
  from android_world.env import representation_utils
  import io
  import numpy as np
  from PIL import Image

  def _save_np_image(path: str, arr) -> None:
    if arr is None:
      return
    try:
      Image.fromarray(arr).save(path)
    except Exception:
      return

  def _raw_to_text(raw_obj) -> str:
    if raw_obj is None:
      return ''
    text_attr = getattr(raw_obj, 'text', None)
    if isinstance(text_attr, str) and text_attr:
      return text_attr[:4000]
    return repr(raw_obj)[:4000]

  class _AdbOnlyController:
    def __init__(self, serial: str):
      self.serial = serial

    def execute_adb_call(self, request: adb_pb2.AdbRequest) -> adb_pb2.AdbResponse:
      args: list[str] | None = None

      if request.HasField('generic'):
        args = list(request.generic.args)
      elif request.HasField('settings'):
        ns_map = {
            adb_pb2.AdbRequest.SettingsRequest.Namespace.SYSTEM: 'system',
            adb_pb2.AdbRequest.SettingsRequest.Namespace.SECURE: 'secure',
            adb_pb2.AdbRequest.SettingsRequest.Namespace.GLOBAL: 'global',
        }
        namespace = ns_map.get(request.settings.name_space, 'system')
        if request.settings.HasField('put'):
          args = [
              'shell',
              'settings',
              'put',
              namespace,
              request.settings.put.key,
              request.settings.put.value,
          ]
        elif request.settings.HasField('get'):
          args = [
              'shell',
              'settings',
              'get',
              namespace,
              request.settings.get.key,
          ]
        elif request.settings.HasField('list'):
          args = ['shell', 'settings', 'list', namespace]

      if args is None:
        return adb_pb2.AdbResponse(
            status=adb_pb2.AdbResponse.Status.UNKNOWN_COMMAND,
            error_message='Unsupported adb request in adb-only mode.',
        )

      # Keep `shell` command as one string to support multi-line/quoted scripts.
      if len(args) >= 2 and args[0] == 'shell':
        run_args = ['shell', ' '.join(args[1:])]
      else:
        run_args = args

      proc = subprocess.run(
          [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', self.serial] + run_args,
          text=False,
          capture_output=True,
          check=False,
      )
      # In adb-only shim mode, return OK so task validators can inspect output.
      status = adb_pb2.AdbResponse.Status.OK
      return adb_pb2.AdbResponse(
          status=status,
          generic=adb_pb2.AdbResponse.GenericResponse(output=proc.stdout or b''),
          error_message=(proc.stderr or b'').decode('utf-8', errors='ignore'),
      )

  class _AdbOnlyM3AEnv:
    def __init__(self, serial: str):
      self.serial = serial
      self.interaction_cache = ''
      self._controller = _AdbOnlyController(serial)

    @property
    def controller(self):
      return self._controller

    def reset(self, go_home: bool = False):
      if go_home:
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '3'])
      return self.get_state(wait_to_stabilize=False)

    def get_state(self, wait_to_stabilize: bool = False):
      if wait_to_stabilize:
        _run_adb(self.serial, ['shell', 'sleep', '1'])
      shot = subprocess.run(
          [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', self.serial, 'exec-out', 'screencap', '-p'],
          capture_output=True,
          check=False,
      )
      img = np.array(Image.open(io.BytesIO(shot.stdout or b'')).convert('RGB'))
      _run_adb(self.serial, ['shell', 'uiautomator', 'dump', '/sdcard/window_dump.xml'])
      dumped = _run_adb(self.serial, ['shell', 'cat', '/sdcard/window_dump.xml'])
      ui_elements = representation_utils.xml_dump_to_ui_elements(dumped.stdout or '')
      return interface.State(pixels=img, forest=None, ui_elements=ui_elements, auxiliaries={})

    def ask_question(self, question: str, timeout_seconds: float = -1.0):
      del question, timeout_seconds
      return None

    def execute_action(self, action: json_action.JSONAction):
      current = self.get_state(wait_to_stabilize=False)
      elements = current.ui_elements
      if action.action_type in ['click', 'long_press'] and action.index is not None:
        idx = int(action.index)
        if idx < 0 or idx >= len(elements) or elements[idx].bbox_pixels is None:
          return
        x, y = elements[idx].bbox_pixels.center
        if action.action_type == 'click':
          _run_adb(self.serial, ['shell', 'input', 'tap', str(int(x)), str(int(y))])
        else:
          _run_adb(self.serial, ['shell', 'input', 'swipe', str(int(x)), str(int(y)), str(int(x)), str(int(y)), '700'])
      elif action.action_type == 'input_text':
        if action.index is not None:
          idx = int(action.index)
          if 0 <= idx < len(elements) and elements[idx].bbox_pixels is not None:
            x, y = elements[idx].bbox_pixels.center
            _run_adb(self.serial, ['shell', 'input', 'tap', str(int(x)), str(int(y))])
        if action.text:
          _run_adb(self.serial, ['shell', 'input', 'text', action.text.replace(' ', '%s')])
      elif action.action_type == 'navigate_home':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '3'])
      elif action.action_type == 'navigate_back':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '4'])
      elif action.action_type == 'keyboard_enter':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '66'])
      elif action.action_type == 'open_app' and action.app_name:
        app_name = action.app_name.lower()
        if app_name in ['settings', 'setting', 'android settings']:
          _run_adb(self.serial, ['shell', 'am', 'start', '-W', '-n', 'com.android.settings/.Settings'])
        elif app_name in ['contacts', 'contact', 'people', 'address book']:
          _run_adb(self.serial, ['shell', 'monkey', '-p', 'com.android.contacts', '-c', 'android.intent.category.LAUNCHER', '1'])
      elif action.action_type == 'scroll' and action.direction:
        w, h = self.logical_screen_size
        cx = int(w / 2)
        if action.direction == 'down':
          _run_adb(self.serial, ['shell', 'input', 'swipe', str(cx), str(int(h * 0.8)), str(cx), str(int(h * 0.2)), '300'])
        elif action.direction == 'up':
          _run_adb(self.serial, ['shell', 'input', 'swipe', str(cx), str(int(h * 0.2)), str(cx), str(int(h * 0.8)), '300'])

    @property
    def foreground_activity_name(self) -> str:
      return _run_adb(self.serial, ['shell', 'dumpsys', 'window', 'windows']).stdout or ''

    @property
    def device_screen_size(self) -> tuple[int, int]:
      return self.logical_screen_size

    @property
    def logical_screen_size(self) -> tuple[int, int]:
      out = _run_adb(self.serial, ['shell', 'wm', 'size']).stdout or ''
      import re
      m = re.search(r'(\d+)x(\d+)', out)
      if m:
        return (int(m.group(1)), int(m.group(2)))
      return (1080, 1920)

    def close(self):
      return None

    def hide_automation_ui(self):
      return None

    @property
    def orientation(self) -> int:
      return 0

    @property
    def physical_frame_boundary(self) -> tuple[int, int, int, int]:
      w, h = self.logical_screen_size
      return (0, 0, w, h)

  serial = _ADB_SERIAL.value
  subprocess.run([_ADB_PATH.value if _ADB_PATH.value else 'adb', 'connect', serial], check=False)
  state = _run_adb(serial, ['get-state'], timeout_sec=8)
  if state.returncode != 0 or 'device' not in (state.stdout or ''):
    raise RuntimeError(f'adb serial not ready: {serial}')

  tasks = _TASKS.value or ['OpenSettings']
  print(f'Starting adb-only eval on serial={serial} tasks={tasks}')
  if _OPENAI_API_KEY.value:
    os.environ['OPENAI_API_KEY'] = _OPENAI_API_KEY.value
  if _OPENAI_BASE_URL.value:
    os.environ['OPENAI_BASE_URL'] = _OPENAI_BASE_URL.value
  run_ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
  record_root = ''
  if _RECORD_STEPS.value:
    if _RECORD_STEPS_DIR.value:
      record_root = os.path.expanduser(_RECORD_STEPS_DIR.value)
    else:
      record_root = os.path.join(
          os.path.expanduser(_OUTPUT_PATH.value),
          f'adb_only_steps_{run_ts}',
      )
    os.makedirs(record_root, exist_ok=True)
  total = 0
  passed = 0
  task_results = []
  task_registry = registry.TaskRegistry()
  aw_registry = task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY)
  for task_name in tasks:
    for combo_idx in range(_N_TASK_COMBINATIONS.value):
      total += 1
      if _ADB_ONLY_AGENT.value == 'm3a':
        env = _AdbOnlyM3AEnv(serial=serial)
        llm = infer.Gpt4Wrapper(_OPENAI_MODEL.value)
        agent = m3a.M3A(env, llm)
        task_lc = task_name.lower()
        step_summaries = []
        step_records_jsonl = ''
        combo_dir = ''
        if _RECORD_STEPS.value:
          combo_dir = os.path.join(record_root, f'{task_name}-combo-{combo_idx}')
          os.makedirs(combo_dir, exist_ok=True)
          step_records_jsonl = os.path.join(combo_dir, 'steps.jsonl')
        if task_name in aw_registry:
          task_type = aw_registry[task_name]
          params = task_type.generate_random_params()
          task = task_type(params)
          task.initialize_task(env)
          goal_text = task.goal
          max_steps = max(1, int(task.complexity * 10))
          ok = False
          detail = f'm3a policy task={task_name} params={params}'
          for _ in range(max_steps):
            resp = agent.step(goal_text)
            ok = bool(task.is_successful(env) == 1)
            if getattr(agent, 'history', None):
              last = agent.history[-1]
              step_summaries.append(last.get('summary', ''))
              if _RECORD_STEPS.value:
                step_idx = len(agent.history)
                base = f'step_{step_idx:03d}'
                before_raw = os.path.join(combo_dir, f'{base}_before_raw.png')
                before_som = os.path.join(combo_dir, f'{base}_before_som.png')
                after_som = os.path.join(combo_dir, f'{base}_after_som.png')
                _save_np_image(before_raw, last.get('raw_screenshot'))
                _save_np_image(before_som, last.get('before_screenshot_with_som'))
                # Keep filename stable for compatibility, but save after-step raw
                # screenshot (without SoM bbox overlay) when available.
                after_img = last.get('after_raw_screenshot')
                if after_img is None:
                  after_img = last.get('after_screenshot_with_som')
                _save_np_image(after_som, after_img)
                action_json = last.get('action_output_json')
                if action_json is not None and hasattr(action_json, 'as_dict'):
                  action_json = action_json.as_dict(skip_none=True)
                rec = {
                    'task_name': task_name,
                    'combo_idx': combo_idx,
                    'step_idx': step_idx,
                    'summary': last.get('summary', ''),
                    'action_reason': last.get('action_reason', ''),
                    'action_output': last.get('action_output', ''),
                    'action_json': action_json,
                    'action_model_raw': _raw_to_text(last.get('action_raw_response')),
                    'summary_model_raw': _raw_to_text(last.get('summary_raw_response')),
                    'before_raw_png': before_raw,
                    'before_som_png': before_som,
                    'after_som_png': after_som,
                }
                with open(step_records_jsonl, 'a', encoding='utf-8') as f:
                  f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            if resp.done:
              break
          try:
            task.tear_down(env)
          except Exception:
            pass
        elif task_lc == 'opensettings':
          goal_text = 'Open Android Settings app and then finish.'
          ok = False
          detail = 'm3a policy fallback'
          for _ in range(6):
            resp = agent.step(goal_text)
            ok = 'com.android.settings' in env.foreground_activity_name
            if resp.done:
              break
        else:
          ok = False
          detail = f'unsupported task in adb-only mode: {task_name}'
      else:
        ok, detail = _run_adb_only_task(serial, task_name)
        step_records_jsonl = ''
      print(
          f'ADB_ONLY_TASK task={task_name} combo={combo_idx} '
          f'success={ok} detail="{detail}"'
      )
      if ok:
        passed += 1
      task_results.append(
          {
              'task_name': task_name,
              'combo_idx': combo_idx,
              'success': bool(ok),
              'detail': detail,
              'agent': _ADB_ONLY_AGENT.value,
              'model': _OPENAI_MODEL.value if _ADB_ONLY_AGENT.value == 'm3a' else '',
              'step_summaries': step_summaries if _ADB_ONLY_AGENT.value == 'm3a' else [],
              'step_records_jsonl': step_records_jsonl,
          }
      )

  screenshot = subprocess.run(
      [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', serial, 'exec-out', 'screencap', '-p'],
      capture_output=True,
      check=False,
  )
  with open('/tmp/android_world_run_adb_only.png', 'wb') as f:
    f.write(screenshot.stdout or b'')
  _run_adb(serial, ['shell', 'uiautomator', 'dump', '/sdcard/window_dump.xml'])
  dumped = _run_adb(serial, ['shell', 'cat', '/sdcard/window_dump.xml'])
  with open('/tmp/android_world_run_adb_only.xml', 'w', encoding='utf-8') as f:
    f.write(dumped.stdout or '')

  print(
      f'Finished adb-only eval: passed={passed}/{total}; '
      'artifacts=/tmp/android_world_run_adb_only.(png|xml)'
  )
  output_root = os.path.expanduser(_OUTPUT_PATH.value)
  os.makedirs(output_root, exist_ok=True)
  ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
  pkl_path = os.path.join(output_root, f'adb_only_run_{ts}.pkl')
  payload = {
      'ts_utc': datetime.datetime.utcnow().isoformat() + 'Z',
      'serial': serial,
      'tasks': tasks,
      'total': total,
      'passed': passed,
      'agent': _ADB_ONLY_AGENT.value,
      'model': _OPENAI_MODEL.value if _ADB_ONLY_AGENT.value == 'm3a' else '',
      'task_results': task_results,
      'record_steps_enabled': bool(_RECORD_STEPS.value),
      'record_steps_dir': record_root,
      'artifacts': {
          'png': '/tmp/android_world_run_adb_only.png',
          'xml': '/tmp/android_world_run_adb_only.xml',
      },
  }
  with open(pkl_path, 'wb') as f:
    pickle.dump(payload, f)
  print(f'ADB_ONLY_RESULT_PKL={pkl_path}')
  if passed != total:
    raise SystemExit(1)


def _get_agent(
    env,
    family: str | None = None,
):
  """Gets agent."""
  from android_world.agents import human_agent
  from android_world.agents import infer
  from android_world.agents import m3a
  from android_world.agents import random_agent
  from android_world.agents import seeact
  from android_world.agents import t3a

  print('Initializing agent...')
  agent = None
  if _AGENT_NAME.value == 'human_agent':
    agent = human_agent.HumanAgent(env)
  elif _AGENT_NAME.value == 'random_agent':
    agent = random_agent.RandomAgent(env)
  # Gemini.
  elif _AGENT_NAME.value == 'm3a_gemini_gcp':
    agent = m3a.M3A(
        env, infer.GeminiGcpWrapper(model_name='gemini-1.5-pro-latest')
    )
  elif _AGENT_NAME.value == 't3a_gemini_gcp':
    agent = t3a.T3A(
        env, infer.GeminiGcpWrapper(model_name='gemini-1.5-pro-latest')
    )
  # GPT.
  elif _AGENT_NAME.value == 't3a_gpt4':
    agent = t3a.T3A(env, infer.Gpt4Wrapper('gpt-4-turbo-2024-04-09'))
  elif _AGENT_NAME.value == 'm3a_gpt4v':
    agent = m3a.M3A(env, infer.Gpt4Wrapper('gpt-4-turbo-2024-04-09'))
  # SeeAct.
  elif _AGENT_NAME.value == 'seeact':
    agent = seeact.SeeAct(env)

  if not agent:
    raise ValueError(f'Unknown agent: {_AGENT_NAME.value}')

  if (
      agent.name in ['M3A', 'T3A', 'SeeAct']
      and family
      and family.startswith('miniwob')
      and hasattr(agent, 'set_task_guidelines')
  ):
    agent.set_task_guidelines(_MINIWOB_ADDITIONAL_GUIDELINES)
  agent.name = _AGENT_NAME.value

  return agent


def _main() -> None:
  """Runs eval suite and gets rewards back."""
  if _ADB_ONLY.value:
    _main_adb_only()
    return

  from android_world import checkpointer as checkpointer_lib
  from android_world import registry
  from android_world import suite_utils

  env = env_launcher.load_and_setup_env(
      console_port=_DEVICE_CONSOLE_PORT.value,
      emulator_setup=_EMULATOR_SETUP.value,
      adb_path=_ADB_PATH.value,
  )

  n_task_combinations = _N_TASK_COMBINATIONS.value
  task_registry = registry.TaskRegistry()
  suite = suite_utils.create_suite(
      task_registry.get_registry(family=_SUITE_FAMILY.value),
      n_task_combinations=n_task_combinations,
      seed=_TASK_RANDOM_SEED.value,
      tasks=_TASKS.value,
      use_identical_params=_FIXED_TASK_SEED.value,
  )
  suite.suite_family = _SUITE_FAMILY.value

  agent = _get_agent(env, _SUITE_FAMILY.value)

  if _SUITE_FAMILY.value.startswith('miniwob'):
    # MiniWoB pages change quickly, don't need to wait for screen to stabilize.
    agent.transition_pause = _MINIWOB_TRANSITION_PAUSE
  else:
    agent.transition_pause = None

  if _CHECKPOINT_DIR.value:
    checkpoint_dir = _CHECKPOINT_DIR.value
  else:
    checkpoint_dir = checkpointer_lib.create_run_directory(_OUTPUT_PATH.value)

  print(
      f'Starting eval with agent {_AGENT_NAME.value} and writing to'
      f' {checkpoint_dir}'
  )
  suite_utils.run(
      suite,
      agent,
      checkpointer=checkpointer_lib.IncrementalCheckpointer(checkpoint_dir),
      demo_mode=False,
  )
  print(
      f'Finished running agent {_AGENT_NAME.value} on {_SUITE_FAMILY.value}'
      f' family. Wrote to {checkpoint_dir}.'
  )
  env.close()


def main(argv: Sequence[str]) -> None:
  del argv
  _main()


if __name__ == '__main__':
  app.run(main)
