#!/usr/bin/env python3
"""Executable D5 oracle defect: state publication follows a late parent swap.

Runs only disposable ActivationTests fixtures. A passing reproduction means the
legacy oracle overwrote foreign outside bytes, NOT that D5 is complete.
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from test_activation import ActivationTests
from agent_workbench import installation


def run():
    assert os.environ.get('TMPDIR'), 'set the profile scratch TMPDIR explicitly'
    fixture = ActivationTests()
    fixture.setUp()
    try:
        plan = fixture.trusted()
        detached = fixture.base / 'detached-state'
        outside = fixture.base / 'outside'
        outside.mkdir()
        foreign = outside / plan.state_file.name
        foreign.write_bytes(b'foreign outside state\n')
        foreign.chmod(0o640)
        original_replace = os.replace
        hit = False
        expected = None
        stage_name = None
        def swapped_replace(source, destination, *args, **kwargs):
            nonlocal hit, expected, stage_name
            if Path(destination) == plan.state_file and not hit:
                hit = True
                expected = Path(source).read_bytes()
                stage_name = Path(source).name
                plan.state_file.parent.rename(detached)
                plan.state_file.parent.symlink_to(outside, target_is_directory=True)
                # Adversary supplies the exact temporary basename in the new
                # parent. Publication must not follow this replacement parent.
                (outside / Path(source).name).write_bytes(expected)
            return original_replace(source, destination, *args, **kwargs)
        error = None
        try:
            with patch.object(os, 'replace', swapped_replace):
                installation.apply_plan(plan)
        except (OSError, ValueError) as exc:
            error = (type(exc).__name__, str(exc))
        assert hit, 'state publication seam was not reached'
        assert foreign.read_bytes() == expected, 'oracle defect no longer reproduces'
        assert foreign.read_bytes() != b'foreign outside state\n'
        assert stage_name is not None
        assert (detached / stage_name).read_bytes() == expected
        assert (fixture.project / '.cursor' / '.agent-workbench-transaction.json').exists()
        print('ORACLE DEFECT REPRODUCED: foreign outside state bytes overwritten')
        print('outside mode:', oct(foreign.stat().st_mode & 0o7777))
        print('detached staged bytes retained:', len(expected))
        print('authenticated transaction journal retained: True')
        print('apply error:', error)
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    run()
