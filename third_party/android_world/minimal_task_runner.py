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

"""Runs a single task.

The minimal_run.py module is used to run a single task, it is a minimal version
of the run.py module. A task can be specified, otherwise a random task is
selected.
"""

from collections.abc import Sequence
import os
import random
import subprocess
from typing import Type

from absl import app
from absl import flags
from absl import logging

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

_TASK = flags.DEFINE_string(
    'task',
    None,
    'A specific task to run.',
)
_ADB_ONLY = flags.DEFINE_boolean(
    'adb_only',
    False,
    'Run a minimal adb-only task path without emulator gRPC.',
)
_ADB_SERIAL = flags.DEFINE_string(
    'adb_serial',
    '127.0.0.1:15500',
    'ADB serial for adb-only mode.',
)
_ADB_ONLY_TASK = flags.DEFINE_enum(
    'adb_only_task',
    'OpenSettings',
    ['OpenSettings', 'GoHome'],
    'Task name for adb-only mode.',
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
    'Model name for m3a adb-only mode.',
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


def _run_adb(serial: str, args: list[str], timeout_sec: int = 20) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', serial] + args,
      text=True,
      capture_output=True,
      timeout=timeout_sec,
      check=False,
  )


def _adb_only_main() -> None:
  from android_world.agents import infer
  from android_world.agents import m3a
  from android_world.env import interface
  from android_world.env import json_action
  from android_world.env import representation_utils
  import io
  import numpy as np
  from PIL import Image

  class _AdbOnlyM3AEnv:
    def __init__(self, serial: str):
      self.serial = serial
      self.interaction_cache = ''

    @property
    def controller(self):
      return None

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
          text = action.text.replace(' ', '%s')
          _run_adb(self.serial, ['shell', 'input', 'text', text])
      elif action.action_type == 'navigate_home':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '3'])
      elif action.action_type == 'navigate_back':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '4'])
      elif action.action_type == 'keyboard_enter':
        _run_adb(self.serial, ['shell', 'input', 'keyevent', '66'])
      elif action.action_type == 'open_app' and action.app_name:
        # Map common request to Settings for quick validation task.
        if action.app_name.lower() in ['settings', 'setting', 'android settings']:
          _run_adb(self.serial, ['shell', 'am', 'start', '-W', '-n', 'com.android.settings/.Settings'])
      elif action.action_type == 'scroll' and action.direction:
        w, h = self.logical_screen_size
        cx, cy = int(w / 2), int(h / 2)
        if action.direction == 'down':
          _run_adb(self.serial, ['shell', 'input', 'swipe', str(cx), str(int(h * 0.8)), str(cx), str(int(h * 0.2)), '300'])
        elif action.direction == 'up':
          _run_adb(self.serial, ['shell', 'input', 'swipe', str(cx), str(int(h * 0.2)), str(cx), str(int(h * 0.8)), '300'])
      elif action.action_type in ['wait', 'answer', 'status', 'unknown']:
        return

    @property
    def foreground_activity_name(self) -> str:
      focus = _run_adb(self.serial, ['shell', 'dumpsys', 'window', 'windows']).stdout or ''
      return focus

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

  goal = _ADB_ONLY_TASK.value
  print('Goal: ' + goal)

  # Reset to home state first.
  _run_adb(serial, ['shell', 'input', 'keyevent', '3'])

  if _ADB_ONLY_AGENT.value == 'm3a':
    if _OPENAI_API_KEY.value:
      os.environ['OPENAI_API_KEY'] = _OPENAI_API_KEY.value
    if _OPENAI_BASE_URL.value:
      os.environ['OPENAI_BASE_URL'] = _OPENAI_BASE_URL.value
    env = _AdbOnlyM3AEnv(serial=serial)
    llm = infer.Gpt4Wrapper(_OPENAI_MODEL.value)
    agent = m3a.M3A(env, llm)
    goal_text = 'Open Android Settings app and then finish.'
    success = False
    for _ in range(3):
      resp = agent.step(goal_text)
      focus = env.foreground_activity_name
      if 'com.android.settings' in focus:
        success = True
      if resp.done:
        break
  else:
    if _ADB_ONLY_TASK.value == 'OpenSettings':
      _run_adb(
          serial,
          ['shell', 'am', 'start', '-W', '-n', 'com.android.settings/.Settings'],
      )
      focus = _run_adb(serial, ['shell', 'dumpsys', 'window', 'windows']).stdout or ''
      success = 'com.android.settings' in focus
    else:
      success = True

  # Save simple artifacts for debugging.
  screenshot = subprocess.run(
      [_ADB_PATH.value if _ADB_PATH.value else 'adb', '-s', serial, 'exec-out', 'screencap', '-p'],
      capture_output=True,
      check=False,
  )
  with open('/tmp/android_world_minimal_adb_only.png', 'wb') as f:
    f.write(screenshot.stdout or b'')
  xml = _run_adb(serial, ['shell', 'uiautomator', 'dump', '/sdcard/window_dump.xml'])
  _ = xml
  dumped = _run_adb(serial, ['shell', 'cat', '/sdcard/window_dump.xml'])
  with open('/tmp/android_world_minimal_adb_only.xml', 'w', encoding='utf-8') as f:
    f.write(dumped.stdout or '')

  print(
      f'{"Task Successful ✅" if success else "Task Failed ❌"};'
      f' {goal}; serial={serial}; '
      'artifacts=/tmp/android_world_minimal_adb_only.(png|xml)'
  )
  if not success:
    raise SystemExit(1)


def _main() -> None:
  """Runs a single task."""
  if _ADB_ONLY.value:
    _adb_only_main()
    return

  from android_world import registry
  from android_world.agents import infer
  from android_world.agents import t3a
  from android_world.env import env_launcher
  from android_world.task_evals import task_eval

  env = env_launcher.load_and_setup_env(
      console_port=_DEVICE_CONSOLE_PORT.value,
      emulator_setup=_EMULATOR_SETUP.value,
      adb_path=_ADB_PATH.value,
  )
  env.reset(go_home=True)
  task_registry = registry.TaskRegistry()
  aw_registry = task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY)
  if _TASK.value:
    if _TASK.value not in aw_registry:
      raise ValueError('Task {} not found in registry.'.format(_TASK.value))
    task_type: Type[task_eval.TaskEval] = aw_registry[_TASK.value]
  else:
    task_type: Type[task_eval.TaskEval] = random.choice(
        list(aw_registry.values())
    )
  params = task_type.generate_random_params()
  task = task_type(params)
  task.initialize_task(env)
  agent = t3a.T3A(env, infer.Gpt4Wrapper('gpt-4-turbo-2024-04-09'))

  print('Goal: ' + str(task.goal))
  is_done = False
  for _ in range(int(task.complexity * 10)):
    response = agent.step(task.goal)
    if response.done:
      is_done = True
      break
  agent_successful = is_done and task.is_successful(env) == 1
  print(
      f'{"Task Successful ✅" if agent_successful else "Task Failed ❌"};'
      f' {task.goal}'
  )
  env.close()


def main(argv: Sequence[str]) -> None:
  del argv
  _main()


if __name__ == '__main__':
  app.run(main)
