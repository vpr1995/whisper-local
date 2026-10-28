# test_dashboard_ipc.py
# Covers the two-way pipe between the running app and the dashboard child
# (window_launcher): command whitelist, navigate messages, child-side pipe.
# Uses fakes and real OS pipes; no window is ever opened.

import io
import json
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from whisper_key import window_launcher  # noqa: E402


class AppCommandDispatchTests(unittest.TestCase):
    # Parent side: what the dashboard child may ask the app to do.

    def test_whitelisted_commands_reach_the_handler(self):
        handler = mock.Mock()
        for command in window_launcher.APP_COMMANDS:
            window_launcher._dispatch_child_line(json.dumps({'command': command}) + '\n', handler)
        self.assertEqual([c.args[0] for c in handler.call_args_list], list(window_launcher.APP_COMMANDS))

    def test_unknown_commands_and_garbage_are_ignored(self):
        handler = mock.Mock()
        for line in ('{"command": "format_disk"}\n', 'not json\n', '[1, 2]\n', '{"nope": 1}\n', '\n'):
            window_launcher._dispatch_child_line(line, handler)
        handler.assert_not_called()

    def test_a_failing_handler_does_not_kill_the_reader(self):
        handler = mock.Mock(side_effect=RuntimeError('boom'))
        window_launcher._dispatch_child_line('{"command": "restart"}\n', handler)  # must not raise
        window_launcher._dispatch_child_line('{"command": "restart"}\n', None)     # no handler: fine

    def test_reader_dispatches_every_line_until_the_pipe_closes(self):
        handler = mock.Mock()
        child = mock.Mock()
        child.stdout = io.StringIO('{"command": "restart"}\njunk\n{"command": "toggle_pause_hotkeys"}\n')
        window_launcher._read_child_commands(child, handler)
        self.assertEqual([c.args[0] for c in handler.call_args_list], ['restart', 'toggle_pause_hotkeys'])


class DashboardSpawnTests(unittest.TestCase):
    def setUp(self):
        window_launcher._open_children.clear()
        self.addCleanup(window_launcher._open_children.clear)

    def test_first_open_spawns_with_both_pipes_and_keeps_stdin_open(self):
        child = mock.Mock()
        with mock.patch.object(window_launcher.subprocess, 'Popen', return_value=child) as popen, \
             mock.patch.object(window_launcher.threading, 'Thread'):
            window_launcher.open_dashboard('history', on_app_command=print)
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs['stdout'], window_launcher.subprocess.PIPE)
        self.assertEqual(kwargs['env'][window_launcher.COMMAND_PIPE_ENV_VAR], '1')
        first_line = child.stdin.write.call_args.args[0]
        self.assertTrue(first_line.endswith(b'\n'))
        self.assertEqual(json.loads(first_line), {'page': 'history', 'app_state': None})
        child.stdin.close.assert_not_called()

    def test_new_dashboard_starts_with_the_last_app_state(self):
        child = mock.Mock()
        with mock.patch.object(window_launcher, '_latest_app_state', 'sleeping'), \
             mock.patch.object(window_launcher.subprocess, 'Popen', return_value=child), \
             mock.patch.object(window_launcher.threading, 'Thread'):
            window_launcher.open_dashboard('home')
        self.assertEqual(json.loads(child.stdin.write.call_args.args[0])['app_state'], 'sleeping')

    def test_state_changes_reach_the_open_dashboard(self):
        child = mock.Mock()
        child.poll.return_value = None
        window_launcher._open_children['dashboard'] = child
        with mock.patch.object(window_launcher, '_latest_app_state', None):
            window_launcher.publish_app_state('recording')
            self.assertEqual(window_launcher._latest_app_state, 'recording')
        self.assertEqual(json.loads(child.stdin.write.call_args.args[0]), {'app_state': 'recording'})

    def test_state_change_without_a_dashboard_is_only_remembered(self):
        with mock.patch.object(window_launcher, '_latest_app_state', None):
            window_launcher.publish_app_state('idle')  # no child open: must not raise
            self.assertEqual(window_launcher._latest_app_state, 'idle')

    def test_second_open_navigates_the_running_child_instead_of_spawning(self):
        child = mock.Mock()
        child.poll.return_value = None  # still running
        window_launcher._open_children['dashboard'] = child
        with mock.patch.object(window_launcher.subprocess, 'Popen') as popen:
            window_launcher.open_dashboard('settings')
        popen.assert_not_called()
        self.assertEqual(json.loads(child.stdin.write.call_args.args[0]), {'navigate': 'settings'})
        child.stdin.flush.assert_called()

    def test_open_after_the_window_closed_spawns_again(self):
        dead = mock.Mock()
        dead.poll.return_value = 0
        window_launcher._open_children['dashboard'] = dead
        with mock.patch.object(window_launcher.subprocess, 'Popen', return_value=mock.Mock()) as popen, \
             mock.patch.object(window_launcher.threading, 'Thread'):
            window_launcher.open_dashboard('home')
        popen.assert_called_once()

    def test_broken_pipe_on_navigate_is_swallowed(self):
        child = mock.Mock()
        child.poll.return_value = None
        child.stdin.write.side_effect = BrokenPipeError()
        window_launcher._open_children['dashboard'] = child
        window_launcher.open_dashboard('settings')  # must not raise

    def test_fallback_window_still_gets_a_one_shot_payload(self):
        child = mock.Mock()
        with mock.patch('whisper_key.utils.tk_requires_main_thread', return_value=True), \
             mock.patch.object(window_launcher.subprocess, 'Popen', return_value=child), \
             mock.patch.object(window_launcher.threading, 'Thread'):
            window_launcher.open_in_child_process('fallback', {'x': 1})
        child.stdin.close.assert_called_once()


class ChildSideTests(unittest.TestCase):
    def test_read_payload_takes_only_the_first_line(self):
        stream = io.StringIO('{"page": "models"}\n{"navigate": "home"}\n')
        self.assertEqual(window_launcher.read_payload(stream), {'page': 'models'})
        self.assertEqual(stream.readline(), '{"navigate": "home"}\n')

    def test_navigate_messages_call_the_navigator(self):
        navigate = mock.Mock()
        window_launcher.listen_to_app(io.StringIO('{"navigate": "about"}\nbad\n{"navigate": 5}\n'), navigate)
        navigate.assert_called_once_with('about')

    def test_app_state_messages_call_the_state_handler(self):
        navigate, on_state = mock.Mock(), mock.Mock()
        window_launcher.listen_to_app(io.StringIO('{"app_state": "sleeping"}\n{"app_state": 3}\n'), navigate, on_state)
        on_state.assert_called_once_with('sleeping')
        navigate.assert_not_called()

    def test_send_command_without_a_pipe_returns_false(self):
        with mock.patch.object(window_launcher, '_command_pipe', None):
            self.assertFalse(window_launcher.send_command_to_app('restart'))

    def test_send_command_rejects_unknown_names(self):
        pipe = io.StringIO()
        with mock.patch.object(window_launcher, '_command_pipe', pipe):
            self.assertFalse(window_launcher.send_command_to_app('rm -rf'))
        self.assertEqual(pipe.getvalue(), '')

    def test_send_command_writes_one_json_line(self):
        pipe = io.StringIO()
        with mock.patch.object(window_launcher, '_command_pipe', pipe):
            self.assertTrue(window_launcher.send_command_to_app('retry_last_dictation'))
        self.assertEqual(pipe.getvalue(), '{"command": "retry_last_dictation"}\n')

    def test_send_command_on_a_closed_pipe_returns_false(self):
        pipe = io.StringIO()
        pipe.close()
        with mock.patch.object(window_launcher, '_command_pipe', pipe):
            self.assertFalse(window_launcher.send_command_to_app('restart'))

    def test_claim_does_nothing_without_the_parent_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(window_launcher, '_command_pipe', None):
            os.environ.pop(window_launcher.COMMAND_PIPE_ENV_VAR, None)
            window_launcher._claim_command_pipe()
            self.assertIsNone(window_launcher._command_pipe)


if __name__ == '__main__':
    unittest.main()
