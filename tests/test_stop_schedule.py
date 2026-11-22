"""node stop --after-block: the rule for the console, the schedule, cron's check and the watch."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from evernode import arguments, cli, nodes, process, stop_schedule, storage
from support import make_node_configuration

NAME = "validator01"
TARGET = 1000
# A chain with a masterchain block every 3 s, read by a server whose clock is CLOCK.
GENESIS = 1_800_000_000
CLOCK = GENESIS + 9_000


def reading(block, shard_client=None, clock=CLOCK):
    """A console reading: the last applied block, and the block the shard client is at."""
    shard_client = block if shard_client is None else shard_client
    return {
        "node_status": "synchronization_finished",
        "masterchainblocknumber": block,
        "masterchainblocktime": GENESIS + 3 * block,
        "timediff": clock - (GENESIS + 3 * block),
        "shards_timediff": clock - (GENESIS + 3 * shard_client),
        "in_current_vset_p34": False,
        "in_next_vset_p36": False,
    }


class Clock:
    """Time that passes only in sleep(), or when a test moves `now` on."""

    def __init__(self):
        self.now = 0
        self.started = time.time()
        self.during_sleep = None

    def __call__(self):
        return self.now

    def time(self):
        """The wall clock: the test's start plus the seconds that passed since."""
        return self.started + self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.during_sleep:
            self.during_sleep()


class ScheduleCase(unittest.TestCase):
    """A managed node in a temporary state directory, and a temporary cron directory."""

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.cron = root / "cron.d"
        self.cron.mkdir()
        for context in (
            patch.object(storage.NodeStateStore, "validate_state_root"),
            patch("evernode.stop_schedule.CRON_DIRECTORY", self.cron),
            patch("evernode.stop_schedule.shutil.which", return_value="/usr/local/bin/evernode"),
            patch("evernode.stop_schedule.elections.cron_daemon_state", return_value="running"),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.store = storage.NodeStateStore(root / "state")
        self.directory = self.store.node_directory(NAME)
        (self.directory / "logs" / "validator").mkdir(parents=True)
        self.store.save_node_config(make_node_configuration(NAME))
        self.cron_file = self.cron / f"evernode_stop_{NAME}"
        self.record = self.directory / "stop-at-block.json"
        self.clock = Clock()

    def schedule(self, block=TARGET - 100, **options):
        options = {"timeout": 30, "force": False, **options}
        return stop_schedule.schedule(self.store, NAME, TARGET, reading(block), **options)

    def check(self, readings, stop=None):
        """One check fed these readings, the last one again and again; returns the stop."""
        stop = stop or MagicMock()
        feed = iter(readings)
        latest = []

        def read():
            latest[:] = [next(feed, *latest)]
            if isinstance(latest[0], Exception):
                raise latest[0]
            # A function is something that happens during the reading.
            return latest[0]() if callable(latest[0]) else latest[0]

        stop_schedule.check(
            self.store,
            NAME,
            read=read,
            stop=stop,
            clock=self.clock,
            sleep=self.clock.sleep,
            now=self.clock.time,
        )
        return stop

    def log(self):
        path = stop_schedule.log_path(self.store, NAME)
        return [json.loads(line) for line in path.read_text().splitlines()]


class ConsoleRuleTests(unittest.TestCase):
    """NODE-6: the block and every shard block it lists are applied."""

    def test_the_shard_clients_block_time_is_read_without_the_servers_clock(self):
        for clock in (CLOCK, GENESIS - 500):  # The second is behind every block: negative lags.
            with self.subTest(clock=clock):
                value = stop_schedule.shard_client_block_time(reading(40, 38, clock))
                self.assertEqual(value, GENESIS + 3 * 38)

    def test_a_missing_or_unknown_value_gives_no_time(self):
        for change in ({"shards_timediff": "unknown"}, {"timediff": None}, {"masterchainblocktime": 1.5}):
            with self.subTest(change=change):
                self.assertIsNone(stop_schedule.shard_client_block_time({**reading(40), **change}))
        self.assertIsNone(stop_schedule.shard_client_block_time({}))

    def states(self, *readings):
        wait = stop_schedule.BlockWait(TARGET)
        return [wait.state(value) for value in readings]

    def test_ready_only_when_the_shard_client_has_reached_the_block(self):
        self.assertEqual(
            self.states(
                reading(TARGET - 1),
                reading(TARGET, TARGET - 1),
                reading(TARGET + 1, TARGET - 1),
                reading(TARGET + 1, TARGET),
            ),
            ["below", "reached", "reached", "ready"],
        )

    def test_one_reading_can_show_the_block_reached_and_ready(self):
        self.assertEqual(self.states(reading(TARGET)), ["ready"])

    def test_a_node_first_seen_past_the_block_waits_for_the_block_it_was_seen_at(self):
        self.assertEqual(
            self.states(
                reading(TARGET + 5, TARGET + 4),
                reading(TARGET + 9, TARGET + 4),
                reading(TARGET + 9, TARGET + 5),
            ),
            ["reached", "reached", "ready"],
        )

    def test_the_last_block_of_a_network_that_stopped_is_enough(self):
        # No block comes any more; only the server's clock moves.
        self.assertEqual(
            self.states(reading(TARGET, TARGET - 1, CLOCK), reading(TARGET, TARGET, CLOCK + 600)),
            ["reached", "ready"],
        )

    def test_values_that_are_not_whole_numbers_never_move_the_wait_forward(self):
        for change, expected in (
            ({"masterchainblocknumber": "not set"}, None),
            ({"masterchainblocknumber": True}, None),
            ({"masterchainblocktime": None}, "reached"),
            ({"shards_timediff": "unknown"}, "reached"),
        ):
            with self.subTest(change=change):
                self.assertEqual(
                    self.states({**reading(TARGET), **change}, {}, reading(TARGET)),
                    [expected, None, "ready"],
                )

    def test_progress_names_the_block_and_what_is_left(self):
        wait = stop_schedule.BlockWait(TARGET)
        self.assertIn("error", wait.progress())
        wait.state(reading(TARGET - 7))
        self.assertEqual(wait.progress(), {"masterchain_block": TARGET - 7, "blocks_left": 7})
        wait.state(reading(TARGET + 2, TARGET - 1))
        self.assertEqual(wait.progress(), {"masterchain_block": TARGET + 2, "blocks_left": 0})


class SchedulingTests(ScheduleCase):
    def test_the_record_and_the_cron_line_are_written(self):
        before = int(time.time())
        self.schedule(timeout=120, force=True)
        record = json.loads(self.record.read_text())
        self.assertTrue(before <= record.pop("seen_below_at") <= time.time())
        self.assertEqual(
            record, {"version": 1, "after_block": TARGET, "timeout": 120, "force": True}
        )
        lines = self.cron_file.read_text().splitlines()
        self.assertEqual(lines[0], "# Managed by evernode")
        self.assertEqual(
            lines[-1],
            f"* * * * * root /usr/local/bin/evernode --state-dir {self.store.root} "
            f"node stop -n {NAME} --check >> {stop_schedule.log_path(self.store, NAME)} 2>&1",
        )
        self.assertEqual(self.cron_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            {key: self.log()[0][key] for key in ("result", "after_block", "blocks_left")},
            {"result": "scheduled", "after_block": TARGET, "blocks_left": 100},
        )

    def test_the_cron_line_is_a_command_the_parser_takes(self):
        self.schedule()
        command = self.cron_file.read_text().splitlines()[-1].split(" >> ")[0].split()[6:]
        args = arguments.build_argument_parser().parse_args(command[1:])
        self.assertEqual((args.action, args.name, args.check), ("stop", NAME, True))

    def test_the_offset_returned_is_where_this_schedules_lines_begin(self):
        stop_schedule.log_path(self.store, NAME).write_text("an earlier line\n")
        self.assertEqual(self.schedule(), len("an earlier line\n"))

    def test_a_node_at_or_past_the_block_is_refused(self):
        for block in (TARGET, TARGET + 3):
            with self.subTest(block=block), self.assertRaises(process.OperationError) as caught:
                self.schedule(block, force=True)
            self.assertIn("already applied", str(caught.exception))
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())

    def test_a_node_without_a_block_number_is_refused(self):
        with self.assertRaises(process.OperationError):
            stop_schedule.schedule(
                self.store, NAME, TARGET, {"node_status": "load_master_state"},
                timeout=30, force=False,
            )
        self.assertFalse(self.cron_file.exists())

    def test_a_second_schedule_is_refused_and_changes_nothing(self):
        self.schedule()
        before = self.record.read_text()
        with self.assertRaises(process.OperationError) as caught:
            stop_schedule.schedule(
                self.store, NAME, TARGET + 50, reading(TARGET - 100), timeout=5, force=True
            )
        self.assertIn("--cancel", str(caught.exception))
        self.assertEqual(self.record.read_text(), before)

    def test_a_host_that_cannot_carry_it_out_is_refused(self):
        for target, value in (
            ("evernode.stop_schedule.elections.cron_daemon_state", "not running"),
            ("evernode.stop_schedule.shutil.which", None),
        ):
            with self.subTest(target=target), patch(target, return_value=value):
                with self.assertRaises(process.OperationError):
                    self.schedule()
                self.assertFalse(self.cron_file.exists())

    def test_no_schedule_while_a_check_of_the_one_before_is_still_ending(self):
        """It would take the new cron file for its own, and stop at the old block."""
        with stop_schedule._hold_check_lock(self.store, NAME):
            with self.assertRaises(process.OperationError) as caught:
                self.schedule()
        self.assertIn("retry in a few seconds", str(caught.exception))
        self.assertFalse(self.cron_file.exists())
        self.schedule()

    def test_a_log_that_cannot_be_written_leaves_nothing_scheduled(self):
        stop_schedule.log_path(self.store, NAME).parent.rmdir()
        with self.assertRaises(OSError):
            self.schedule()
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())

    def test_describe_says_what_is_scheduled(self):
        self.assertIsNone(stop_schedule.describe(self.store, NAME)["scheduled_stop"])
        self.schedule()
        self.assertEqual(
            stop_schedule.describe(self.store, NAME)["scheduled_stop"],
            {"after_block": TARGET, "timeout": 30, "force": False},
        )


class CheckTests(ScheduleCase):
    def test_a_check_that_does_not_get_ready_reports_progress_and_stops_nothing(self):
        self.schedule()
        stop = self.check([reading(TARGET - 60), reading(TARGET - 40)])
        stop.assert_not_called()
        self.assertTrue(self.cron_file.exists())
        self.assertEqual(self.clock.now, stop_schedule.CHECK_SECONDS)
        self.assertEqual(len(self.log()), 2)
        self.assertEqual(
            {key: self.log()[-1][key] for key in ("masterchain_block", "blocks_left")},
            {"masterchain_block": TARGET - 40, "blocks_left": 40},
        )

    def test_the_block_applied_without_its_shard_blocks_is_not_ready(self):
        self.schedule()
        self.check([reading(TARGET + 1, TARGET - 1)]).assert_not_called()
        self.assertEqual(self.log()[-1]["blocks_left"], 0)
        self.assertNotIn("stop_in_seconds", self.log()[-1])

    def test_a_ready_node_is_stopped_once_after_the_settle_time(self):
        self.schedule(timeout=120, force=True)
        ready_at = []
        stop = MagicMock(side_effect=lambda *_: ready_at.append(self.clock.now))
        self.check([reading(TARGET - 1), reading(TARGET, TARGET - 1), reading(TARGET + 1, TARGET)], stop)
        stop.assert_called_once_with(120, True)
        self.assertEqual(ready_at, [2 * stop_schedule.POLL_SECONDS + stop_schedule.SETTLE_SECONDS])
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())
        ready, stopped = self.log()[-2:]
        self.assertEqual(ready["stop_in_seconds"], stop_schedule.SETTLE_SECONDS)
        self.assertEqual(
            {key: stopped[key] for key in ("result", "after_block", "masterchain_block")},
            {"result": "stopped", "after_block": TARGET, "masterchain_block": TARGET + 1},
        )

    def test_the_cron_file_is_gone_before_the_stop_begins(self):
        self.schedule()
        stop = MagicMock(side_effect=lambda *_: self.assertFalse(self.cron_file.exists()))
        self.check([reading(TARGET)], stop)
        stop.assert_called_once()

    def test_a_cancel_during_the_wait_or_the_settle_time_stops_nothing(self):
        for readings, cancel_at in (
            ([reading(TARGET - 5)], 10),
            ([reading(TARGET)], stop_schedule.SETTLE_SECONDS - stop_schedule.POLL_SECONDS),
        ):
            with self.subTest(cancel_at=cancel_at):
                self.clock = Clock()
                self.schedule()
                self.clock.during_sleep = lambda: (
                    self.clock.now == cancel_at and stop_schedule.cancel(self.store, NAME)
                )
                self.check(readings).assert_not_called()
                self.assertEqual(self.log()[-1]["result"], "cancelled")
                self.assertFalse(self.record.exists())

    def test_another_stop_of_the_node_during_the_settle_time_ends_the_check(self):
        self.schedule()
        self.clock.during_sleep = lambda: stop_schedule.remove_cron(NAME)
        self.check([reading(TARGET)]).assert_not_called()
        self.assertNotIn("result", self.log()[-1])

    def test_a_check_remembers_when_the_node_was_last_below_the_block(self):
        self.schedule()
        self.check([reading(TARGET - 60)])
        seen = json.loads(self.record.read_text())["seen_below_at"]
        self.assertEqual(seen, int(self.clock.time()))

    def test_a_check_whose_schedule_ended_during_a_reading_writes_nothing(self):
        self.schedule()

        def cancelled_meanwhile():
            stop_schedule.cancel(self.store, NAME)
            return reading(TARGET)

        self.check([reading(TARGET - 60), cancelled_meanwhile]).assert_not_called()
        self.assertEqual(self.log()[-1]["result"], "cancelled")
        self.assertFalse(self.record.exists())

    def test_a_node_that_passed_the_block_within_the_limit_is_stopped(self):
        """Cron missed some minutes: the node was below the block a short time ago."""
        self.schedule()
        self.check([reading(TARGET - 60)])
        self.clock.now += stop_schedule.MISSED_SECONDS - 60
        self.check([reading(TARGET + 40)]).assert_called_once()
        self.assertEqual(self.log()[-1]["result"], "stopped")

    def test_a_node_that_passed_the_block_unwatched_is_not_stopped(self):
        """NODE-6: nothing shows that it still holds the block's state."""
        self.schedule()
        self.check([reading(TARGET - 60)])
        self.clock.now += stop_schedule.MISSED_SECONDS + 1
        self.check([reading(TARGET + 400)]).assert_not_called()
        failed = self.log()[-1]
        self.assertEqual(failed["result"], "failed")
        self.assertIn(f"last seen below block {TARGET}", failed["error"])
        self.assertIn("was not stopped", failed["error"])
        self.assertIn(f"evernode node stop -n {NAME}", failed["hint"])
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())

    def test_a_node_whose_shard_blocks_never_come_is_given_up_at_the_limit(self):
        self.schedule()
        stop = MagicMock()
        for _ in range(stop_schedule.MISSED_SECONDS // 60 + 2):
            self.check([reading(TARGET + 1, TARGET - 1)], stop)
            self.clock.now += 60 - stop_schedule.CHECK_SECONDS
        stop.assert_not_called()
        self.assertEqual(self.log()[-1]["result"], "failed")
        self.assertFalse(self.cron_file.exists())

    def test_a_refused_stop_is_reported_and_never_tried_again(self):
        self.schedule()
        stop = MagicMock(side_effect=process.OperationError("This node is in the next validator set"))
        self.check([reading(TARGET)], stop)
        failed = self.log()[-1]
        self.assertEqual(failed["result"], "failed")
        self.assertIn("next validator set", failed["error"])
        self.assertIn(f"evernode node stop -n {NAME}", failed["hint"])
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())
        self.check([reading(TARGET)], stop)
        stop.assert_called_once()

    def test_a_failed_reading_is_logged_and_never_moves_the_wait_forward(self):
        self.schedule()
        error = process.OperationError("Node container is not running")
        self.check([reading(TARGET - 1), error]).assert_not_called()
        self.assertEqual(self.log()[-1]["error"], "Node container is not running")
        self.assertTrue(self.cron_file.exists())

    def test_a_reading_without_a_block_number_is_logged(self):
        self.schedule()
        self.check([{"node_status": "load_master_state"}]).assert_not_called()
        self.assertIn("no masterchain block", self.log()[-1]["error"])

    def test_a_check_leaves_the_minute_to_one_still_working(self):
        self.schedule()
        read = MagicMock()
        with stop_schedule._hold_check_lock(self.store, NAME):
            stop_schedule.check(self.store, NAME, read=read, stop=MagicMock())
        read.assert_not_called()

    def test_a_check_with_nothing_scheduled_reads_nothing(self):
        read = MagicMock()
        self.assertEqual(stop_schedule.check(self.store, NAME, read=read, stop=MagicMock()), 0)
        read.assert_not_called()
        self.assertFalse(stop_schedule.log_path(self.store, NAME).exists())

    def test_a_cron_file_without_a_readable_record_is_removed(self):
        for damage in (self.record.unlink, lambda: self.record.write_text('{"after_block": "x"}')):
            with self.subTest(damage=damage):
                self.schedule()
                damage()
                self.check([reading(TARGET)]).assert_not_called()
                self.assertFalse(self.cron_file.exists())
                self.assertEqual(self.log()[-1]["result"], "failed")


class CancelTests(ScheduleCase):
    def test_cancel_removes_the_cron_file_and_the_record(self):
        self.schedule()
        self.assertTrue(stop_schedule.cancel(self.store, NAME))
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())
        self.assertEqual(self.log()[-1]["result"], "cancelled")

    def test_cancel_with_nothing_scheduled_changes_nothing(self):
        self.assertFalse(stop_schedule.cancel(self.store, NAME))
        self.assertFalse(stop_schedule.log_path(self.store, NAME).exists())

    def test_only_one_caller_removes_the_cron_file(self):
        self.schedule()
        self.assertEqual([stop_schedule.remove_cron(NAME) for _ in range(2)], [True, False])

    def test_a_cron_file_evernode_did_not_write_is_refused(self):
        self.cron_file.write_text("* * * * * root /bin/true\n")
        with self.assertRaises(process.OperationError):
            stop_schedule.remove_cron(NAME)
        self.assertTrue(self.cron_file.exists())
        self.assertFalse(stop_schedule.is_scheduled(NAME))

    def test_a_symlinked_cron_file_is_refused(self):
        target = self.cron / "elsewhere"
        target.write_text(stop_schedule.CRON_HEADER)
        self.cron_file.symlink_to(target)
        with self.assertRaises(process.OperationError):
            stop_schedule.remove_cron(NAME)
        self.assertFalse(stop_schedule.is_scheduled(NAME))


class NodeStopTests(ScheduleCase):
    """NODE-6: any stop of the node ends its scheduled stop."""

    def stop(self):
        errors = io.StringIO()
        with (
            patch("evernode.nodes.docker.inspect_managed_container", return_value=None),
            patch("evernode.nodes.elections.disable_node_election_schedule"),
            contextlib.redirect_stderr(errors),
        ):
            nodes.stop_node(make_node_configuration(NAME), disable_elections=False)
        return errors.getvalue()

    def test_a_stop_removes_the_scheduled_stop_and_says_so(self):
        self.schedule()
        self.assertIn("is removed", self.stop())
        self.assertFalse(self.cron_file.exists())

    def test_a_stop_with_nothing_scheduled_says_nothing(self):
        self.assertEqual(self.stop(), "")

    def test_a_stop_refuses_before_it_stops_anything_when_the_cron_file_is_not_evernodes(self):
        self.cron_file.write_text("* * * * * root /bin/true\n")
        with (
            patch("evernode.nodes.docker.inspect_managed_container") as inspect,
            patch("evernode.nodes.process.execute_command") as execute,
        ):
            inspect.return_value = {"State": {"Status": "running"}}
            with self.assertRaises(process.OperationError):
                nodes.stop_node(make_node_configuration(NAME), disable_elections=False)
        execute.assert_not_called()


class WatchTests(ScheduleCase):
    def watch(self, steps, **options):
        """Watch while each step in turn happens during a sleep; returns the lines and the result."""
        lines = []
        steps = iter(steps)
        result = stop_schedule.watch(
            self.store, NAME, sleep=lambda _: next(steps)(), out=lines.append, **options
        )
        return [json.loads(line) for line in lines], result

    def test_lines_are_shown_as_they_come_until_the_stop_has_ended(self):
        offset = self.schedule()
        lines, result = self.watch(
            [
                lambda: stop_schedule._log(self.store, NAME, masterchain_block=TARGET - 50),
                lambda: None,
                lambda: stop_schedule.cancel(self.store, NAME),
            ],
            offset=offset,
        )
        self.assertEqual(result, "cancelled")
        self.assertEqual(
            [line.get("result") for line in lines], ["scheduled", None, "cancelled"]
        )

    def test_a_check_that_is_stopping_the_node_is_waited_for(self):
        offset = self.schedule()
        with contextlib.ExitStack() as working:

            def claim():
                working.enter_context(stop_schedule._hold_check_lock(self.store, NAME))
                stop_schedule.remove_cron(NAME)

            def finish():
                stop_schedule._log(self.store, NAME, result="stopped")
                working.close()

            _, result = self.watch([claim, lambda: None, finish], offset=offset)
        self.assertEqual(result, "stopped")

    def test_without_an_offset_the_latest_line_comes_first(self):
        self.schedule()
        stop_schedule._log(self.store, NAME, masterchain_block=TARGET - 50)
        lines, result = self.watch([lambda: stop_schedule.cancel(self.store, NAME)])
        self.assertEqual([line.get("masterchain_block") for line in lines], [TARGET - 50, None])
        self.assertEqual(result, "cancelled")

    def test_with_nothing_scheduled_it_ends_at_once_with_the_last_result(self):
        self.assertEqual(self.watch([]), ([], None))
        self.schedule()
        self.check([reading(TARGET)])
        lines, result = self.watch([])
        self.assertEqual((len(lines), result), (1, "stopped"))

    def test_a_line_that_is_not_json_is_shown_and_is_no_result(self):
        stop_schedule.log_path(self.store, NAME).write_text("Error: something from cron\n")
        lines = []
        self.assertIsNone(stop_schedule.watch(self.store, NAME, out=lines.append))
        self.assertEqual(lines, ["Error: something from cron"])


class CommandLineTests(ScheduleCase):
    def run_cli(self, *arguments_, statistics=None, watch="stopped"):
        """evernode node stop ... against the temporary node; returns (exit code, what it calls)."""
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore", return_value=self.store),
            patch("evernode.nodes.fetch_node_statistics", return_value=statistics or reading(TARGET - 100)),
            patch("evernode.cli.nodes.apply_node_lifecycle_operation") as lifecycle,
            patch("evernode.cli.stop_schedule.watch", side_effect=[watch]) as watching,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            code = cli.main(["node", "stop", *arguments_])
        return code, lifecycle, watching, errors.getvalue()

    def test_after_block_schedules_the_stop_and_watches_it(self):
        code, lifecycle, watching, _ = self.run_cli("-n", NAME, "--after-block", str(TARGET), "--timeout", "90")
        self.assertEqual(code, 0)
        lifecycle.assert_not_called()
        self.assertEqual(json.loads(self.record.read_text())["timeout"], 90)
        self.assertTrue(self.cron_file.exists())
        watching.assert_called_once_with(self.store, NAME, offset=0)

    def test_the_exit_code_says_whether_the_node_was_stopped(self):
        for result, expected in (("stopped", 0), ("failed", 1), ("cancelled", 1), (None, 1)):
            with self.subTest(result=result):
                self.assertEqual(self.run_cli("-n", NAME, "--watch", watch=result)[0], expected)

    def test_ctrl_c_ends_the_display_and_leaves_the_stop_scheduled(self):
        self.schedule()
        code, _, _, errors = self.run_cli("-n", NAME, "--watch", watch=KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertIn("stays scheduled", errors)
        self.assertTrue(self.cron_file.exists())

    def test_a_dry_run_installs_nothing(self):
        code, _, watching, _ = self.run_cli("-n", NAME, "--after-block", str(TARGET), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(self.cron_file.exists())
        self.assertFalse(self.record.exists())
        watching.assert_not_called()

    def test_the_refusals_of_the_ordinary_stop_come_when_scheduling_unless_forced(self):
        member = {**reading(TARGET - 100), "in_next_vset_p36": True}
        code, _, _, errors = self.run_cli("-n", NAME, "--after-block", str(TARGET), statistics=member)
        self.assertEqual(code, 1)
        self.assertIn("next validator set", errors)
        self.assertFalse(self.cron_file.exists())
        code, *_ = self.run_cli("-n", NAME, "--after-block", str(TARGET), "--force", statistics=member)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(self.record.read_text())["force"])

    def test_a_node_already_at_the_block_is_refused_even_with_force(self):
        code, lifecycle, _, errors = self.run_cli(
            "-n", NAME, "--after-block", str(TARGET), "--force", statistics=reading(TARGET)
        )
        self.assertEqual(code, 1)
        self.assertIn("already applied", errors)
        lifecycle.assert_not_called()

    def test_a_block_below_one_and_all_nodes_are_refused_and_stop_nothing(self):
        for arguments_ in (("-n", NAME, "--after-block", "0"), ("--all", "--after-block", "5")):
            with self.subTest(arguments=arguments_):
                code, lifecycle, _, _ = self.run_cli(*arguments_)
                self.assertEqual(code, 1)
                lifecycle.assert_not_called()
                self.assertFalse(self.cron_file.exists())

    def test_cancel_removes_the_stop_and_is_no_error_when_there_is_none(self):
        self.schedule()
        for _ in range(2):
            code, lifecycle, _, _ = self.run_cli("-n", NAME, "--cancel")
            self.assertEqual(code, 0)
            lifecycle.assert_not_called()
            self.assertFalse(self.cron_file.exists())

    def test_cancel_with_a_dry_run_changes_nothing(self):
        self.schedule()
        self.assertEqual(self.run_cli("-n", NAME, "--cancel", "--dry-run")[0], 0)
        self.assertTrue(self.cron_file.exists())

    def test_crons_check_stops_the_node_the_ordinary_way(self):
        self.schedule(timeout=45)
        # The node is ready at the first reading, so only the settle time would sleep.
        with patch("evernode.stop_schedule.SETTLE_SECONDS", 0):
            code, lifecycle, _, _ = self.run_cli("-n", NAME, "--check", statistics=reading(TARGET))
        self.assertEqual(code, 0)
        lifecycle.assert_called_once_with(self.store, NAME, "stop", timeout=45, force=False)
        self.assertEqual(self.log()[-1]["result"], "stopped")

    def test_a_plain_stop_is_the_ordinary_stop(self):
        code, lifecycle, watching, _ = self.run_cli("-n", NAME, "--timeout", "40")
        self.assertEqual(code, 0)
        lifecycle.assert_called_once_with(
            self.store, NAME, "stop", timeout=40, purge_data=False, force=False
        )
        watching.assert_not_called()

    def test_two_of_the_flags_together_and_other_commands_are_refused_by_the_parser(self):
        parser = arguments.build_argument_parser()
        for command in (
            ["node", "stop", "--after-block", "5", "--watch"],
            ["node", "stop", "--watch", "--cancel"],
            ["node", "restart", "--after-block", "5"],
            ["node", "start", "--watch"],
        ):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(command)


if __name__ == "__main__":
    unittest.main()
