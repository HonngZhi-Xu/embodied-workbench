"""Platform-owned interactive collector double; never opens a robot or sensor."""
import json
from pathlib import Path
import sys
import time

root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
keys = root / 'received-keys.jsonl'
log = (root / 'capture.jsonl').open('w', encoding='utf-8', buffering=1)
def event(name, **data):
    value = dict(event=name, **data)
    text = json.dumps(value)
    log.write(text + '\n')
    print(text, flush=True)
def read():
    text = sys.stdin.readline().rstrip('\n\r')
    with keys.open('a', encoding='utf-8') as file:
        file.write(json.dumps(text) + '\n')
    return text

print('Help: ALIGN is required later; this banner is not a prompt.', flush=True)
if len(sys.argv) > 2 and sys.argv[2] == 'live-r3':
    sys.stdout.write('确认已单独同意本次40N/18N夹爪参数、现场已准备好，按Ent')
    sys.stdout.flush(); time.sleep(.08)
    sys.stdout.write('er启动（其他输入取消）：'); sys.stdout.flush()
else:
    sys.stdout.write('Press Ent'); sys.stdout.flush(); time.sleep(.08)
    sys.stdout.write('er to continue: '); sys.stdout.flush()
assert read() == ''
print('\nOutput directory: ' + str(root), flush=True)
print('Type ALIGN to confirm alignment: ', end='', flush=True)
assert read() == 'ALIGN'
event('alignment_completed')
episode = 0
while True:
    key = read()
    if key == 's':
        episode += 1
        event('recording_started', episode_id=episode)
    elif key == '':
        event('episode_finished', episode_id=episode, queued=True, rows=100, reason='operator_save')
    elif key == 'd':
        event('episode_finished', episode_id=episode, queued=False, rows=0, reason='operator_discard')
    elif key == 'u':
        event('episode_deleted', operation='delete_last', remaining=episode-1)
    elif key == 'q':
        result = dict(writer_stopped=True, saved_rows=100, saved_episodes=1, status='software_complete_pending_review')
        (root/'result.json').write_text(json.dumps(result), encoding='utf-8')
        event('finished', result=result)
        log.close()
        break
