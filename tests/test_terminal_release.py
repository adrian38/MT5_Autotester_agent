import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run_tests


class TerminalReleaseTests(unittest.TestCase):
    def test_update_handoff_matches_only_its_installation(self):
        target = Path('C:/MT5_IC_5/terminal64.exe')
        updater = {'pid': '1', 'path': 'C:/data/liveupdate/terminal64.exe',
                   'command': 'terminal64.exe /update /path:"C:\\MT5_IC_5"'}
        other = dict(updater, pid='2', command='terminal64.exe /update /path:"C:\\MT5_IC_50"')
        successor = {'pid': '3', 'path': str(target), 'command': '/skipupdate:123'}
        with patch.object(run_tests, 'get_running_terminal_processes', return_value=[updater, other, successor]):
            self.assertEqual(run_tests.find_matching_running_terminals(target), [updater, successor])

    def test_waits_through_gap_between_updater_and_successor(self):
        busy = [{'pid': '7'}]
        with (
            patch.object(run_tests, 'find_matching_running_terminals', side_effect=[busy, [], busy, [], []]) as scan,
            patch.object(run_tests.time, 'sleep'),
        ):
            run_tests.wait_for_terminal_release(Path('terminal64.exe'), Mock())
        self.assertEqual(scan.call_count, 5)

    def test_timeout_does_not_release_busy_profile(self):
        with (
            patch.object(run_tests, 'find_matching_running_terminals', return_value=[{'pid': '7'}]),
            patch.object(run_tests.time, 'monotonic', side_effect=[0, 121]),
        ):
            with self.assertRaises(run_tests.TerminalStillRunningError):
                run_tests.wait_for_terminal_release(Path('terminal64.exe'), Mock())

    def test_process_inventory_failure_is_not_treated_as_closed(self):
        with patch.object(run_tests.subprocess, 'run', return_value=Mock(returncode=1)):
            with self.assertRaises(RuntimeError):
                run_tests.get_running_terminal_processes()

    def test_worker_does_not_launch_next_candidate_after_release_timeout(self):
        profile = Mock(name='profile')
        jobs = [Mock(index=1), Mock(index=2)]
        with (
            patch.object(run_tests, 'log_runner_diagnostics'),
            patch.object(run_tests, 'settings_from_profile'),
            patch.object(run_tests, 'run_backtest_job', side_effect=run_tests.TerminalStillRunningError('busy')) as execute,
        ):
            failures = run_tests.run_jobs_parallel(jobs, [profile], Mock(), Mock(), {}, Mock(), set_mode=True)
        self.assertEqual(failures, 1)
        self.assertEqual(execute.call_count, 1)


if __name__ == '__main__':
    unittest.main()
