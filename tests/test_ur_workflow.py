import os
from pathlib import Path
import shlex
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_agent.ur_workflow import URWorkflow, training_plan


def test_site_command_preserves_all_hydra_overrides():
    plan = training_plan({})
    overrides = plan['overrides']
    assert '--config-name=train_gello_live_image_20hz' in plan['command']
    assert '~shape_meta.obs.tactile' in overrides
    assert 'task.dataset.obs_keys=[camera_0,camera_1,camera_2,robot_joint,gripper_position]' in overrides
    assert 'training.num_epochs=60' in overrides
    assert 'dataloader.batch_size=8' in overrides
    assert 'training.resume=false' in overrides
    assert 'checkpoint.topk.monitor_key=train_loss' in overrides
    assert '${now:%Y.%m.%d}/${now:%H.%M.%S}' in plan['command']


def test_paths_with_shell_metacharacters_stay_single_arguments():
    dataset = '/data/my run/$(touch should-not-run).zarr'
    plan = training_plan({'dataset': dataset, 'epochs': 7, 'lr': 0.0002})
    command = plan['command'].replace('\\\n', '')
    argv = shlex.split(command.splitlines()[2])
    assert 'dataset_path="' + dataset + '"' in argv
    assert 'optimizer.lr=0.0002' in argv
    assert 'training.num_epochs=7' in argv


@pytest.mark.parametrize('values', [
    {'epochs': 1.5}, {'epochs': True}, {'lr': float('nan')},
    {'crop': [500, 500]}, {'resize': [0, 320]}, {'dataset': 'relative.zarr'},
    {'conda_env': 'robodiff; echo bad'}, {'random_crop': 'false'},
])
def test_invalid_training_parameters_are_rejected(values):
    with pytest.raises(ValueError):
        training_plan(values)


def workflow(monkeypatch):
    from edge_agent.capture_observer import CaptureObserver
    core = SimpleNamespace(lock=threading.RLock(), jobs={})
    runner = URWorkflow(core)
    runner.capture = dict(id='test-session', running=True, controllable=True, aligned=False,
                          startup_confirmed=True, phase='waiting_align', align_prompt=False, request_ids={})
    runner.capture_process = SimpleNamespace(poll=lambda: None)
    runner.capture_fd = 99
    runner.capture_observer = CaptureObserver(runner.capture)
    sent = []
    monkeypatch.setattr(os, 'write', lambda fd, data: sent.append((fd, data)))
    return runner, sent


def test_align_requires_script_evidence_and_operator_ack_before_keys(monkeypatch):
    runner, sent = workflow(monkeypatch)
    key = lambda action, confirmed=False: runner.send_key(action, 'test-session', confirmed, request_id='request-' + str(len(runner.capture['request_ids'])))
    with pytest.raises(ValueError): key('align', True)
    runner.capture['align_prompt'] = True
    key('align', True)
    with pytest.raises(ValueError): key('ready', True)
    runner.capture_observer.event({'event':'alignment_completed'})
    with pytest.raises(ValueError): key('start')
    key('ready', True)
    key('start')
    assert runner.capture['phase'] == 'awaiting_receipt'
    runner.capture_observer.event({'event':'recording_started', 'episode_id':1})
    key('finish')
    runner.capture_observer.event({'event':'episode_finished','episode_id':1,'queued':True})
    key('next')
    runner.capture_observer.event({'event':'recording_started','episode_id':2})
    key('finish')
    assert [v for _, v in sent] == [b'ALIGN\n', b's\n', b'\n', b's\n', b'\n']


def test_delete_requires_confirmation_and_rejects_stale_session(monkeypatch):
    runner, sent = workflow(monkeypatch)
    runner.capture.update(aligned=True, phase='recording')
    with pytest.raises(ValueError): runner.send_key('discard','test-session',request_id='delete-request')
    with pytest.raises(ValueError): runner.send_key('discard','old-session',True,request_id='delete-request')
    assert not sent
    runner.send_key('discard','test-session',True,request_id='delete-request')
    runner.send_key('discard','test-session',True,request_id='delete-request')
    assert sent == [(99, b'd\n')]


def test_missing_site_environment_never_starts_training():
    core = SimpleNamespace(lock=threading.RLock(), jobs={}, capture=None)
    runner = URWorkflow(core)
    with pytest.raises(ValueError, match='未就绪'):
        runner.start_training({'project': '/path/that/does/not/exist'})
    assert not core.jobs
