import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_agent.capture_observer import CaptureObserver, ReadOnlyReceipts, allowed_actions
from edge_agent.ur_workflow import URWorkflow
from edge_agent.capture_session import capture_plan


def session():
    return dict(id='UR-test', running=True, controllable=True, phase='initializing', aligned=False,
                startup_confirmed=False, command_pending=None, request_ids={})


def runner(monkeypatch, state=None):
    core = SimpleNamespace(lock=threading.RLock(), jobs={}, capture=None)
    workflow = URWorkflow(core)
    workflow.capture = state or session()
    workflow.capture_process = SimpleNamespace(poll=lambda: None, pid=12345)
    workflow.capture_fd = 99
    workflow.capture_observer = CaptureObserver(workflow.capture)
    writes = []
    monkeypatch.setattr(os, 'write', lambda fd, value: writes.append(value))
    return workflow, writes


def test_fragmented_unterminated_prompts_ignore_banner_and_retry_alignment(monkeypatch):
    workflow, writes = runner(monkeypatch)
    observer = workflow.capture_observer
    observer.terminal('Instructions: use ALIGN after initialization.\n')
    assert allowed_actions(workflow.capture) == []
    observer.terminal('Press Ent')
    assert allowed_actions(workflow.capture) == []
    observer.terminal('er to continue: ')
    assert allowed_actions(workflow.capture) == ['startup']
    workflow.send_key('startup', 'UR-test', True, 'startup-first')
    observer.terminal('device initialization\nType AL')
    assert not workflow.capture.get('align_prompt')
    observer.terminal('IGN to confirm alignment: ')
    assert allowed_actions(workflow.capture) == ['align']
    workflow.send_key('align', 'UR-test', True, 'alignment-first')
    observer.event({'event':'alignment_confirmation_rejected','attempt':1})
    assert allowed_actions(workflow.capture) == []
    observer.terminal('\nType ALIGN to confirm alignment: ')
    assert allowed_actions(workflow.capture) == ['align']
    workflow.send_key('align', 'UR-test', True, 'alignment-second')
    assert writes == [b'\n', b'ALIGN\n', b'ALIGN\n']


def test_multiple_prompts_in_same_chunk_are_processed_in_order():
    s = session(); s['startup_confirmed'] = True
    observer = CaptureObserver(s)
    observer.terminal('Press Enter to continue: \nType ALIGN to confirm alignment: ')
    assert s['phase'] == 'waiting_align' and allowed_actions(s) == ['align']


def test_rejected_alignment_and_reprompt_in_one_read_remains_actionable():
    s=session(); s.update(startup_confirmed=True, alignment_request_sent=True, phase='aligning')
    observer=CaptureObserver(s)
    observer.terminal('{"event":"alignment_confirmation_rejected","attempt":1}\nType ALIGN to confirm alignment: ')
    assert s['phase']=='waiting_align' and allowed_actions(s)==['align']


def test_unknown_prompt_is_not_automatically_accepted():
    s = session(); observer = CaptureObserver(s)
    observer.terminal('ALIGN means physically aligning each joint.\nEnter prepares things\n')
    assert allowed_actions(s) == []
    observer = CaptureObserver(s, {'startup':'设备检查完毕，请确认继续 > '})
    observer.terminal('设备检查完毕，请确认继续 > ')
    assert allowed_actions(s) == ['startup']


def test_alignment_event_needs_operator_ready_and_commands_need_receipts(monkeypatch):
    workflow, writes = runner(monkeypatch)
    s = workflow.capture; o = workflow.capture_observer
    s.update(startup_confirmed=True, phase='waiting_align', align_prompt=True)
    workflow.send_key('align','UR-test',True,'alignment-request')
    with pytest.raises(ValueError): workflow.send_key('ready','UR-test',True,'premature-ready')
    o.event({'event':'alignment_completed'})
    assert not s['aligned']
    workflow.send_key('ready','UR-test',True,'operator-ready')
    workflow.send_key('start','UR-test',False,'recording-one')
    assert s['phase'] == 'awaiting_receipt'
    with pytest.raises(ValueError): workflow.send_key('finish','UR-test',False,'premature-finish')
    o.event({'event':'recording_started','episode_id':1})
    workflow.send_key('finish','UR-test',False,'save-request-one')
    o.event({'event':'episode_finished','episode_id':1,'queued':True,'reason':'operator_save'})
    assert s['save_state'] == 'queued' and s['phase'] == 'ready'
    workflow.send_key('next','UR-test',False,'recording-two')
    o.event({'event':'recording_started','episode_id':2})
    assert writes == [b'ALIGN\n', b's\n', b'\n', b's\n']


def test_delete_last_is_idle_confirmed_idempotent_and_session_scoped(monkeypatch):
    s = session(); s.update(phase='ready', aligned=True, in_main_loop=True)
    workflow, writes = runner(monkeypatch, s)
    with pytest.raises(ValueError): workflow.send_key('delete_last','UR-test',False,'delete-last-one')
    with pytest.raises(ValueError): workflow.send_key('delete_last','UR-old',True,'delete-last-one')
    workflow.send_key('delete_last','UR-test',True,'delete-last-one')
    workflow.send_key('delete_last','UR-test',True,'delete-last-one')
    with pytest.raises(ValueError): workflow.send_key('delete_last','UR-test',True,'delete-last-two')
    assert writes == [b'u\n']
    workflow.capture_observer.event({'event':'episode_deleted','operation':'delete_last','remaining':2})
    assert s['phase'] == 'ready'


def test_quit_waits_for_exit_and_is_different_from_signals(monkeypatch):
    s = session(); s.update(phase='recording', aligned=True, in_main_loop=True)
    workflow, writes = runner(monkeypatch,s)
    signals=[]
    monkeypatch.setattr(os,'killpg',lambda pid,sig:signals.append(sig),raising=False)
    workflow.send_key('quit','UR-test',True,'graceful-quit')
    assert s['phase'] == 'exiting' and s['running'] and writes == [b'q\n']
    workflow.capture_observer.event({'event':'episode_finished','queued':True,'rows':200})
    assert s['phase'] == 'exiting' and s['save_state'] == 'queued'
    workflow.stop_capture('UR-test','interrupt',True,'interrupt-request')
    workflow.stop_capture('UR-test','interrupt',True,'interrupt-request')
    assert signals == [signal.SIGINT]
    assert '强制' in s['message']


@pytest.mark.parametrize('reason',['too_short','timing_quality','observation_gap','sensor_fault','operator_discard'])
def test_rejected_or_incomplete_episode_never_means_saved(reason):
    s=session(); s.update(aligned=True,phase='recording')
    CaptureObserver(s).event({'event':'episode_finished','queued':False,'reason':reason})
    assert s['save_state'] == 'not_saved' and s['phase'] == 'ready'


def test_final_result_is_pending_audit_and_does_not_claim_quality():
    s=session(); observer=CaptureObserver(s)
    observer.event({'event':'finished','result':dict(status='software_complete_pending_review',writer_stopped=True,saved_rows=20,saved_episodes=2,dataset='/allowed/run/replay_buffer.zarr')})
    assert s['save_state'] == 'writer_stopped_pending_audit'
    assert s['dataset_path'] == '/allowed/run/replay_buffer.zarr'
    assert s['running']  # only process observation can confirm exit


def test_readonly_receipts_paths_partial_lines_and_result(tmp_path):
    run=tmp_path/'actual-output'; run.mkdir()
    s=session(); s['output_dir']=str(run); s['alignment_request_sent']=True
    observer=CaptureObserver(s); reader=ReadOnlyReceipts(observer,[tmp_path])
    file=run/'capture.jsonl'
    file.write_text('{"event":"alignment_',encoding='utf-8')
    reader.poll(); assert not s.get('alignment_observed')
    with file.open('a',encoding='utf-8') as stream:stream.write('completed"}\n')
    reader.poll(); assert s['alignment_observed']
    result=run/'result.json'; data=dict(writer_stopped=True,saved_rows=2,saved_episodes=1,dataset=str(run/'manual_task/replay_buffer.zarr'))
    result.write_text(json.dumps(data),encoding='utf-8')
    before={p.name:p.read_bytes() for p in run.iterdir() if p.is_file()}
    reader.poll(); reader.poll()
    assert s['save_state']=='writer_stopped_pending_audit'
    assert before=={p.name:p.read_bytes() for p in run.iterdir() if p.is_file()}
    s['output_dir']=str(tmp_path.parent/'escape')
    with pytest.raises(ValueError):reader.poll()


def test_capture_mode_argv_and_resume_use_original_flags(tmp_path):
    script=tmp_path/'experiments/start_live_follow_local.sh'; script.parent.mkdir(); script.write_text('#!/bin/bash\n')
    both=capture_plan(str(script))
    rgb=capture_plan(str(script),'rgb')
    assert both['argv']==['bash',str(script.resolve())]
    assert rgb['argv']==['bash',str(script.resolve()),'--no-tactile']
    dataset=tmp_path/'my data; $(touch no).zarr'; dataset.mkdir()
    assets=SimpleNamespace(check_resume=lambda p,m:Path(p).resolve())
    plan=capture_plan(str(script),'rgb',str(dataset),assets)
    assert plan['argv'][-2:]==['--resume',str(dataset.resolve())]
    assert plan['parameters']['capture_mode']=='rgb_only'


def test_service_close_does_not_signal_capture(monkeypatch,tmp_path):
    core=SimpleNamespace(data_root=tmp_path,lock=threading.RLock(),jobs={})
    workflow=URWorkflow(core); workflow.capture=session()
    workflow.capture['pid']=123
    signals=[]
    workflow.capture_process=SimpleNamespace(poll=lambda:None,pid=123)
    monkeypatch.setattr(os,'killpg',lambda *args:signals.append(args),raising=False)
    workflow.close()
    assert not signals and workflow.capture['phase']=='uncontrolled'
    restored=URWorkflow(core)
    assert restored.capture['controllable'] is False and restored.status()['capture']['allowed_actions']==[]


def test_missing_request_id_cannot_send_a_key(monkeypatch):
    workflow,writes=runner(monkeypatch)
    with pytest.raises(ValueError):workflow.send_key('start','UR-test')
    assert not writes


def test_resume_old_log_prefix_cannot_complete_this_alignment(tmp_path):
    run=tmp_path/'resume'; run.mkdir()
    source=run/'capture.jsonl'
    old=json.dumps({'event':'alignment_completed'})+'\n'
    source.write_text(old,encoding='utf-8')
    s=session(); s.update(output_dir=str(run), alignment_request_sent=True,
                         prior_receipt_sizes={str(source.resolve()):source.stat().st_size})
    reader=ReadOnlyReceipts(CaptureObserver(s),[tmp_path])
    reader.poll()
    assert not s.get('alignment_observed')
    with source.open('a',encoding='utf-8') as out:out.write(json.dumps({'event':'alignment_completed','attempt':2})+'\n')
    reader.poll(); assert s['alignment_observed']


@pytest.mark.skipif(os.name != 'posix', reason='The collector uses Linux PTY; unit parser/control tests run on Windows too')
def test_platform_fake_collector_full_pty_flow(tmp_path):
    import shlex
    fixture=Path(__file__).parent/'fixtures/fake_live_capture.py'
    script=tmp_path/'project/experiments/start_live_follow_local.sh'
    script.parent.mkdir(parents=True)
    output=tmp_path/'external/actual-run'
    script.write_text('#!/bin/bash\nexec '+shlex.quote(sys.executable)+' -u '+shlex.quote(str(fixture))+' '+shlex.quote(str(output))+'\n')
    assets=SimpleNamespace(roots=(tmp_path/'external',), register=lambda p,session:None)
    core=SimpleNamespace(data_root=tmp_path/'platform',assets=assets,lock=threading.RLock(),jobs={},capture=None)
    workflow=URWorkflow(core)
    def wait_for(action):
        deadline=time.time()+5
        while time.time()<deadline:
            if action in workflow.status()['capture']['allowed_actions']:return
            time.sleep(.02)
        raise AssertionError(workflow.status()['capture'])
    started=workflow.start_capture(str(script),request_id='start-pty-run',confirmed_external_idle=True)
    key=lambda action,req:workflow.send_key(action,started['id'],True,req)
    try:
        wait_for('startup'); key('startup','startup-pty-run')
        wait_for('align'); key('align','align-pty-run')
        wait_for('ready'); key('ready','ready-pty-run')
        key('start','start-episode-one'); wait_for('finish'); key('finish','save-episode-one')
        wait_for('start'); key('next','start-episode-two'); wait_for('finish'); key('finish','save-episode-two')
        wait_for('delete_last'); key('delete_last','delete-last-run'); key('delete_last','delete-last-run')
        wait_for('start'); key('quit','graceful-exit-run')
        deadline=time.time()+5
        while workflow.capture.get('running') and time.time()<deadline:time.sleep(.02)
        assert workflow.capture['phase']=='exited'
        assert workflow.capture['save_state']=='writer_stopped_pending_audit'
        keys=[json.loads(l) for l in (output/'received-keys.jsonl').read_text().splitlines()]
        assert keys==['','ALIGN','s','','s','','u','q']
    finally:
        if workflow.capture_process.poll() is None:
            os.killpg(workflow.capture_process.pid,signal.SIGTERM)
            workflow.capture_process.wait(timeout=3)
